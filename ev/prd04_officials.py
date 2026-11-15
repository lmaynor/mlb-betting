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
    rows ev.model_picks.qualifies does not reject), n_books >= 4, market_ev in [3%, 10%], model_edge >= 1.5%,
    and the CURRENT sharp-agreement check (ev.model_picks.official_caps' sharp_ok; slice 2 replaces it with the
    fail-closed p_sharp_ref check and the sharp-weighted consensus).

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
    """The PRD-04 tier of a board row's (sport, stat): 1, 2, or 3 (blocked). Engine does not matter in slice 1:
    trained skater rows are Tier 3 until slice 3 opens the trained path; nhl saves is Tier 2 on either engine."""
    key = (str(r.get("sport") or "").lower(), str(r.get("stat") or "").lower())
    if key in TIER1_MARKETS:
        return 1
    if key in TIER2_MARKETS:
        return 2
    return 3


def _floor_for(tier: int) -> int | None:
    if tier == 1:
        return TIER1_FLOOR
    if tier == 2:
        return tier2_floor()
    return None


def shadow_verdict(r: dict, live_tier: str | None = None) -> dict:
    """The PRD-04 verdict for one board row that the live path at least tracks.

    Returns the full shadow record: the tier, the floor applied, every gate input, `would_be_official`, and the
    list of failed checks in `reasons` (empty when it would be official). Pure: no I/O, no clock."""
    from ev import model_picks as MP          # lazy: model_picks imports this module lazily too

    tier = market_tier(r)
    floor = _floor_for(tier)
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
    if blend_ev is None or not (MIN_BLEND_EV <= blend_ev <= MAX_BLEND_EV):
        reasons.append("blend_ev_out_of_band")
    if american is not None and american > MAX_OFFICIAL_AMERICAN_ODDS:
        reasons.append("odds_above_ceiling")
    if not MP.official_caps(r)["sharp_ok"]:
        reasons.append("sharp_disagrees_current_check")
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
    rec["would_be_official"] = not reasons
    return rec


def evaluate_payload(payload: dict, now: datetime | None = None) -> list[dict]:
    """Shadow records for every row on a board payload that the live path at least tracks (qualifies() not
    None). Rows the live path ignores entirely are not shadow business -- the tracked tier is the funnel the
    PRD-04 gate re-judges."""
    from ev import model_picks as MP          # lazy, see shadow_verdict
    out = []
    for r in payload.get("rows") or []:
        live_tier = MP.qualifies(r, now)
        if live_tier is None:
            continue
        out.append(shadow_verdict(r, live_tier=live_tier))
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
