"""
ev.ownbettor.risk -- the risk gate: kill switch, daily caps, daily loss stop, error breaker.

Same doctrine as mlb.execution.kalshi.risk: every order passes check() (no code path
may skip it), and the kill switch is MANUAL-RESET-ONLY -- nothing auto-resumes after a
trip.  State persists in the audit dir, so a trip survives a crash/restart.

Trips happen on:
  * realized P&L for the ET day at or past the daily loss stop (checked before every
    order, fed by settlement rows in the audit log),
  * a venue AUTH failure (immediate, first occurrence),
  * max_consecutive_errors venue errors in a row.
Caps reject (without tripping) on: daily placed-order count, daily filled spend.
"""

from __future__ import annotations

from ev.ownbettor.audit import AuditLog, et_today
from ev.ownbettor.config import BettorConfig


class RiskGate:
    def __init__(self, cfg: BettorConfig, audit: AuditLog):
        self.cfg, self.audit = cfg, audit

    # ---- kill switch ----
    @property
    def killed(self) -> bool:
        return self.audit.kill is not None

    @property
    def kill_reason(self) -> str | None:
        k = self.audit.kill
        return k.get("reason") if k else None

    def trip(self, reason: str) -> None:
        if not self.killed:
            self.audit.set_kill(reason)
            self.audit.append("kill_tripped", et_today(), reason=reason)

    def reset(self) -> None:
        """Manual only.  Called from the CLI, never from the trading loop."""
        self.audit.clear_kill()
        self.audit.append("kill_reset", et_today(), reason="manual reset via CLI")

    # ---- pre-order check ----
    def check(self, day: str, stake: float) -> tuple[bool, str]:
        """-> (allowed, REJ_* reason).  stake = the planned stake for this order."""
        if self.killed:
            return False, "REJ_KILLED"
        pnl = self.audit.realized_pnl_today(day)
        if pnl <= -self.cfg.daily_loss_stop_usd:
            self.trip(f"daily loss stop: realized pnl {pnl:.2f} <= -{self.cfg.daily_loss_stop_usd:.2f}")
            return False, "REJ_LOSS_STOP"
        if self.audit.orders_placed(day) >= self.cfg.daily_bet_cap:
            return False, "REJ_DAILY_BET_CAP"
        if self.audit.spent_today(day) + stake > self.cfg.daily_spend_cap_usd + 1e-9:
            return False, "REJ_DAILY_SPEND_CAP"
        return True, ""

    def check_loss_stop(self, day: str) -> bool:
        """Trip immediately when the day's realized P&L breaches the stop (called when
        settlements are booked, so the halt does not wait for the next order)."""
        pnl = self.audit.realized_pnl_today(day)
        if pnl <= -self.cfg.daily_loss_stop_usd:
            self.trip(f"daily loss stop: realized pnl {pnl:.2f} <= -{self.cfg.daily_loss_stop_usd:.2f}")
            return True
        return False

    # ---- error breaker ----
    def note_submit_ok(self) -> None:
        if self.audit.consecutive_errors:
            self.audit.set_consecutive_errors(0)

    def note_submit_error(self, *, auth: bool = False) -> None:
        if auth:
            self.trip("venue auth failure")
            return
        n = self.audit.consecutive_errors + 1
        self.audit.set_consecutive_errors(n)
        if n >= self.cfg.max_consecutive_errors:
            self.trip(f"{n} consecutive venue errors")
