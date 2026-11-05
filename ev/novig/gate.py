"""
ev.novig.gate -- the optional restriction layer ev.novig.exec.run_pass consults for each Novig PLAN.

Default OFF (NOVIG_GATE_ENABLED unset): load_exec_gate() returns None and run_pass is exactly what it was.
When ON it can only ADD restrictions to the existing shadow / paper path:

  1. Liability caps (ev.novig.liability): a PLAN that breaches the per-bet / per-player / per-game caps is shrunk to fit
     or turned into REJ_LIABILITY_*.  Applied to the shadow fill as well, so the shadow ledger never records exposure the
     caps would not allow a live order to carry.
  2. Forward-CLV slice gate (ev.novig.clv_gate): every decision is ANNOTATED with `inputs.exec_gate` =
     {slice, open, status, live_eligible}.  A slice that is not open is NOT live-eligible; with `skip_closed_paper`
     (default on) its plan is also not mirrored to the Novig PAPER sandbox.  The production-priced SHADOW fill is never
     blocked by the CLV gate: it is the only source of the forward CLV the gate needs, so gating it would deadlock every
     slice at n=0.

There is no order client here and no live-routing code: `live_eligible` is an annotation for a future, separately reviewed
executor.  Nothing in this module can place, route or size a real-money order.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from ev.novig.clv_gate import GateConfig, SliceGate, load_gate_config
from ev.novig.liability import LiabilityConfig, check_liability, ledger_exposures, load_liability_config


@dataclass
class GateReview:
    decision: object                       # the (possibly shrunk / rejected) gates.Decision
    skip_paper: bool = False
    note: dict | None = None


class NovigExecGate:
    def __init__(self, cfg: GateConfig, liab: LiabilityConfig, slices: SliceGate):
        self.cfg, self.liab, self.slices = cfg, liab, slices
        self.counts = {"closed_annotated": 0, "paper_skipped_closed": 0, "liability_shrunk": 0, "liability_rejected": 0}

    def summary(self) -> dict:
        return dict(self.counts, verdict_stale=self.slices.stale)

    def review(self, *, ledger, day: str, mode: str, leg, d) -> GateReview:
        """`d` is a gates.Decision whose outcome is 'PLAN' (every existing gate and the RiskGate already passed)."""
        liab_note = None
        res = check_liability(self.liab, contracts=d.plan.contracts, price=d.plan.limit_price, game_key=leg.game_key,
                              player_norm=leg.player_norm, exposures=ledger_exposures(ledger, day, mode),
                              fee_charged=leg.fee_charged)
        if not res.allowed:
            d.outcome, d.reason = res.reason, f"liability cap ({res.binding})"
            self.counts["liability_rejected"] += 1
            liab_note = {"allowed": False, "binding": res.binding}
        elif res.contracts < d.plan.contracts:
            d.plan = replace(d.plan, contracts=res.contracts, stake=res.contracts * d.plan.cost_per_contract)
            self.counts["liability_shrunk"] += 1
            liab_note = {"allowed": True, "shrunk_to": res.contracts, "binding": res.binding}
        st = self.slices.status(leg.game_key.split(":")[0], leg.stat)
        note = {"slice": st["slice"], "open": st["open"], "status": st["status"],
                "live_eligible": bool(st["open"]) and res.allowed, "liability": liab_note}
        d.inputs["exec_gate"] = note
        skip = False
        if not st["open"]:
            self.counts["closed_annotated"] += 1
            skip = self.cfg.skip_closed_paper
            if skip and d.outcome == "PLAN":
                self.counts["paper_skipped_closed"] += 1
        return GateReview(d, skip, note)


def load_exec_gate(env=None, now: datetime | None = None) -> NovigExecGate | None:
    """None unless NOVIG_GATE_ENABLED is on.  The verdict file is read once, at construction (fail closed)."""
    cfg = load_gate_config(env)
    if not cfg.enabled:
        return None
    return NovigExecGate(cfg, load_liability_config(env), SliceGate.load(cfg, now))
