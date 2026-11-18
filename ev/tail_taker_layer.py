"""ev/tail_taker_layer.py -- Module 4: Two-Sided Tail Taker / Layer (RES-12b).

SIGNAL-ONLY: Emits trading signals and decisions only.
NO venue clients, NO order placement, NO imports from any execution module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, List, Literal, Optional, Sequence, Tuple

import numpy as np

from ev.capability_mask import CapabilityDecision
from mlb_core.odds.utils import devig_two_way


def _mad(x: Sequence[float]) -> float:
    """Median Absolute Deviation (MAD) of sequence, matching ev.props._mad."""
    arr = np.asarray(x, dtype=float)
    if len(arr) == 0:
        return 0.0
    return float(np.median(np.abs(arr - np.median(arr))))


@dataclass
class TailQuote:
    """Input quote evaluating a single ladder tail rung."""
    sport: str
    stat: str
    line: float
    venue: str                     # "kalshi" | "novig" | retail book name
    book: str                      # target book being evaluated
    p_model_over: Optional[float]
    p_blend_over: Optional[float]
    decision: CapabilityDecision   # CapabilityDecision for this rung
    ref_quotes: List[Tuple[str, float, float]]  # [(book, p_over_raw, p_under_raw), ...] on EXACT rung
    ask_no: Optional[float] = None              # exchange NO ask as implied prob
    dec_under: Optional[float] = None           # retail Under decimal odds
    q_yes: Optional[float] = None               # implied prob of the YES/Over ask
    d_over: Optional[float] = None              # decimal odds of the Over


@dataclass(frozen=True)
class TailSignal:
    """Output signal emitted when a tail condition triggers."""
    mode: Literal["TAIL_NO_LAY", "TAIL_YES_SMASH"]
    venue: str
    book: str
    sport: str
    stat: str
    line: float
    p_tail_fair_over: float
    q_price: float                 # q_no or q_yes acted on
    fee_drag: float
    edge_pp: float
    roi_or_net_ev: float
    reason_code: str


def tail_fee_drag(venue: str, price: float) -> float:
    """Compute fee drag on taker transactions.

    novig (pre-game): 0.0
    kalshi: 0.07 * price * (1.0 - price) (taker_fee_mult = 1.0)
    retail book: 0.0 (vig already embedded in q_no = 1 / dec_under)
    """
    v = (venue or "").lower().strip()
    if v == "novig":
        return 0.0
    if v == "kalshi":
        p = min(max(float(price), 0.0), 1.0)
        return 0.07 * p * (1.0 - p)
    return 0.0


def scan_two_sided_tails(quotes: Sequence[TailQuote]) -> List[TailSignal]:
    """Scan candidate TailQuotes and emit signals for qualifying tail opportunities.

    Pure evaluation function: SIGNAL-ONLY, no external side-effects.
    """
    signals: List[TailSignal] = []

    for q in quotes:
        routing = q.decision.routing
        target_book = q.book.lower().strip()

        # Leave-target-out reference quotes
        valid_refs = [
            (b, po, pu) for b, po, pu in q.ref_quotes
            if b.lower().strip() != target_book and 0.0 < po < 1.0 and 0.0 < pu < 1.0
        ]

        p_cons_power_over: Optional[float] = None
        ref_fairs: List[float] = []
        if len(valid_refs) >= 3:
            for _, po, pu in valid_refs:
                fair_over, _ = devig_two_way(po, pu, method="log")
                ref_fairs.append(fair_over)
            p_cons_power_over = float(np.mean(ref_fairs))

        p_tail_fair_over: Optional[float] = None
        disp: float = _mad(ref_fairs) if ref_fairs else 0.0

        if routing in ("ROUTE_TO_NO_TAKER", "CONSENSUS_OUTLIER_ONLY"):
            if p_cons_power_over is not None:
                p_tail_fair_over = min(p_cons_power_over, q.decision.p_over_masked)
            else:
                p_tail_fair_over = q.decision.p_over_masked

        elif routing == "OVER_ELIGIBLE":
            # Requires ref-quote path (no smash without corroboration) and dispersion <= 0.06
            if p_cons_power_over is not None and disp <= 0.06:
                p_blend = q.p_blend_over if q.p_blend_over is not None else q.decision.p_over_masked
                p_tail_fair_over = min(p_cons_power_over, p_blend)
            else:
                p_tail_fair_over = None

        if p_tail_fair_over is None:
            continue

        # Branch A: TAIL_NO_LAY
        if p_tail_fair_over <= 0.30 and routing in ("ROUTE_TO_NO_TAKER", "CONSENSUS_OUTLIER_ONLY"):
            q_no: Optional[float] = None
            if q.ask_no is not None:
                q_no = float(q.ask_no)
            elif q.dec_under is not None and q.dec_under > 0:
                q_no = 1.0 / float(q.dec_under)

            if q_no is not None and 0.0 < q_no < 1.0:
                p_fair_no = 1.0 - p_tail_fair_over
                fee = tail_fee_drag(q.venue, q_no)
                edge_pp = p_fair_no - q_no - fee
                roi = edge_pp / q_no

                if edge_pp >= 0.025 and roi >= 0.030:
                    signals.append(
                        TailSignal(
                            mode="TAIL_NO_LAY",
                            venue=q.venue,
                            book=q.book,
                            sport=q.sport,
                            stat=q.stat,
                            line=q.line,
                            p_tail_fair_over=p_tail_fair_over,
                            q_price=q_no,
                            fee_drag=fee,
                            edge_pp=edge_pp,
                            roi_or_net_ev=roi,
                            reason_code="TAIL_NO_LAY",
                        )
                    )
                    continue

        # Branch B: TAIL_YES_SMASH
        if routing == "OVER_ELIGIBLE":
            d_over: Optional[float] = None
            q_yes: Optional[float] = None
            if q.d_over is not None and q.d_over > 0:
                d_over = float(q.d_over)
                q_yes = float(q.q_yes) if q.q_yes is not None else 1.0 / d_over
            elif q.q_yes is not None and 0.0 < q.q_yes < 1.0:
                q_yes = float(q.q_yes)
                d_over = 1.0 / q_yes

            if d_over is not None and q_yes is not None and d_over >= 3.50:
                fee = tail_fee_drag(q.venue, q_yes)
                edge_pp = p_tail_fair_over - q_yes
                net_ev_pct = (p_tail_fair_over * d_over - 1.0 - fee * d_over) * 100.0

                if edge_pp >= 0.020 and net_ev_pct >= 5.0:
                    signals.append(
                        TailSignal(
                            mode="TAIL_YES_SMASH",
                            venue=q.venue,
                            book=q.book,
                            sport=q.sport,
                            stat=q.stat,
                            line=q.line,
                            p_tail_fair_over=p_tail_fair_over,
                            q_price=q_yes,
                            fee_drag=fee,
                            edge_pp=edge_pp,
                            roi_or_net_ev=net_ev_pct,
                            reason_code="TAIL_YES_SMASH",
                        )
                    )

    return signals
