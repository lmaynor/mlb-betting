"""
mlb.execution.kalshi.risk -- independent risk gate + kill switch.

Pattern from ~/market-maker's RiskManager: every order passes check_order() (no code path may
skip it) and the kill switch is MANUAL-RESET-ONLY -- nothing auto-resumes after a trip. The kill
state is persisted in the ledger so it survives a crash/restart.

Budget model (dollars of all-in cost):
    remaining = min( per_game_cap - exposure(game_pk),
                     daily_deploy_cap - deployed_today,
                     available_cash )
The planner (sizing.plan_order) further applies the per-bet cap and Kelly.

Daily-loss feed: position_pnl() turns Kalshi's own /portfolio/positions (market_positions) into
realized + mark-to-bid P&L, net of fees, for the tickers THIS executor traded; the runner feeds it
to check_pnl() at the start of every pass.
"""

from __future__ import annotations

from mlb.execution.kalshi.config import ExecConfig
from mlb.execution.kalshi.ledger import Ledger

KILL_KEY = "kill_reason"


def position_pnl(market_positions, tickers, marks) -> tuple[float, float, float]:
    """(realized, unrealized, fees) in dollars for the given tickers.

    market_positions: Kalshi /portfolio/positions -> market_positions rows
        (realized_pnl_dollars EXCLUDES fees; position_fp > 0 = long YES, < 0 = long NO;
         market_exposure_dollars = cost basis of the open position).
    marks: {ticker: (yes_bid, no_bid)} -- the open position is valued at what it could be SOLD for
        (conservative liquidation value); an unmarked open position is carried at cost (0 unrealized).
    """
    realized = unrealized = fees = 0.0
    for p in market_positions or []:
        t = p.get("ticker")
        if t not in tickers:
            continue
        realized += float(p.get("realized_pnl_dollars") or 0.0)
        fees += float(p.get("fees_paid_dollars") or 0.0)
        pos = float(p.get("position_fp") or 0.0)
        if pos != 0.0 and t in marks:
            yes_bid, no_bid = marks[t]
            value = pos * yes_bid if pos > 0 else (-pos) * no_bid
            unrealized += value - float(p.get("market_exposure_dollars") or 0.0)
    return realized, unrealized, fees


class RiskGate:
    def __init__(self, cfg: ExecConfig, ledger: Ledger, mode: str):
        self.cfg, self.ledger, self.mode = cfg, ledger, mode
        self._consecutive_errors = 0

    # ---- kill switch ----
    @property
    def killed(self) -> bool:
        return self.ledger.kv_get(KILL_KEY) is not None

    @property
    def kill_reason(self) -> str | None:
        return self.ledger.kv_get(KILL_KEY)

    def trip(self, reason: str) -> None:
        if not self.killed:
            self.ledger.kv_set(KILL_KEY, reason)

    def reset(self) -> None:
        """Manual only. Called from the CLI, never from the trading loop."""
        self.ledger.kv_del(KILL_KEY)
        self._consecutive_errors = 0

    # ---- budgets / checks ----
    def budget_cap(self, game_pk, day: str, available_cash: float) -> float:
        per_game = self.cfg.per_game_cap - self.ledger.game_exposure(game_pk, day, self.mode)
        daily = self.cfg.daily_deploy_cap - self.ledger.deployed_today(day, self.mode)
        return max(0.0, min(per_game, daily, available_cash))

    def check_order(self, stake: float, game_pk, day: str, available_cash: float):
        """-> (allowed, reason). Reason is a REJ_* code when not allowed."""
        if self.killed:
            return False, "REJ_KILLED"
        if self.ledger.orders_today(day, self.mode) >= self.cfg.max_orders_per_day:
            return False, "REJ_RISK"
        if stake > self.budget_cap(game_pk, day, available_cash) + 1e-9:
            return False, "REJ_RISK"
        return True, ""

    def check_pnl(self, realized_pnl_today: float) -> None:
        if realized_pnl_today < -self.cfg.daily_loss_cap:
            self.trip(f"max daily loss exceeded: pnl {realized_pnl_today:.2f} < -{self.cfg.daily_loss_cap:.2f}")

    # ---- error breaker ----
    def note_submit_ok(self) -> None:
        self._consecutive_errors = 0

    def note_submit_error(self) -> None:
        self._consecutive_errors += 1
        if self._consecutive_errors >= self.cfg.max_consecutive_rejects:
            self.trip(f"{self._consecutive_errors} consecutive submit errors")
