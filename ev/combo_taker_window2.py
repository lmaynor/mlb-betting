"""ev/combo_taker_window2.py -- Window 2 Portfolio Architecture Package (RES-12 / RUL-20261119).

Governing documents:
- Pre-registration (LOCKED): ~/workspace/beezy/rfq-window2-preregistration-2026-11-19.md
- Ruling: RUL-20261119 (Neon, 'RFQ Taker Window 2 Machinery Redesign'), verbatim at
  ~/workspace/beezy/argon-rulings/RUL-20261119-rfq-taker-window2-verbatim.md

FENCES (hard): Pure, signal/decision only module. Shadow-only execution logging.
NO venue clients, NO order placement, NO network, NO imports from any execution module.
Do NOT change scan_combos or ComboLiabilityGuard behavior/defaults (Window 1 stays frozen).
"""

from __future__ import annotations

import collections
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from ev.combo_taker_scanner import (
    ComboCandidate,
    ComboLeg,
    ComboLiabilityGuard,
    ComboNoSignal,
    _make_leg_stub,
    evaluate_combo,
)
from ev.kalshi_rfq_pricer import PricerConfig, pair_rho_true
from ev.sgp_book_pricer import copula_joint


# -----------------------------------------------------------------------------
# 2a. Window2Config
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Window2Config:
    """Pre-registered configuration for Window 2 Portfolio Architecture Package."""
    lambda_leg: float = 0.50
    lambda_game: float = 0.15
    batch_window_sec: float = 60.0
    tier_a_roi_threshold: float = 0.12
    tier_b_cap_fraction: float = 0.40
    release_mins_to_tip: float = 60.0
    combo_cap_tier_a: float = 10.0
    combo_cap_tier_b: float = 5.0
    max_contracts_per_combo: int = 40
    shared_leg_cap: float = 35.0
    player_cap: float = 50.0
    game_cap_dollars: float = 150.0
    game_pool_fraction: float = 0.25
    game_max_combo_legs: int = 15
    daily_pool: float = 600.0
    r_max: float = 2.00
    roi_input_floor: float = -0.50
    hinge_roi: float = 0.030
    eb_k0: float = 100.0
    eb_family_min_rows: int = 30
    output_clamp: float = 0.50
    output_floor: float = -1.0
    cold_start_haircut: float = 0.50
    trust_floor_fires: int = 100
    trust_floor_evaluated: int = 250
    trust_floor_days: int = 7
    derank_multiplier: float = 0.75
    breaker_min_fires: int = 50
    breaker_min_days: int = 5
    breaker_mean_pnl: float = -0.020
    theta_stress: float = 0.000
    stress_dispersion_mult: float = 1.5
    stale_pad_near: float = 0.030
    stale_pad_far: float = 0.010
    stale_near_age_sec: float = 1800.0
    stale_far_age_sec: float = 3600.0
    stale_near_mins_to_tip: float = 120.0
    cond_b_roi: float = 1.50
    cond_b_ratio: float = 2.50


# -----------------------------------------------------------------------------
# 2b. Families & Trust Book
# -----------------------------------------------------------------------------

FAMILIES: Tuple[str, ...] = ("NBA_PROP", "NHL_PROP", "NFL_PROP", "GAME_LINE")

_SPORT_TO_FAMILY = {
    "nba": "NBA_PROP",
    "nhl": "NHL_PROP",
    "nfl": "NFL_PROP",
}

_REGIME_PRECEDENCE = {
    "CIRCUIT_BROKEN": 0,
    "ACCRUING": 1,
    "TRUSTED": 2,
}


def leg_family(leg: ComboLeg) -> str:
    """Classify a leg into a family. Fails closed on unknown sports with players."""
    if leg.player_norm:
        sport = leg.sport.lower()
        if sport in _SPORT_TO_FAMILY:
            return _SPORT_TO_FAMILY[sport]
        raise ValueError(f"Unknown sport with player: {leg.sport}")
    return "GAME_LINE"


class FamilyTrustBook:
    """Tracks per-family settled FIRE counts, distinct game days, summed pnl_per_contract,

    and settled EVALUATED counts.
    """

    def __init__(self, cfg: Optional[Window2Config] = None) -> None:
        self.cfg = cfg or Window2Config()
        self.fires: Dict[str, int] = collections.defaultdict(int)
        self.days: Dict[str, Set[str]] = collections.defaultdict(set)
        self.pnl_sum: Dict[str, float] = collections.defaultdict(float)
        self.evaluated: Dict[str, int] = collections.defaultdict(int)

    def add_fire(self, family: str, day: str, pnl_per_contract: float) -> None:
        self.fires[family] += 1
        self.days[family].add(day)
        self.pnl_sum[family] += float(pnl_per_contract)

    def add_evaluated(self, family: str, day: str) -> None:
        self.evaluated[family] += 1
        self.days[family].add(day)

    def regime(self, family: str) -> str:
        fires = self.fires[family]
        days = len(self.days[family])
        pnl = self.pnl_sum[family]
        evaluated = self.evaluated[family]
        mean_pnl = (pnl / fires) if fires > 0 else 0.0

        if (
            fires >= self.cfg.breaker_min_fires
            and days >= self.cfg.breaker_min_days
            and mean_pnl <= self.cfg.breaker_mean_pnl
        ):
            return "CIRCUIT_BROKEN"
        if days >= self.cfg.trust_floor_days and (
            fires >= self.cfg.trust_floor_fires or evaluated >= self.cfg.trust_floor_evaluated
        ):
            return "TRUSTED"
        return "ACCRUING"

    def tau(self, family: str) -> float:
        reg = self.regime(family)
        if reg == "TRUSTED":
            return 1.00
        if reg == "ACCRUING":
            return self.cfg.derank_multiplier
        return 0.0


def combo_family(
    legs: Sequence[ComboLeg], book: FamilyTrustBook
) -> Tuple[str, str, float]:
    """Determine family label, regime, and tau for a combo.

    Weakest regime wins (CIRCUIT_BROKEN < ACCRUING < TRUSTED).
    Label is the weakest leg's family (first such leg in leg order on ties).
    """
    if not legs:
        return ("GAME_LINE", "ACCRUING", book.cfg.derank_multiplier)

    leg_fams = [leg_family(leg) for leg in legs]
    leg_regimes = [book.regime(f) for f in leg_fams]

    min_rank = min(_REGIME_PRECEDENCE[r] for r in leg_regimes)
    weakest_idx = min(
        i for i, r in enumerate(leg_regimes) if _REGIME_PRECEDENCE[r] == min_rank
    )

    label = leg_fams[weakest_idx]
    regime = leg_regimes[weakest_idx]
    tau = book.tau(label)
    return (label, regime, tau)


# -----------------------------------------------------------------------------
# 2c. Calibration
# -----------------------------------------------------------------------------

def _fit_piecewise_ridge(
    x_arr: np.ndarray, y_arr: np.ndarray, w_arr: np.ndarray, hinge_x: float
) -> np.ndarray:
    """Solve weighted ridge least squares for y ~ alpha + beta_1*x + beta_2*max(0, x - hinge_x).

    Ridge penalty 1e-6 applied only to slopes beta_1 and beta_2 (not alpha).
    """
    if len(x_arr) == 0:
        return np.array([0.0, 0.0, 0.0], dtype=float)

    X = np.column_stack([
        np.ones_like(x_arr),
        x_arr,
        np.maximum(0.0, x_arr - hinge_x),
    ])
    R = np.diag([0.0, 1e-6, 1e-6])
    A = (X.T * w_arr) @ X + R
    b = (X.T * w_arr) @ y_arr
    try:
        beta = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        beta = np.linalg.lstsq(A, b, rcond=None)[0]
    return beta


def _eval_curve(beta: np.ndarray, x: float, hinge_x: float) -> float:
    return float(beta[0] + beta[1] * x + beta[2] * max(0.0, x - hinge_x))


class RoiCalibrator:
    """Empirical-Bayes shrinkage calibrator mapping claimed ROI to expected ROI."""

    def __init__(
        self,
        cfg: Window2Config,
        pooled_beta: np.ndarray,
        family_betas: Dict[str, np.ndarray],
        family_row_counts: Dict[str, int],
        x_cap: Optional[float],
        family_fire_counts: Optional[Dict[str, int]] = None,
        counts_basis: str = "evaluated_fallback",
    ) -> None:
        self.cfg = cfg
        self.pooled_beta = pooled_beta
        self.family_betas = family_betas
        self.family_row_counts = family_row_counts
        self.x_cap = x_cap
        self._hinge_x = math.log(1.0 + cfg.hinge_roi)
        # RUL-20261123 item 4: the EB combination weight's n_f is the family's SETTLED FIRES
        # (pre-registration section 6: "n_f the family's settled fires in the estimation population"),
        # not its settled evaluated rows. counts_basis records the unit family_fire_counts is in:
        # "fires" when the fit rows carried fire flags (the wired shadow path always does), or
        # "evaluated_fallback" when no row carried one (legacy callers whose row population is the
        # fire population by construction). build_shadow_admission refuses the fallback so the live
        # shadow path always knows its unit.
        # RATIFIED (Luc, Nov 24, 2026): fires-based n_f as implemented here. The alternative --
        # the evaluated-units equivalent (k_0 = 250 / min_rows = 75, matching section 7's stated
        # 250-evaluated ~= 100-fires equivalence) -- was considered and not taken. Fires was chosen
        # because section 6's words name settled fires and it leaves every locked config value
        # untouched.
        self.family_fire_counts = dict(family_fire_counts) if family_fire_counts is not None else dict(family_row_counts)
        self.counts_basis = counts_basis

    def predict(self, roi_claimed: float, family: str) -> float:
        x_raw = math.log(1.0 + min(max(float(roi_claimed), self.cfg.roi_input_floor), self.cfg.r_max))
        x = min(x_raw, self.x_cap) if self.x_cap is not None else x_raw

        # n_f = the family's settled fires (item 4; see __init__). family_row_counts (evaluated rows)
        # still gates whether a family curve was FIT at all -- a family can only reach w > 0 with
        # >= eb_family_min_rows fires, and fires are a subset of evaluated rows, so the curve exists
        # whenever the weight is nonzero.
        family_fires = self.family_fire_counts.get(family, 0)
        if family_fires < self.cfg.eb_family_min_rows:
            w = 0.0
        else:
            w = family_fires / (family_fires + self.cfg.eb_k0)

        pooled_val = _eval_curve(self.pooled_beta, x, self._hinge_x)
        if family in self.family_betas and w > 0.0:
            fam_val = _eval_curve(self.family_betas[family], x, self._hinge_x)
            val = w * fam_val + (1.0 - w) * pooled_val
        else:
            val = pooled_val

        return float(min(self.cfg.output_clamp, max(self.cfg.output_floor, val)))

    def sizing_value(self, roi_claimed: float, family: str, cold_start: bool) -> float:
        pred = self.predict(roi_claimed, family)
        return float(pred * (self.cfg.cold_start_haircut if cold_start else 1.0))


def fit_calibrator(
    rows: Sequence[Dict[str, Any]], cfg: Optional[Window2Config] = None
) -> RoiCalibrator:
    """Fit Q5 RoiCalibrator from settled evaluation rows."""
    if cfg is None:
        cfg = Window2Config()

    hinge_x = math.log(1.0 + cfg.hinge_roi)

    parsed_rows: List[Dict[str, Any]] = []
    any_fire_flags = False
    for r in rows:
        ask_no = float(r["ask_no"])
        fee = 0.07 * ask_no * (1.0 - ask_no)
        collateral = ask_no + fee
        if collateral <= 0.0:
            continue
        pnl = float(r["pnl_per_contract"])
        realized_roi = pnl / collateral
        roi_claimed = float(r["roi_claimed"])
        x = math.log(1.0 + min(max(roi_claimed, cfg.roi_input_floor), cfg.r_max))
        # Item 4: a row's fire status rides on "is_fire" (or the banked records' "fires" key). The
        # flag is only read here for the EB weight's unit; the fit itself still uses EVERY settled
        # evaluated row, exactly as pre-registration section 6 specifies for the estimation population.
        is_fire = r.get("is_fire", r.get("fires"))
        if is_fire is not None:
            any_fire_flags = True
        parsed_rows.append({
            "x": x,
            "y": realized_roi,
            "w": collateral,
            "family": str(r.get("family", "GAME_LINE")),
            "is_fire": bool(is_fire),
        })

    if not parsed_rows:
        zero_beta = np.array([0.0, 0.0, 0.0], dtype=float)
        return RoiCalibrator(
            cfg=cfg,
            pooled_beta=zero_beta,
            family_betas={},
            family_row_counts={},
            x_cap=None,
        )

    # Pooled fit
    x_all = np.array([r["x"] for r in parsed_rows], dtype=float)
    y_all = np.array([r["y"] for r in parsed_rows], dtype=float)
    w_all = np.array([r["w"] for r in parsed_rows], dtype=float)

    pooled_beta = _fit_piecewise_ridge(x_all, y_all, w_all, hinge_x)

    # Family fits
    family_row_counts: Dict[str, int] = collections.defaultdict(int)
    family_fire_counts: Dict[str, int] = collections.defaultdict(int)
    family_betas: Dict[str, np.ndarray] = {}
    family_groups: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in parsed_rows:
        family_groups[r["family"]].append(r)
        family_row_counts[r["family"]] += 1
        if r["is_fire"]:
            family_fire_counts[r["family"]] += 1
    if not any_fire_flags:
        # No row carried a fire flag: the caller's population is the fire population by construction
        # (legacy fixtures), so the counts coincide. The wired shadow path never lands here --
        # build_shadow_admission raises on this basis instead of silently mixing units.
        family_fire_counts = collections.defaultdict(int, family_row_counts)
    counts_basis = "fires" if any_fire_flags else "evaluated_fallback"

    for fam, f_rows in family_groups.items():
        if len(f_rows) >= cfg.eb_family_min_rows:
            fx = np.array([r["x"] for r in f_rows], dtype=float)
            fy = np.array([r["y"] for r in f_rows], dtype=float)
            fw = np.array([r["w"] for r in f_rows], dtype=float)
            family_betas[fam] = _fit_piecewise_ridge(fx, fy, fw, hinge_x)

    # Quartile clamp
    x_cap: Optional[float] = None
    if len(parsed_rows) >= 4:
        sorted_rows = sorted(parsed_rows, key=lambda r: r["x"])
        splits = np.array_split(sorted_rows, 4)
        quartile_means: List[float] = []
        for chunk in splits:
            if len(chunk) == 0:
                quartile_means.append(0.0)
                continue
            total_w = sum(r["w"] for r in chunk)
            q_mean = sum(r["w"] * r["y"] for r in chunk) / total_w if total_w > 0 else 0.0
            quartile_means.append(q_mean)

        # Compare Q4 (index 3) vs Q3 (index 2)
        if quartile_means[3] < quartile_means[2]:
            q3_rows = splits[2]
            if len(q3_rows) > 0:
                x_cap = float(max(r["x"] for r in q3_rows))

    return RoiCalibrator(
        cfg=cfg,
        pooled_beta=pooled_beta,
        family_betas=family_betas,
        family_row_counts=dict(family_row_counts),
        x_cap=x_cap,
        family_fire_counts=dict(family_fire_counts),
        counts_basis=counts_basis,
    )


# -----------------------------------------------------------------------------
# 2c-ii. Shadow-path wiring (RUL-20261123 item 2)
# -----------------------------------------------------------------------------

def accrue_settled_rows(book: FamilyTrustBook, rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Accrue SETTLED rows into a FamilyTrustBook -> counts accrued.

    Row shape: {"family": str, "day": str, "is_fire": bool, "pnl_per_contract": float | None}.
    Every row accrues as one settled EVALUATED combo for its family; rows with is_fire accrue as a
    settled FIRE as well and must carry pnl_per_contract (a fire without a settled pnl is a caller
    bug and raises -- fail loudly, never silently skip).

    Two rules the pre-registration leaves to the implementation, fixed here:
    - SETTLED-ONLY ACCRUAL LAG: a row may be accrued only after its outcome is known, and the caller
      accrues a day's rows only AFTER that day's admission decisions are made (run_candidates does
      exactly this). A family's regime on day D therefore reflects settlements through day D-1 at
      the latest -- never the day being decided. This matches section 7's ">= 7 distinct SETTLED
      game days" and keeps the shadow honest: no same-day outcome can lift its own family.
    - MIXED-FAMILY ATTRIBUTION: a combo accrues ONLY to its single weakest-link family label (the
      combo_family() label: the family of the weakest-regime leg, first such leg on ties) -- the
      same label its admission decision carries. It never accrues fractionally or to every leg's
      family: the trust book prices the combo as one unit, so its outcome grades one unit.
    """
    n_eval = n_fire = 0
    for r in rows:
        fam = str(r["family"])
        day = str(r["day"])
        book.add_evaluated(fam, day)
        n_eval += 1
        if r.get("is_fire", r.get("fires")):
            pnl = r.get("pnl_per_contract")
            if pnl is None:
                raise ValueError(f"settled fire row for family {fam} day {day} carries no pnl_per_contract")
            book.add_fire(fam, day, float(pnl))
            n_fire += 1
    return {"evaluated": n_eval, "fires": n_fire}


def build_shadow_admission(
    fit_rows: Sequence[Dict[str, Any]],
    cfg: Optional[Window2Config] = None,
    cold_start: bool = True,
) -> Window2Admission:
    """Build the WIRED Window2Admission for the Ledger B-shadow path (RUL-20261123 item 2).

    Fits the RoiCalibrator on the settled fit rows and seeds the FamilyTrustBook by accruing those
    same rows (they are history: settled, and all decided before the shadow run starts, so the
    settled-only lag is respected). Raises on an empty population or on rows without fire flags --
    the shadow path must never run on an uncalibrated engine or on mixed EB units, and it must
    never silently fall back to raw roi_no_taker (the pre-staging CLI constructed Window2Admission()
    bare, which did exactly that).
    """
    if not fit_rows:
        raise ValueError("build_shadow_admission: no settled fit rows -- refusing to wire an uncalibrated shadow")
    cfg = cfg or Window2Config()
    calibrator = fit_calibrator(fit_rows, cfg)
    if calibrator.counts_basis != "fires":
        raise ValueError(
            "build_shadow_admission: fit rows carry no fire flags, so the EB weight's unit is "
            "ambiguous (evaluated rows vs settled fires). Supply is_fire/fires on every row.")
    book = FamilyTrustBook(cfg)
    accrue_settled_rows(book, fit_rows)
    return Window2Admission(cfg=cfg, calibrator=calibrator, trust_book=book, cold_start=cold_start)


# -----------------------------------------------------------------------------
# 2d. ROTTEN_LEG_V2 (Shadow-only)
# -----------------------------------------------------------------------------

@dataclass
class LegStressInputs:
    p_max: Optional[float] = None
    q_kalshi: Optional[float] = None
    disp: Optional[float] = None
    age_sec: float = 0.0


@dataclass
class RottenLegV2Result:
    flag: bool
    conditions: Tuple[str, ...]
    edge_stress: float
    worst_leg_index: int
    p_hi: Tuple[float, ...]


def rotten_leg_v2(
    candidate: ComboCandidate,
    signal: ComboNoSignal,
    stress: Sequence[Optional[LegStressInputs]],
    cfg: Window2Config,
    timing_approx: bool = False,
) -> RottenLegV2Result:
    """Stressed counterfactual worst-leg test (shadow-only).

    timing_approx (RUL-20261123 item 7): the candidate's game-start times are synthetic
    (now + 3600 stamped because no commence time was known), so mins_to_tip is not evidence about
    the real tip. The near staleness pad (+0.030) is conditioned on VERIFIED proximity to tip
    (age > 30 min AND mins_to_tip <= 120); with synthetic timing that branch must not fire -- the
    far pad (+0.010, age alone) still applies."""
    legs = candidate.legs
    m = len(legs)
    if m == 0:
        return RottenLegV2Result(
            flag=False,
            conditions=(),
            edge_stress=0.0,
            worst_leg_index=-1,
            p_hi=(),
        )

    mins_to_tip = (min(leg.game_start_ts for leg in legs) - candidate.now_ts) / 60.0

    p_hi_list: List[float] = []
    for j, leg in enumerate(legs):
        s = (
            stress[j]
            if (stress is not None and j < len(stress) and stress[j] is not None)
            else LegStressInputs()
        )
        p_j = float(leg.p_lambda)
        comps = [p_j]
        if s.p_max is not None:
            comps.append(float(s.p_max))
        if s.q_kalshi is not None:
            comps.append(float(s.q_kalshi))
        if s.disp is not None:
            comps.append(float(p_j + cfg.stress_dispersion_mult * s.disp))

        pad = (
            cfg.stale_pad_near
            if (s.age_sec > cfg.stale_near_age_sec and mins_to_tip <= cfg.stale_near_mins_to_tip
                and not timing_approx)
            else (cfg.stale_pad_far if s.age_sec > cfg.stale_far_age_sec else 0.0)
        )
        p_hi_j = min(0.99, max(comps) + pad)
        p_hi_list.append(p_hi_j)

    # Rebuild TRUE copula correlation matrix Sigma_true
    stubs = [_make_leg_stub(leg) for leg in legs]
    pricer_cfg = PricerConfig()
    Sigma_true = np.eye(m)
    for i in range(m):
        for j in range(i + 1, m):
            if legs[i].game_id == legs[j].game_id:
                rt = pair_rho_true(stubs[i], legs[i].direction, stubs[j], legs[j].direction, pricer_cfg)
                Sigma_true[i, j] = Sigma_true[j, i] = (rt if rt is not None else 0.0)
            else:
                Sigma_true[i, j] = Sigma_true[j, i] = 0.0

    venue_clean = candidate.venue.lower().strip()
    fee_per_contract = 0.0 if venue_clean == "novig" else 1.0 * 0.07 * signal.ask_no * (1.0 - signal.ask_no)

    base_p_lambdas = [min(max(float(leg.p_lambda), 1e-6), 1.0 - 1e-6) for leg in legs]

    edges: List[float] = []
    for j in range(m):
        p_stress_j = list(base_p_lambdas)
        p_stress_j[j] = min(max(float(p_hi_list[j]), 1e-6), 1.0 - 1e-6)
        P_stress_j = copula_joint(p_stress_j, Sigma_true)
        edge_j = (1.0 - P_stress_j) - signal.ask_no - fee_per_contract
        edges.append(float(edge_j))

    worst_leg_index = int(np.argmin(edges))
    edge_stress = float(edges[worst_leg_index])

    conditions: List[str] = []
    if edge_stress < cfg.theta_stress:
        conditions.append("EDGE_COLLAPSE")
    if (
        signal.roi_no_taker > cfg.cond_b_roi
        and signal.p_true_yes > 0
        and (candidate.bid_yes / signal.p_true_yes) > cfg.cond_b_ratio
    ):
        conditions.append("TAIL_RATIO_ANOMALY")

    return RottenLegV2Result(
        flag=(len(conditions) > 0),
        conditions=tuple(conditions),
        edge_stress=edge_stress,
        worst_leg_index=worst_leg_index,
        p_hi=tuple(p_hi_list),
    )


# -----------------------------------------------------------------------------
# 2e. Admission Engine + Shadow Log
# -----------------------------------------------------------------------------

@dataclass
class Window2Fire:
    candidate: ComboCandidate
    signal: ComboNoSignal
    stress: Tuple[Optional[LegStressInputs], ...] = ()
    day: str = ""
    # RUL-20261123 item 7: True when the candidate's game-start times are synthetic (stamped
    # now + 3600 because no commence time was known). Such a fire's mins_to_tip is exactly 60.0 by
    # construction and must not satisfy the Tier A T-60m release (the Tier B reservation stays in
    # force for it) nor V2's verified-proximity staleness branch.
    timing_approx: bool = False


@dataclass
class Window2Decision:
    combo_id: str
    day: str
    batch_start_ts: float
    tier: str
    family: str
    family_regime: str
    tau_fam: float
    roi_claimed: float
    roi_cal: float
    sizing_roi: float
    score: float
    n_shared_leg: int
    n_game: int
    discount: float
    contracts: int
    collateral: float
    admitted: bool
    refusals: Tuple[str, ...]
    rotten_leg_v2: bool
    rotten_leg_v2_conditions: Tuple[str, ...]
    edge_stress: float
    legacy_rotten_leg: bool
    window1_admitted: Optional[bool]


class Window2Admission:
    """Window 2 Admission Engine implementing composite S_i ranking, tiered reservation,

    four-level cap hierarchy, and Ledger B shadow logging.
    """

    def __init__(
        self,
        cfg: Optional[Window2Config] = None,
        calibrator: Optional[RoiCalibrator] = None,
        trust_book: Optional[FamilyTrustBook] = None,
        cold_start: bool = True,
    ) -> None:
        self.cfg = cfg or Window2Config()
        self.calibrator = calibrator
        self.trust_book = trust_book or FamilyTrustBook(self.cfg)
        self.cold_start = cold_start

    def run_day(self, fires: Sequence[Window2Fire], day: str) -> List[Window2Decision]:
        """Process candidate fires for a single game day and emit decision records."""
        firing_items = [f for f in fires if f.signal.fires]
        if not firing_items:
            return []

        # 1. Window 1 shadow comparison: fresh ComboLiabilityGuard, now_ts order, stake set on a
        # copy of candidate. RUL-20261123 item 14(b): the stake is FEE-INCLUSIVE collateral --
        # the largest whole-contract position (<= 100 contracts) whose stake + taker fee fits the
        # $15 per-combo collateral cap. The pre-fix stake = min($15, ask_no * 100) excluded the
        # fee, so the guard's ledgers understated true collateral by up to ~7% (~$1.05) and the
        # cap bound looser than the pre-registration's collateral definition. The fee uses this
        # module's closed form (the module fence bars execution-module imports); the harness path
        # uses the canonical taker_fee for the identical computation.
        w1_guard = ComboLiabilityGuard()
        w1_admitted_map: Dict[str, bool] = {}
        w1_ordered = sorted(firing_items, key=lambda f: (f.candidate.now_ts, f.candidate.combo_id))
        for f in w1_ordered:
            ask_no = f.signal.ask_no
            unit = ask_no + 0.07 * ask_no * (1.0 - ask_no)
            n_contracts = min(100, int(15.0 // unit)) if unit > 0.0 else 0
            cand_copy = ComboCandidate(
                combo_id=f.candidate.combo_id,
                venue=f.candidate.venue,
                legs=f.candidate.legs,
                bid_yes=f.candidate.bid_yes,
                age_since_creation_sec=f.candidate.age_since_creation_sec,
                is_rfq_live=f.candidate.is_rfq_live,
                now_ts=f.candidate.now_ts,
                stake=n_contracts * unit,
            )
            w1_admitted_map[f.candidate.combo_id] = w1_guard.admit(cand_copy, f.signal)

        # 2. Batching: batch key = floor(now_ts / batch_window_sec); ascending order.
        batches: Dict[int, List[Window2Fire]] = collections.defaultdict(list)
        for f in firing_items:
            bkey = int(math.floor(f.candidate.now_ts / self.cfg.batch_window_sec))
            batches[bkey].append(f)

        sorted_batch_keys = sorted(batches.keys())

        # Cumulative day state tracking
        day_collateral = 0.0
        tier_b_day_collateral = 0.0

        game_collateral: Dict[str, float] = collections.defaultdict(float)
        tier_b_game_collateral: Dict[str, float] = collections.defaultdict(float)

        game_legs: Dict[str, int] = collections.defaultdict(int)
        tier_b_game_legs: Dict[str, int] = collections.defaultdict(int)

        leg_collateral: Dict[Tuple[str, Optional[str], str], float] = collections.defaultdict(float)
        tier_b_leg_collateral: Dict[Tuple[str, Optional[str], str], float] = collections.defaultdict(float)

        player_collateral: Dict[Tuple[str, str], float] = collections.defaultdict(float)
        tier_b_player_collateral: Dict[Tuple[str, str], float] = collections.defaultdict(float)

        admitted_fires_today: List[Window2Fire] = []
        decisions: List[Window2Decision] = []

        effective_game_cap = min(self.cfg.game_cap_dollars, self.cfg.game_pool_fraction * self.cfg.daily_pool)

        def _v2(fire: Window2Fire) -> Tuple[RottenLegV2Result, bool]:
            """rotten_leg_v2 with per-combo exception isolation (RUL-20261123 item 6): a fire whose
            stress evaluation raises is failed CLOSED by the caller, never allowed to abort the day."""
            try:
                return rotten_leg_v2(fire.candidate, fire.signal, fire.stress, self.cfg,
                                     timing_approx=fire.timing_approx), False
            except Exception:  # noqa: BLE001 -- isolation is the point; the caller refuses the combo
                return RottenLegV2Result(flag=False, conditions=("EVALUATION_ERROR",), edge_stress=0.0,
                                         worst_leg_index=-1, p_hi=()), True

        for bkey in sorted_batch_keys:
            batch_fires = batches[bkey]
            batch_start_ts = float(bkey * self.cfg.batch_window_sec)
            remaining = list(batch_fires)

            while remaining:
                scores: Dict[str, float] = {}
                discounts: Dict[str, float] = {}
                n_shared_map: Dict[str, int] = {}
                n_game_map: Dict[str, int] = {}
                fam_info_map: Dict[str, Tuple[str, str, float]] = {}
                roi_cal_map: Dict[str, float] = {}

                # RUL-20261123 item 6: per-combo exception isolation. Scoring one combo (family
                # classification raises ValueError on an unknown-sport player leg; the calibrator
                # can raise on a malformed row) must fail THAT combo closed, never abort the day.
                errored: List[Window2Fire] = []
                for f in remaining:
                    cid = f.candidate.combo_id
                    cand = f.candidate
                    try:
                        cand_leg_keys = {(leg.game_id, leg.player_norm, leg.stat) for leg in cand.legs}
                        cand_games = {leg.game_id for leg in cand.legs if leg.game_id}

                        n_shared = sum(
                            1
                            for af in admitted_fires_today
                            if cand_leg_keys & {(l.game_id, l.player_norm, l.stat) for l in af.candidate.legs}
                        )
                        n_game = sum(
                            1
                            for af in admitted_fires_today
                            if cand_games & {l.game_id for l in af.candidate.legs if l.game_id}
                        )

                        n_shared_map[cid] = n_shared
                        n_game_map[cid] = n_game

                        discount = 1.0 / (1.0 + self.cfg.lambda_leg * n_shared + self.cfg.lambda_game * n_game)
                        discounts[cid] = discount

                        fam_label, regime, tau_fam = combo_family(cand.legs, self.trust_book)
                        fam_info_map[cid] = (fam_label, regime, tau_fam)

                        roi_claimed = f.signal.roi_no_taker
                        if self.calibrator is not None:
                            roi_cal = self.calibrator.predict(roi_claimed, fam_label)
                        else:
                            roi_cal = roi_claimed
                        roi_cal_map[cid] = roi_cal

                        score = roi_cal * tau_fam * discount
                        scores[cid] = score
                    except Exception:  # noqa: BLE001 -- fail this combo closed, keep the day alive
                        errored.append(f)

                for f in errored:
                    remaining.remove(f)
                    cid = f.candidate.combo_id
                    v2_res, _ = _v2(f)
                    decisions.append(
                        Window2Decision(
                            combo_id=cid,
                            day=f.day if f.day else day,
                            batch_start_ts=batch_start_ts,
                            tier="B",
                            family=fam_info_map.get(cid, ("UNKNOWN", "ACCRUING", 0.0))[0],
                            family_regime=fam_info_map.get(cid, ("UNKNOWN", "ACCRUING", 0.0))[1],
                            tau_fam=fam_info_map.get(cid, ("UNKNOWN", "ACCRUING", 0.0))[2],
                            roi_claimed=f.signal.roi_no_taker,
                            roi_cal=roi_cal_map.get(cid, 0.0),
                            sizing_roi=0.0,
                            score=scores.get(cid, 0.0),
                            n_shared_leg=n_shared_map.get(cid, 0),
                            n_game=n_game_map.get(cid, 0),
                            discount=discounts.get(cid, 1.0),
                            contracts=0,
                            collateral=0.0,
                            admitted=False,
                            refusals=("EVALUATION_ERROR",),
                            rotten_leg_v2=v2_res.flag,
                            rotten_leg_v2_conditions=v2_res.conditions,
                            edge_stress=v2_res.edge_stress,
                            legacy_rotten_leg=f.signal.has_rotten_leg,
                            window1_admitted=w1_admitted_map.get(cid),
                        )
                    )
                if not remaining:
                    break

                # RUL-20261123 item 5: sign-inversion guard. S_i = ROI^cal * tau / (1 + ...) ranks a
                # NEGATIVE calibrated ROI *higher* the more concentrated the combo is (dividing a
                # negative by a larger denominator moves it toward zero from below). A combo
                # calibrated to <= 0 is therefore refused outright here -- it can never be admitted
                # and can never outrank a positive-ROI^cal combo through the denominator.
                nonpositive = [f for f in remaining if roi_cal_map[f.candidate.combo_id] <= 0.0]
                for f in nonpositive:
                    remaining.remove(f)
                    cid = f.candidate.combo_id
                    fam_label, regime, tau_fam = fam_info_map[cid]
                    roi_cal = roi_cal_map[cid]
                    if self.calibrator is not None:
                        sizing_roi = self.calibrator.sizing_value(f.signal.roi_no_taker, fam_label, self.cold_start)
                    else:
                        sizing_roi = roi_cal * (self.cfg.cold_start_haircut if self.cold_start else 1.0)
                    v2_res, _ = _v2(f)
                    decisions.append(
                        Window2Decision(
                            combo_id=cid,
                            day=f.day if f.day else day,
                            batch_start_ts=batch_start_ts,
                            tier="B",
                            family=fam_label,
                            family_regime=regime,
                            tau_fam=tau_fam,
                            roi_claimed=f.signal.roi_no_taker,
                            roi_cal=roi_cal,
                            sizing_roi=sizing_roi,
                            score=scores[cid],
                            n_shared_leg=n_shared_map[cid],
                            n_game=n_game_map[cid],
                            discount=discounts[cid],
                            contracts=0,
                            collateral=0.0,
                            admitted=False,
                            refusals=("NONPOSITIVE_CALIBRATED_ROI",),
                            rotten_leg_v2=v2_res.flag,
                            rotten_leg_v2_conditions=v2_res.conditions,
                            edge_stress=v2_res.edge_stress,
                            legacy_rotten_leg=f.signal.has_rotten_leg,
                            window1_admitted=w1_admitted_map.get(cid),
                        )
                    )
                if not remaining:
                    break

                # Select candidate with max S_i (tiebreaker: earlier now_ts, then combo_id)
                best_fire = min(
                    remaining,
                    key=lambda f: (
                        -scores[f.candidate.combo_id],
                        f.candidate.now_ts,
                        f.candidate.combo_id,
                    ),
                )
                remaining.remove(best_fire)

                cid = best_fire.candidate.combo_id
                cand = best_fire.candidate
                sig = best_fire.signal
                fam_label, regime, tau_fam = fam_info_map[cid]
                roi_cal = roi_cal_map[cid]
                roi_claimed = sig.roi_no_taker
                score = scores[cid]
                discount = discounts[cid]
                n_shared = n_shared_map[cid]
                n_game = n_game_map[cid]

                if self.calibrator is not None:
                    sizing_roi = self.calibrator.sizing_value(roi_claimed, fam_label, self.cold_start)
                else:
                    sizing_roi = roi_cal * (self.cfg.cold_start_haircut if self.cold_start else 1.0)

                v2_res, v2_errored = _v2(best_fire)
                dec_day = best_fire.day if best_fire.day else day

                # Item 6: a fire whose stress evaluation raised is refused, never admitted
                # unflagged and never allowed to abort the day.
                if v2_errored:
                    decisions.append(
                        Window2Decision(
                            combo_id=cid,
                            day=dec_day,
                            batch_start_ts=batch_start_ts,
                            tier="B",
                            family=fam_label,
                            family_regime=regime,
                            tau_fam=tau_fam,
                            roi_claimed=roi_claimed,
                            roi_cal=roi_cal,
                            sizing_roi=sizing_roi,
                            score=score,
                            n_shared_leg=n_shared,
                            n_game=n_game,
                            discount=discount,
                            contracts=0,
                            collateral=0.0,
                            admitted=False,
                            refusals=("EVALUATION_ERROR",),
                            rotten_leg_v2=False,
                            rotten_leg_v2_conditions=("EVALUATION_ERROR",),
                            edge_stress=0.0,
                            legacy_rotten_leg=sig.has_rotten_leg,
                            window1_admitted=w1_admitted_map.get(cid),
                        )
                    )
                    continue

                # Circuit breaker check
                if regime == "CIRCUIT_BROKEN":
                    decisions.append(
                        Window2Decision(
                            combo_id=cid,
                            day=dec_day,
                            batch_start_ts=batch_start_ts,
                            tier="B",
                            family=fam_label,
                            family_regime=regime,
                            tau_fam=tau_fam,
                            roi_claimed=roi_claimed,
                            roi_cal=roi_cal,
                            sizing_roi=sizing_roi,
                            score=score,
                            n_shared_leg=n_shared,
                            n_game=n_game,
                            discount=discount,
                            contracts=0,
                            collateral=0.0,
                            admitted=False,
                            refusals=("FAMILY_CIRCUIT_BROKEN",),
                            rotten_leg_v2=v2_res.flag,
                            rotten_leg_v2_conditions=v2_res.conditions,
                            edge_stress=v2_res.edge_stress,
                            legacy_rotten_leg=sig.has_rotten_leg,
                            window1_admitted=w1_admitted_map.get(cid),
                        )
                    )
                    continue

                # Tier determination
                tier = "A" if (roi_cal >= self.cfg.tier_a_roi_threshold and regime == "TRUSTED") else "B"

                ask_no = sig.ask_no
                venue_clean = cand.venue.lower().strip()
                fee_taker = 0.0 if venue_clean == "novig" else 1.0 * 0.07 * ask_no * (1.0 - ask_no)
                unit = ask_no + fee_taker
                combo_cap = self.cfg.combo_cap_tier_a if tier == "A" else self.cfg.combo_cap_tier_b

                if unit > 0.0:
                    contracts = min(self.cfg.max_contracts_per_combo, max(1, math.floor(combo_cap / unit)))
                    collateral = contracts * unit
                else:
                    contracts = 0
                    collateral = 0.0

                mins_to_tip = (
                    (min(leg.game_start_ts for leg in cand.legs) - cand.now_ts) / 60.0
                    if cand.legs
                    else 0.0
                )

                cand_games = list({leg.game_id for leg in cand.legs if leg.game_id})
                cand_leg_keys = list({(leg.game_id, leg.player_norm, leg.stat) for leg in cand.legs})
                cand_player_keys = list({(leg.game_id, leg.player_norm) for leg in cand.legs if leg.player_norm})

                failed_caps: List[str] = []

                # Cap check 1: DAILY_POOL
                if day_collateral + collateral > self.cfg.daily_pool + 1e-9:
                    failed_caps.append("DAILY_POOL")

                # Cap check 2: GAME_CAP
                for g in cand_games:
                    if game_collateral[g] + collateral > effective_game_cap + 1e-9:
                        failed_caps.append("GAME_CAP")
                        break

                # Cap check 3: GAME_LEG_COUNT
                for g in cand_games:
                    legs_in_g = sum(1 for leg in cand.legs if leg.game_id == g)
                    if game_legs[g] + legs_in_g > self.cfg.game_max_combo_legs:
                        failed_caps.append("GAME_LEG_COUNT")
                        break

                # Cap check 4: SHARED_LEG_CAP
                for lk in cand_leg_keys:
                    if leg_collateral[lk] + collateral > self.cfg.shared_leg_cap + 1e-9:
                        failed_caps.append("SHARED_LEG_CAP")
                        break

                # Cap check 5: PLAYER_CAP
                for pk in cand_player_keys:
                    if player_collateral[pk] + collateral > self.cfg.player_cap + 1e-9:
                        failed_caps.append("PLAYER_CAP")
                        break

                # Tier B reservation checks (when total caps passed). RUL-20261123 item 7: a
                # timing_approx fire's mins_to_tip is exactly 60.0 by construction (synthetic
                # start = now + 3600), which silently satisfied the T-60m release and let it eat
                # the reserved 60%. Synthetic timing is not a release: the reservation stays in
                # force for such fires until a verified start time says otherwise.
                if len(failed_caps) == 0:
                    if tier == "B" and (mins_to_tip > self.cfg.release_mins_to_tip or best_fire.timing_approx):
                        frac = self.cfg.tier_b_cap_fraction
                        res_failed = False

                        if tier_b_day_collateral + collateral > frac * self.cfg.daily_pool + 1e-9:
                            res_failed = True

                        if not res_failed:
                            for g in cand_games:
                                if tier_b_game_collateral[g] + collateral > frac * effective_game_cap + 1e-9:
                                    res_failed = True
                                    break

                        if not res_failed:
                            max_b_legs = math.floor(frac * self.cfg.game_max_combo_legs)
                            for g in cand_games:
                                legs_in_g = sum(1 for leg in cand.legs if leg.game_id == g)
                                if tier_b_game_legs[g] + legs_in_g > max_b_legs:
                                    res_failed = True
                                    break

                        if not res_failed:
                            for lk in cand_leg_keys:
                                if tier_b_leg_collateral[lk] + collateral > frac * self.cfg.shared_leg_cap + 1e-9:
                                    res_failed = True
                                    break

                        if not res_failed:
                            for pk in cand_player_keys:
                                if tier_b_player_collateral[pk] + collateral > frac * self.cfg.player_cap + 1e-9:
                                    res_failed = True
                                    break

                        if res_failed:
                            failed_caps.append("TIER_B_RESERVATION")

                admitted = (len(failed_caps) == 0)

                if admitted:
                    day_collateral += collateral
                    if tier == "B":
                        tier_b_day_collateral += collateral

                    for g in cand_games:
                        legs_in_g = sum(1 for leg in cand.legs if leg.game_id == g)
                        game_collateral[g] += collateral
                        game_legs[g] += legs_in_g
                        if tier == "B":
                            tier_b_game_collateral[g] += collateral
                            tier_b_game_legs[g] += legs_in_g

                    for lk in cand_leg_keys:
                        leg_collateral[lk] += collateral
                        if tier == "B":
                            tier_b_leg_collateral[lk] += collateral

                    for pk in cand_player_keys:
                        player_collateral[pk] += collateral
                        if tier == "B":
                            tier_b_player_collateral[pk] += collateral

                    admitted_fires_today.append(best_fire)

                dec = Window2Decision(
                    combo_id=cid,
                    day=dec_day,
                    batch_start_ts=batch_start_ts,
                    tier=tier,
                    family=fam_label,
                    family_regime=regime,
                    tau_fam=tau_fam,
                    roi_claimed=roi_claimed,
                    roi_cal=roi_cal,
                    sizing_roi=sizing_roi,
                    score=score,
                    n_shared_leg=n_shared,
                    n_game=n_game,
                    discount=discount,
                    contracts=contracts,
                    collateral=collateral,
                    admitted=admitted,
                    refusals=tuple(failed_caps),
                    rotten_leg_v2=v2_res.flag,
                    rotten_leg_v2_conditions=v2_res.conditions,
                    edge_stress=v2_res.edge_stress,
                    legacy_rotten_leg=sig.has_rotten_leg,
                    window1_admitted=w1_admitted_map.get(cid),
                )
                decisions.append(dec)

        return decisions
