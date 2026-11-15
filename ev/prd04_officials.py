"""
ev.prd04_officials -- the PRD-04 officials gate, SHADOW ONLY (adopted Nov 15, 2026;
RUL-20261115-prd04-officials-scope rounds 2 + 3; build spec ~/workspace/beezy/prd-04-build-spec-2026-11-15.md).

The live officials definition stays exactly where it has always been (ev.model_picks.qualifies). This module
computes, for every board row the live path would at least TRACK, what the PRD-04 configuration WOULD do with it,
and logs those verdicts to a shadow record (PRD04Shadow/{ct_day}/{sport}.json via mlb_core.storage). Nothing here
feeds the Daily Card, the PROPS ledger, Discord, or any public surface. The shadow week runs with this logging on
(PRD04_SHADOW=1 in the picks job) until the pre-registered bars pass and Luc approves the cutover; only then does
any of this become the live gate.

Slice 1 scope (this module as shipped):
  * the tiered market gate replacing the 3-market allowlist + single score floor:
      Tier 1 (floor 40): nba rebounds / threes / points
      Tier 2 (floor 48): nhl sog / saves (saves: intraday first-sighting, goalie confirmation required);
        the Tier 2 floor drops to 42 once the trained props serve (NHL_TRAINED_PROPS) or a board-engine fit
        confirms c >= 0.15 at t >= 2.5 for a prior-pending market (see tier2_floor)
      Tier 3 (BLOCKED, shadow-log only): nba assists / pra / pr / pa / ra / steals / blocks, board-engine NHL
        goals / assists / points, every production NFL market, anytime_td -- in fact every (sport, stat) not in
        Tier 1 / Tier 2, including trained skater rows until slice 3 opens their path.
  * a real blended-EV cap: MAX_BLEND_EV = 0.100, band [0.025, 0.100] on the row's blend_ev (round 2). NOTE the
    live gate's band is on the winner's-curse-SHRUNK official EV with floor OFFICIAL_MIN_BLEND_EV = 0.035 (Argon
    data-methods D4); both numbers are recorded on every shadow row so the cutover decision can reconcile them
    with a week of data in hand. The shadow gate enforces the adopted PRD-04 band.
  * an odds ceiling: MAX_OFFICIAL_AMERICAN_ODDS = +500 on the bet side (the existing 10% EV caps stay absolute).
  * the rest of the live official stack, unchanged: the tracked-pick base criteria (inherited by only evaluating
    rows ev.model_picks.qualifies does not reject), n_books >= 4, market_ev in [3%, 10%], model_edge >= 1.5%.

Slice 2 scope (this module as shipped):
  * the sharp check is now the adopted FAIL-CLOSED one (sharp_check below), replacing the live gate's fail-open
    sharp_agrees inside the shadow gate only: a play needs a sharp reference (p_sharp_ref, stamped at board
    assembly by ev.daily_projections.prd04_consensus_fields from the sharp-weighted consensus machinery) with
    p_blend within +2.5pp of it AND the reference itself clearing +0.5% EV at the best price. No reference ->
    the check fails, with exactly two escapes: the round-3 Kalshi fallback (kalshi_fallback: veto only, active
    only when every adopted liquidity condition verifiably holds) and, for the OPENER cohort (slice 4), the
    unconfirmed-sharp bypass tagged OPENER_UNCONFIRMED_SHARP. Every shadow record carries the sharp cohort
    fields (sharp_status / sharp_cohort / the reference and weighted-consensus values) so the shadow-week
    validation -- sharp_agrees rows must beat sharp_missing rows by >= +0.50% CLV -- is computable from the tape.

Slice 3 scope (this module as shipped):
  * the NHL trained skater path (round 3): goals / assists / points rows priced by the TRAINED engine
    (nhl_model == "trained_v1") are Tier 2 at floor 48 -- the board engine's skater rows stay Tier 3, the
    engine invariant is absolute. The path reprices the blend with the round-3 weights (goals .10 /
    assists .12 / points .12) in place of the board's BLEND_C_TRAINED, requires blend_ev and market_ev
    inside [3%, 10%], caps skater odds at +250, and hard-blocks uncertain lineup status. The floor drops
    to 42 when the trained-skater shadow cohort's mean CLV reaches +1.00% (trained_skater_floor; the
    judging pass hands the measured value in as skater_cohort_clv).

Tier 3 re-entry bar (round 2, for the record; enforced by whoever judges the shadow tape, not by this gate):
held-out walk-forward c >= 0.10 (t >= 2.5) on the served engine against the sharp-weighted consensus, plus
n >= 100 shadow plays with mean CLV >= +1.00% and ROI CI not excluding 0 from below. Fallback guardrail (round 1):
c = 0 markets producing > 2 locked plays/week triggers a signal-floor review -- every shadow record carries its
blend_c so that count is computable from the tape.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ev import daily_card as DC
from ev import daily_projections as DP
from nhl.props import saves_hardening as SH

log = logging.getLogger("ev.prd04_officials")

_CT = ZoneInfo("America/Chicago")

# -- adopted constants -----------------------------------------------------------------------------------------
MAX_BLEND_EV = 0.100                    # round 2: the real blended-EV cap (none existed on blend_ev itself)
MIN_BLEND_EV = 0.025                    # round 2: the adopted band floor on blend_ev (see the module docstring
                                        # for the 0.035 shrunk-EV floor the live gate uses alongside it)
MAX_OFFICIAL_AMERICAN_ODDS = 500        # round 1, kept in round 2: no official beyond +500
MIN_MARKET_EV, MAX_MARKET_EV = 0.030, 0.100   # the Tier-1 market_ev band, unchanged from ev.daily_card
MIN_MODEL_EDGE = 0.015                  # unchanged from ev.daily_card.is_confluence_play
MIN_BOOKS = 4                           # unchanged (ev.model_picks.OFFICIAL_MIN_BOOKS)

TIER1_MARKETS = frozenset({("nba", "rebounds"), ("nba", "threes"), ("nba", "points")})
TIER1_FLOOR = 40
TIER2_MARKETS = frozenset({("nhl", "sog"), ("nhl", "saves")})
TIER2_FLOOR = 48
TIER2_FLOOR_TRAINED_OR_FIT = 42
# A board-engine fit confirming c >= 0.15 at t >= 2.5 for a prior-pending Tier 2 market also earns the 42 floor.
# No such fit exists as of the adoption date; flip this (with the study cited) when one lands.
TIER2_BOARD_FIT_CONFIRMED = False

SHADOW_KEY_FMT = "PRD04Shadow/{day}/{sport}.json"

# -- slice 2: the fail-closed sharp check -------------------------------------------------------------------------
SHARP_REF_MAX_BLEND_GAP = 0.025       # p_blend may sit at most +2.5pp above the sharp reference fair
SHARP_REF_MIN_PRICE_FACTOR = 1.005    # and the reference fair must still clear +0.5% EV at the best price
OPENER_BYPASS_MIN_BOOKS = 3           # the OPENER cohort's unconfirmed-sharp bypass (slice 4 passes cohort="opener")
OPENER_BYPASS_MAX_DISP = 0.045
# Round 3 Kalshi fallback activation: ALL conditions must verifiably hold, and the fallback is a veto only.
KALSHI_MAX_SPREAD = 0.04
KALSHI_MIN_DEPTH = 500                # contracts on the thinner side of the book
KALSHI_MAX_AGE_SEC = 900.0
KALSHI_MID_MIN, KALSHI_MID_MAX = 0.15, 0.85
KALSHI_VETO_GAP = 0.030               # |p_blend - kalshi mid| beyond this vetoes the play
KALSHI_MIN_PRICE_FACTOR = 1.005       # the bet must clear +0.5% EV against the Kalshi mid at the best price


def shadow_enabled() -> bool:
    """PRD04_SHADOW=1 turns the shadow pass on in ev.model_picks.run. Default OFF: merging this module changes
    no runtime behavior until the picks job opts in."""
    return os.environ.get("PRD04_SHADOW", "").strip() in ("1", "true", "yes", "on")


def tier2_floor() -> int:
    """48, dropping to 42 once the trained props serve or a board fit confirms a prior-pending market."""
    if TIER2_BOARD_FIT_CONFIRMED or DP.trained_enabled():
        return TIER2_FLOOR_TRAINED_OR_FIT
    return TIER2_FLOOR


def market_tier(r: dict) -> int:
    """The PRD-04 tier of a board row's (sport, stat): 1, 2, or 3 (blocked). Slice 3 opens the trained
    skater path: an NHL goals / assists / points row priced by trained_v1 is Tier 2; the same market on
    the board engine stays Tier 3 (see is_trained_skater)."""
    key = (str(r.get("sport") or "").lower(), str(r.get("stat") or "").lower())
    if key in TIER1_MARKETS:
        return 1
    if key in TIER2_MARKETS:
        return 2
    if is_trained_skater(r):
        return 2
    return 3


# -- slice 3: the NHL trained skater path ---------------------------------------------------------------------------
TRAINED_SKATER_MARKETS = frozenset({("nhl", "goals"), ("nhl", "assists"), ("nhl", "points")})
TRAINED_SKATER_WEIGHTS = {"goals": 0.10, "assists": 0.12, "points": 0.12}   # round 3, replacing BLEND_C_TRAINED here
TRAINED_SKATER_FLOOR = 48
TRAINED_SKATER_FLOOR_CLV = 42
TRAINED_SKATER_CLV_BAR = 0.01           # the cohort's 7-day mean CLV at +1.00% earns the 42 floor
TRAINED_SKATER_MIN_EV = 0.030           # blend_ev AND market_ev inside [3%, 10%] on this path
TRAINED_SKATER_MAX_AMERICAN = 250       # the skater odds ceiling, tighter than the global +500
SKATER_LINEUP_BLOCKS = frozenset({"OUT", "IR", "DTD"})   # unconfirmed status blocks: no shadow official on a maybe


def is_trained_skater(r: dict) -> bool:
    """True for an NHL goals / assists / points row priced by the trained engine (nhl_model == 'trained_v1').
    The engine invariant: a board-engine (empirical_ewm_v2) skater row NEVER enters the trained path."""
    return (str(r.get("sport") or "").lower(), str(r.get("stat") or "").lower()) in TRAINED_SKATER_MARKETS \
        and r.get("nhl_model") == "trained_v1"


def trained_skater_floor(cohort_mean_clv: float | None = None) -> int:
    """48, dropping to 42 once the trained-skater shadow cohort's mean CLV reaches +1.00% (round 3). The
    judging pass measures the cohort from the shadow tape and hands the value in; None keeps 48."""
    if cohort_mean_clv is not None and cohort_mean_clv >= TRAINED_SKATER_CLV_BAR:
        return TRAINED_SKATER_FLOOR_CLV
    return TRAINED_SKATER_FLOOR


def _logit(p: float) -> float:
    import math
    p = min(max(float(p), 0.01), 0.99)
    return math.log(p / (1.0 - p))


def prd04_blend(r: dict) -> tuple:
    """(p_blend_side, blend_ev, blend_c) under the PRD-04 configuration for one row.

    Every row except trained skaters keeps the board's stamped blend. A trained skater row is repriced with
    the round-3 weight in the SAME c-only logit blend the board runs (ev.daily_projections.blend_p_over
    with (a, b) = (0, 1)): sigmoid(logit(p_mkt) + c * (logit(p_model) - logit(p_mkt))) off the row's stamped
    side probabilities, and blend_ev at the row's best price on its side. (If a pre-blend isotonic
    calibrator is ever registered for these stats -- none is today -- the board's own blend would differ
    by that calibration step; the shadow record carries both blends so the difference stays visible.)"""
    from ev import model_picks as MP          # lazy, see shadow_verdict
    side = str(r.get("side") or "").lower()
    if not is_trained_skater(r):
        return MP._num(r.get("p_blend_side")), MP._num(r.get("blend_ev")), None
    import math
    c = TRAINED_SKATER_WEIGHTS[str(r.get("stat") or "").lower()]
    p_model = MP._num(r.get("p_model_side"))
    p_mkt = MP._num(r.get("p_mkt_side"))
    best = MP._best(r, side) if side in ("over", "under") else None
    if p_model is None or p_mkt is None or best is None:
        return None, None, c
    z = _logit(p_mkt) + c * (_logit(p_model) - _logit(p_mkt))
    p_blend = 1.0 / (1.0 + math.exp(-z))
    from mlb_core.odds.utils import american_to_decimal
    return p_blend, p_blend * american_to_decimal(best[1]) - 1.0, c


def _floor_for(tier: int, r: dict | None = None, skater_cohort_clv: float | None = None) -> int | None:
    if tier == 1:
        return TIER1_FLOOR
    if tier == 2:
        if r is not None and is_trained_skater(r):
            return trained_skater_floor(skater_cohort_clv)
        return tier2_floor()
    return None


def kalshi_fallback(r: dict, side: str) -> dict:
    """The round-3 Kalshi p_sharp_ref fallback state for a board row: {active, veto, mid_side, checks}.

    Active only when EVERY adopted condition verifiably holds on the row's Kalshi stamps (spread <= 0.04,
    depth >= 500 contracts, quote age <= 900s with a trade inside the trailing 30 min, mid inside
    [0.15, 0.85]). A condition the feed cannot evidence leaves the fallback INACTIVE -- on today's
    ParlayAPI Kalshi quotes depth / trade stamps are always None (see ev.daily_projections.prd04_kalshi_fields),
    so the fallback cannot activate until a depth-bearing Kalshi source is wired in. Fail-closed by
    construction. When active it is a VETO ONLY: it never raises a candidate's EV and never enters the
    consensus; it fails the play when p_blend disagrees with the mid by > 3.0pp or the best price does not
    clear +0.5% EV against the mid."""
    from ev import model_picks as MP          # lazy, see shadow_verdict
    mid_over = MP._num(r.get("kalshi_mid_over"))
    spread = MP._num(r.get("kalshi_spread"))
    depth = r.get("kalshi_depth")
    age = MP._num(r.get("kalshi_age_sec"))
    checks = {
        "mid_present": mid_over is not None,
        "spread_ok": spread is not None and spread <= KALSHI_MAX_SPREAD,
        "depth_ok": isinstance(depth, (int, float)) and not isinstance(depth, bool)
                    and float(depth) >= KALSHI_MIN_DEPTH,
        "age_ok": age is not None and age <= KALSHI_MAX_AGE_SEC,
        "traded_ok": r.get("kalshi_traded_30m") is True,
        "band_ok": mid_over is not None and KALSHI_MID_MIN <= mid_over <= KALSHI_MID_MAX,
    }
    out: dict[str, Any] = {"active": all(checks.values()), "veto": False, "mid_side": None, "checks": checks}
    if not out["active"] or side not in ("over", "under"):
        return out
    mid_side = mid_over if side == "over" else 1.0 - mid_over
    out["mid_side"] = mid_side
    best = MP._best(r, side)
    p_blend = MP._num(r.get("p_blend_side"))
    dec_best = None
    if best is not None:
        from mlb_core.odds.utils import american_to_decimal
        dec_best = american_to_decimal(best[1])
    veto = p_blend is None or abs(p_blend - mid_side) > KALSHI_VETO_GAP
    if dec_best is None or mid_side * dec_best < KALSHI_MIN_PRICE_FACTOR:
        veto = True
    out["veto"] = bool(veto)
    return out


def sharp_check(r: dict, cohort: str = "intraday") -> dict:
    """The PRD-04 fail-closed sharp verdict for one board row (slice 2): {ok, status, cohort_class,
    p_sharp_ref_side, tag, kalshi}.

    With a sharp reference stamped on the row (p_sharp_ref_over): pass requires p_blend - p_sharp_ref
    <= +2.5pp AND p_sharp_ref * dec_best >= 1.005. With NO reference the check FAILS CLOSED ('missing'),
    with exactly two escapes: an active Kalshi fallback that does not veto ('kalshi_pass'; a veto is
    'kalshi_veto'), and -- for cohort='opener' only -- the unconfirmed-sharp bypass at n_books >= 3 and
    disp <= 0.045 ('opener_bypass', tagged OPENER_UNCONFIRMED_SHARP).

    cohort_class is the shadow-week validation cohort (round 2: sharp_agrees rows must beat sharp_missing
    rows by >= +0.50% CLV): 'agrees' when a reference existed and passed, 'disagrees' when one existed and
    failed, 'missing' when none existed (the fallback / bypass rows live here)."""
    from ev import model_picks as MP          # lazy, see shadow_verdict
    from mlb_core.odds.utils import american_to_decimal
    side = str(r.get("side") or "").lower()
    p_blend = MP._num(r.get("p_blend_side"))
    best = MP._best(r, side) if side in ("over", "under") else None
    dec_best = american_to_decimal(best[1]) if best is not None else None
    ref_over = MP._num(r.get("p_sharp_ref_over"))
    out: dict[str, Any] = {"ok": False, "status": "missing", "cohort_class": "missing",
                           "p_sharp_ref_side": None, "tag": None, "kalshi": None}
    if ref_over is not None and side in ("over", "under"):
        ref_side = ref_over if side == "over" else 1.0 - ref_over
        out["p_sharp_ref_side"] = ref_side
        ok = (p_blend is not None and dec_best is not None
              and (p_blend - ref_side) <= SHARP_REF_MAX_BLEND_GAP
              and ref_side * dec_best >= SHARP_REF_MIN_PRICE_FACTOR)
        out.update(ok=bool(ok), status="pass" if ok else "fail",
                   cohort_class="agrees" if ok else "disagrees")
        return out
    kf = kalshi_fallback(r, side)
    out["kalshi"] = kf
    if kf["active"]:
        out.update(ok=not kf["veto"], status="kalshi_veto" if kf["veto"] else "kalshi_pass")
        return out
    if cohort == "opener":
        try:
            n_books = int(r.get("n_books") or 0)
        except (TypeError, ValueError):
            n_books = 0
        disp = MP._num(r.get("cons_disp"))
        if n_books >= OPENER_BYPASS_MIN_BOOKS and disp is not None and disp <= OPENER_BYPASS_MAX_DISP:
            out.update(ok=True, status="opener_bypass", tag="OPENER_UNCONFIRMED_SHARP")
    return out


def shadow_verdict(r: dict, live_tier: str | None = None, skater_cohort_clv: float | None = None,
                   cohort: str = "intraday") -> dict:
    """The PRD-04 verdict for one board row that the live path at least tracks.

    Returns the full shadow record: the tier, the floor applied, every gate input, `would_be_official`, and the
    list of failed checks in `reasons` (empty when it would be official). Pure: no I/O, no clock.
    `skater_cohort_clv` is the trained-skater shadow cohort's measured mean CLV, handed in by the judging
    pass; it only moves the trained-skater floor (48 -> 42 at +1.00%). `cohort` selects the sharp check's
    semantics: "opener" (slice 4) enables the OPENER_UNCONFIRMED_SHARP bypass; anything else is the
    fail-closed intraday check."""
    from ev import model_picks as MP          # lazy: model_picks imports this module lazily too

    tier = market_tier(r)
    floor = _floor_for(tier, r, skater_cohort_clv)
    skater = is_trained_skater(r)
    side = str(r.get("side") or "").lower()
    best = MP._best(r, side) if side in ("over", "under") else None
    book, american = best if best else (None, None)
    blend_ev = MP._num(r.get("blend_ev"))
    trained = r.get("nhl_model") == "trained_v1"
    blend_c = DP.blend_c(r.get("sport"), r.get("stat"), trained=trained)
    rec: dict[str, Any] = {
        "sport": r.get("sport"), "player": r.get("player"), "stat": r.get("stat"), "side": side,
        "point": MP._num(r.get("line")), "book": book, "american": american,
        "tier": tier, "floor": floor, "live_tier": live_tier,
        "score": DC.parse_beezy_score(r), "n_books": r.get("n_books"),
        "market_ev": MP._num(r.get("market_ev")), "model_edge": MP._num(r.get("model_edge")),
        "blend_ev": blend_ev, "official_ev_shrunk": MP.official_ev(r), "blend_c": blend_c,
        "blend_c_zero": blend_c == 0.0,
        "prior_pending_board_fit": (str(r.get("sport") or "").lower(), str(r.get("stat") or "").lower())
                                   in DP.BLEND_PRIOR_PENDING_BOARD_FIT,
        "nhl_model": r.get("nhl_model"), "engine": r.get("engine"),
        "game_key": r.get("game_key"), "commence": r.get("commence"),
        "mins_to_start": MP._num(r.get("mins_to_start")),
        "row_key": DP.row_key(r),
        "record_key": f"{DP.row_key(r)}|{side}",
        "would_be_official": False, "reasons": [],
    }
    reasons: list[str] = rec["reasons"]
    p_blend_p4, blend_ev_p4, c_p4 = prd04_blend(r)
    rec.update({
        "p_blend_side_prd04": p_blend_p4, "blend_ev_prd04": blend_ev_p4,
        "blend_c_prd04": c_p4 if c_p4 is not None else blend_c,
        "trained_path": skater, "lineup_status": r.get("lineup_status"),
    })
    sc = sharp_check({**r, "p_blend_side": p_blend_p4} if skater else r, cohort=cohort)
    rec.update({
        "sharp_status": sc["status"], "sharp_cohort": sc["cohort_class"], "sharp_tag": sc["tag"],
        "p_sharp_ref_over": MP._num(r.get("p_sharp_ref_over")), "p_sharp_ref_side": sc["p_sharp_ref_side"],
        "p_sharp_ref_book": r.get("p_sharp_ref_book"), "n_sharp_ref": r.get("n_sharp_ref"),
        "p_cons_w_over": MP._num(r.get("p_cons_w_over")), "cons_disp": MP._num(r.get("cons_disp")),
        "kalshi_mid_over": MP._num(r.get("kalshi_mid_over")), "kalshi_spread": MP._num(r.get("kalshi_spread")),
        "kalshi_fallback_active": bool(sc["kalshi"] and sc["kalshi"]["active"]),
        "kalshi_veto": bool(sc["kalshi"] and sc["kalshi"]["veto"]),
    })
    if tier == 3:
        reasons.append("tier_3_blocked")
        return rec
    # NHL game-line engines have no validated edge (mirrors ev.daily_card.is_confluence_play's guard).
    if str(r.get("system") or "").upper().startswith(("NBA_", "NHL_")) and r.get("validated") is not True:
        reasons.append("game_line_engine_unvalidated")
    try:
        n_books = int(r.get("n_books") or 0)
    except (TypeError, ValueError):
        n_books = 0
    if n_books < MIN_BOOKS:
        reasons.append(f"n_books<{MIN_BOOKS}")
    mkt_ev = rec["market_ev"]
    if mkt_ev is None or not (MIN_MARKET_EV <= mkt_ev <= MAX_MARKET_EV):
        reasons.append("market_ev_out_of_band")
    if rec["model_edge"] is None or rec["model_edge"] < MIN_MODEL_EDGE:
        reasons.append("model_edge_below_min")
    if floor is not None and rec["score"] < floor:
        reasons.append(f"score_below_floor_{floor}")
    band_lo = TRAINED_SKATER_MIN_EV if skater else MIN_BLEND_EV
    if blend_ev_p4 is None or not (band_lo <= blend_ev_p4 <= MAX_BLEND_EV):
        reasons.append("blend_ev_out_of_band")
    if american is not None and american > MAX_OFFICIAL_AMERICAN_ODDS:
        reasons.append("odds_above_ceiling")
    if not sc["ok"]:
        reasons.append(f"sharp_check_{sc['status']}")
    if (str(r.get("sport") or "").lower(), str(r.get("stat") or "").lower()) == ("nhl", "saves"):
        # Tier 2 saves: intraday first-sighting product (the shadow log's first-sighting dedupe IS the
        # first-sighting rule) with goalie confirmation required -- the saves hardening verdict, which carries
        # the dual-confirmed starter gate, must pass whenever the board stamped one.
        rec["goalie_confirmed"] = r.get("saves_starter_dual_confirmed") is True
        verdict = SH.officials_gate_from_row(r)
        if verdict is not None and not verdict.ok:
            reasons.append("saves_hardening_gate")
        if "saves_starter_dual_confirmed" in r and r.get("saves_starter_dual_confirmed") is not True:
            reasons.append("goalie_not_confirmed")
    if skater:
        # Round 3 trained-path extras: the tighter +250 skater ceiling and the lineup hard-block --
        # an uncertain status (a maybe-scratched skater) never produces a shadow official.
        if american is not None and american > TRAINED_SKATER_MAX_AMERICAN:
            reasons.append("skater_odds_above_ceiling")
        if r.get("status_uncertain") is True:
            reasons.append("status_uncertain")
        ls = str(r.get("lineup_status") or "").upper()
        if ls in SKATER_LINEUP_BLOCKS:
            reasons.append(f"lineup_block_{ls.lower()}")
    rec["would_be_official"] = not reasons
    return rec


def evaluate_payload(payload: dict, now: datetime | None = None,
                     skater_cohort_clv: float | None = None) -> list[dict]:
    """Shadow records for every row on a board payload that the live path at least tracks (qualifies() not
    None). Rows the live path ignores entirely are not shadow business -- the tracked tier is the funnel the
    PRD-04 gate re-judges."""
    from ev import model_picks as MP          # lazy, see shadow_verdict
    out = []
    for r in payload.get("rows") or []:
        live_tier = MP.qualifies(r, now)
        if live_tier is None:
            continue
        out.append(shadow_verdict(r, live_tier=live_tier, skater_cohort_clv=skater_cohort_clv))
    return out


def shadow_key(sport: str, day: str) -> str:
    return SHADOW_KEY_FMT.format(day=day, sport=sport)


def log_shadow(sport: str, records: list[dict], now: datetime | None = None) -> int:
    """Append new shadow records to the day's shadow file; first sighting of a record_key wins (the same
    anti-cherry-pick rule as the live ledger, and what makes Tier 2 saves a first-sighting product).
    Returns the number of NEW records written. Never raises: a shadow failure must not sink the picks pass."""
    from mlb_core import storage
    now = now or datetime.now(_CT)
    day = now.astimezone(_CT).strftime("%Y-%m-%d") if now.tzinfo else now.strftime("%Y-%m-%d")
    key = shadow_key(sport, day)
    try:
        existing: list = []
        if storage.exists(key):
            existing = (json.loads(storage.read_bytes(key).decode("utf-8")) or {}).get("records") or []
        seen = {str(x.get("record_key")) for x in existing}
        fresh = []
        for rec in records:
            if str(rec.get("record_key")) in seen:
                continue
            seen.add(str(rec.get("record_key")))
            fresh.append({**rec, "shadow_logged_at": now.isoformat()})
        if fresh:
            payload = {"date": day, "sport": sport, "records": existing + fresh}
            storage.write_bytes(json.dumps(payload, indent=2, default=str).encode("utf-8"), key)
        return len(fresh)
    except Exception:                                                                    # noqa: BLE001
        log.warning("prd04_officials: shadow write failed for %s", sport, exc_info=True)
        return 0
