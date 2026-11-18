"""ev/combo_taker_scanner.py -- Module 5.2: Combo "NO" Deconstructor (RES-12b).

The 4-Blade Retail Parlay Deconstructor.
SIGNAL-ONLY: Evaluates multi-leg combos and emits decisions/signals only.
NO venue clients, NO order placement, NO network, NO imports from any execution module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Sequence, Set, Tuple

import numpy as np

from ev.kalshi_rfq_pricer import LegFairValue, PricerConfig, pair_rho_cons, pair_rho_true
from ev.sgp_book_pricer import copula_joint


@dataclass
class ComboLeg:
    """Input single leg of a multi-leg parlay/combo."""
    ticker: str
    venue: str
    player_norm: Optional[str]
    game_id: str
    sport: str
    stat: str
    line: float
    direction: int                 # +1 if over/yes direction, -1 if under/no direction
    p_lambda: float                # capability-masked fair P(this leg event hits)
    routing: str                   # CapabilityDecision routing string
    q_implied: float               # retail implied P of this leg event
    is_hot_scratch: bool
    game_start_ts: float           # epoch seconds
    team: Optional[str] = None


@dataclass
class ComboCandidate:
    """Input combo/parlay candidate to evaluate."""
    combo_id: str
    venue: str                     # "kalshi" | "novig"
    legs: List[ComboLeg]
    bid_yes: float                 # executable YES bid; Ask_NO = 1 - bid_yes
    age_since_creation_sec: float
    is_rfq_live: bool
    now_ts: float                  # current epoch seconds
    stake: Optional[float] = None  # optional stake exposure override; defaults to Ask_NO


@dataclass(frozen=True)
class ComboNoSignal:
    """Output evaluation decision for a combo NO taker signal."""
    combo_id: str
    venue: str
    ask_no: float
    p_true_yes: float
    p_cons_yes: float
    roi_no_taker: float
    has_rotten_leg: bool
    is_cannibalistic: bool
    fires: bool
    reasons: Tuple[str, ...]


def _make_leg_stub(leg: ComboLeg) -> LegFairValue:
    """Construct minimal LegFairValue stub for pairwise rho lookup."""
    return LegFairValue(
        ticker=leg.ticker,
        sport=leg.sport,
        game_key=leg.game_id,
        market_class="player_prop" if leg.player_norm else "game_line",
        player_norm=leg.player_norm,
        stat=leg.stat,
        strike=leg.line,
        p_cons_yes=leg.p_lambda,
        n_books=3,
        disp=0.01,
        p_model_yes=leg.p_lambda,
        pooled=False,
        updated_ts=0.0,
        team=leg.team,
    )


def evaluate_combo(candidate: ComboCandidate, cfg: Optional[PricerConfig] = None) -> ComboNoSignal:
    """Evaluate a single ComboCandidate through Blades 1, 2, and 3.

    SIGNAL-ONLY pure evaluation.
    """
    if cfg is None:
        cfg = PricerConfig()

    legs = candidate.legs
    m = len(legs)
    ask_no = 1.0 - candidate.bid_yes

    if m == 0:
        return ComboNoSignal(
            combo_id=candidate.combo_id,
            venue=candidate.venue,
            ask_no=ask_no,
            p_true_yes=0.0,
            p_cons_yes=0.0,
            roi_no_taker=0.0,
            has_rotten_leg=False,
            is_cannibalistic=False,
            fires=False,
            reasons=("NO_LEGS",),
        )

    p_lambdas = [min(max(float(leg.p_lambda), 1e-6), 1.0 - 1e-6) for leg in legs]

    # BLADE 1: Single Rotten-Leg Contagion Detector
    has_rotten_leg = any(
        (leg.routing == "ROUTE_TO_NO_TAKER" or (float(leg.q_implied) - float(leg.p_lambda)) >= 0.050)
        for leg in legs
    )

    # Build correlation matrices Sigma_true and Sigma_cons
    stubs = [_make_leg_stub(leg) for leg in legs]
    Sigma_true = np.eye(m)
    Sigma_cons = np.eye(m)

    for i in range(m):
        for j in range(i + 1, m):
            if legs[i].game_id == legs[j].game_id:
                rt = pair_rho_true(stubs[i], legs[i].direction, stubs[j], legs[j].direction, cfg)
                rc = pair_rho_cons(stubs[i], legs[i].direction, stubs[j], legs[j].direction, cfg)
                Sigma_true[i, j] = Sigma_true[j, i] = (rt if rt is not None else 0.0)
                Sigma_cons[i, j] = Sigma_cons[j, i] = (rc if rc is not None else 0.0)
            else:
                Sigma_true[i, j] = Sigma_true[j, i] = 0.0
                Sigma_cons[i, j] = Sigma_cons[j, i] = 0.0

    # BLADE 2: Cannibalistic / Negative-Correlation SGP Detector
    P_copula = copula_joint(p_lambdas, Sigma_true)
    P_indep = float(math.prod(p_lambdas))
    is_cannibalistic = bool(P_copula <= 0.85 * P_indep)

    # BLADE 3: Conservative Taker NO Strike Formula
    p_cons_list = [min(0.99, p + 0.008) for p in p_lambdas]
    P_cons_yes = copula_joint(p_cons_list, Sigma_cons)
    P_true_yes = P_copula

    venue_clean = candidate.venue.lower().strip()
    fee_taker = 0.0 if venue_clean == "novig" else 1.0 * 0.07 * ask_no * (1.0 - ask_no)

    net_cons_edge = (1.0 - P_cons_yes) - ask_no - fee_taker
    roi_no_taker = (net_cons_edge / ask_no) if ask_no > 0 else 0.0
    net_true_edge = (1.0 - P_true_yes) - ask_no - fee_taker

    reasons: List[str] = []
    if roi_no_taker < 0.030:
        reasons.append("ROI_BELOW_0.030")
    if net_true_edge < 0.025:
        reasons.append("EDGE_BELOW_0.025")
    if any(leg.is_hot_scratch for leg in legs):
        reasons.append("HOT_SCRATCH")
    if not all(leg.game_start_ts >= candidate.now_ts + 900 for leg in legs):
        reasons.append("GAME_TOO_CLOSE")
    if not (candidate.age_since_creation_sec >= 600 or candidate.is_rfq_live):
        reasons.append("COMBO_TOO_NEW")

    fires = (len(reasons) == 0)
    if fires:
        reasons.append("QUALIFIED_NO_TAKER")
        if has_rotten_leg:
            reasons.append("BLADE1_ROTTEN_LEG")
        if is_cannibalistic:
            reasons.append("BLADE2_CANNIBALISTIC")

    return ComboNoSignal(
        combo_id=candidate.combo_id,
        venue=candidate.venue,
        ask_no=ask_no,
        p_true_yes=P_true_yes,
        p_cons_yes=P_cons_yes,
        roi_no_taker=roi_no_taker,
        has_rotten_leg=has_rotten_leg,
        is_cannibalistic=is_cannibalistic,
        fires=fires,
        reasons=tuple(reasons),
    )


class ComboLiabilityGuard:
    """BLADE 4: Marginal Player & Game Liability Guard.

    Enforces cumulative NO liability caps across taken combos:
      max_exposure_per_player_dollars = 75.00
      max_exposure_per_game_dollars = 150.00
      max_per_combo_liability_dollars = 15.00
    Combos breaching any cap are skipped, not partially taken.
    """

    def __init__(
        self,
        max_exposure_per_player_dollars: float = 75.00,
        max_exposure_per_game_dollars: float = 150.00,
        max_per_combo_liability_dollars: float = 15.00,
    ):
        self.max_exposure_per_player_dollars = max_exposure_per_player_dollars
        self.max_exposure_per_game_dollars = max_exposure_per_game_dollars
        self.max_per_combo_liability_dollars = max_per_combo_liability_dollars
        self.player_exposure: Dict[str, float] = {}
        self.game_exposure: Dict[str, float] = {}
        self.admitted_combos: List[str] = []

    def get_liability(self, candidate: ComboCandidate) -> float:
        """Calculate liability for this candidate combo, capped at max_per_combo_liability_dollars."""
        raw = candidate.stake if candidate.stake is not None else (1.0 - candidate.bid_yes)
        return min(self.max_per_combo_liability_dollars, float(raw))

    def can_admit(self, candidate: ComboCandidate) -> bool:
        """Check whether candidate combo can be admitted without breaching caps."""
        liability = self.get_liability(candidate)

        players = {leg.player_norm for leg in candidate.legs if leg.player_norm}
        for p in players:
            if self.player_exposure.get(p, 0.0) + liability > self.max_exposure_per_player_dollars + 1e-9:
                return False

        games = {leg.game_id for leg in candidate.legs if leg.game_id}
        for g in games:
            if self.game_exposure.get(g, 0.0) + liability > self.max_exposure_per_game_dollars + 1e-9:
                return False

        return True

    def admit(self, candidate: ComboCandidate, signal: Optional[ComboNoSignal] = None) -> bool:
        """Attempt to admit candidate; updates cumulative exposures if admitted."""
        if not self.can_admit(candidate):
            return False

        liability = self.get_liability(candidate)
        players = {leg.player_norm for leg in candidate.legs if leg.player_norm}
        for p in players:
            self.player_exposure[p] = self.player_exposure.get(p, 0.0) + liability

        games = {leg.game_id for leg in candidate.legs if leg.game_id}
        for g in games:
            self.game_exposure[g] = self.game_exposure.get(g, 0.0) + liability

        self.admitted_combos.append(candidate.combo_id)
        return True


def scan_combos(
    candidates: Sequence[ComboCandidate],
    guard: Optional[ComboLiabilityGuard] = None,
    cfg: Optional[PricerConfig] = None,
) -> List[ComboNoSignal]:
    """Scan candidate combos, sort qualifying firing signals by ROI descending,

    and admit through Blade 4 ComboLiabilityGuard.
    SIGNAL-ONLY: Emits decisions only.
    """
    if guard is None:
        guard = ComboLiabilityGuard()

    evaluated: List[Tuple[ComboCandidate, ComboNoSignal]] = [
        (c, evaluate_combo(c, cfg=cfg)) for c in candidates
    ]

    # Filter to candidates that fire Blades 1-3
    firing = [(c, s) for c, s in evaluated if s.fires]

    # Sort firing candidates by ROI_no_taker descending
    firing.sort(key=lambda cs: cs[1].roi_no_taker, reverse=True)

    admitted_signals: List[ComboNoSignal] = []
    for c, s in firing:
        if guard.admit(c, s):
            admitted_signals.append(s)

    return admitted_signals
