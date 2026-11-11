"""
ev.daily_projections -- one daily prop-projection board for NFL / NHL / NBA.

For every prop the books offer on the upcoming slate (or, when no lines have posted yet, the top rotation players on
today's scheduled teams) it produces ONE row joining:

  * a projection built from the sport's player game-log master    proj_mean, p_over_model, l5/l10 hit rate, szn_avg
  * the market, from the latest banked/live book quotes            consensus_line, best over/under price + book,
                                                                   p_over_market (median de-vigged P(over))
  * the two engines' agreement                                     model_edge, market_ev, confluence, beezy_score

    PYTHONPATH=. python -m ev.daily_projections --sport nfl|nhl|nba|all [--all] [--write-gcs | --no-write]

Served by mlb/runners/public_api.py::get_sport_projections() at GET /api/public/projections?sport=nfl|nhl|nba via
get_daily_projections(): process cache -> EVSweep/{sport}_props/projections_latest.json; a missing/stale file triggers a
BACKGROUND recompute, never an inline one. The CLI (`--all --write-gcs`) is the scheduled precompute: the Cloud Run job
`daily-projections` runs it every 30 min (deploy/setup_daily_projections_job.sh).

Inputs beyond the game-log masters (2026-10-17):
  lines    load_events = union of the banked ParlayAPI events and the BettingPros live feed (Odds/bettingpros_live/{sport}/
           latest.json, ev/bettingpros_events.py); see union_events for the freshness / dedup rules. Payload `lines_source`.
  teams    ev.rosters (the CURRENT roster) decides a player's team and who is a rotation player; without it the team is the
           last game-log row's, which is wrong after a trade / retirement.
  games    payload `games` lists every upcoming game of the window, priced or not.
  archive  every compute merges its rows into EVSweep/{sport}_props/archive/{Central game day}.json (the Yesterday board).

WHAT THE ENGINE IS (and is not). The trained research models (nfl/models/props.py, nhl/props/train.py,
nba/props/train.py) keep their artifacts LOCAL (never in GCS, never in the Cloud Run image), so the live board cannot
load them. It runs an empirical estimator instead -- engine "empirical_ewm_v2":

    mu    = w * EWM(value) + (1-w) * EWM(value)/EWM(usage) * projected usage          (usage = targets / carries /
            attempts / TOI / minutes; w = 0.5, STAT_PARAMS overrides where validated), then x an opponent-allowed
            factor (ratio of what that opponent's opposition produced vs each producer's own baseline, shrunk toward 1
            and clipped to [0.85, 1.15]; measured worth ~0, kept because it is harmless)
    NFL calibration (nfl/props/projection_calibration.json, only stats that cleared a held-out walk-forward bar):
            mu_cal = mu * k (scale) or mu * exp(b0 + b1 ln(implied team total/22.5) + b2 team spread/7) (game term)
            -- the pool of players a book quotes is selected on high trailing usage and regresses ~3-7% in every season
    dist  = NegBin (counts) / Normal (yards, points, saves) / Poisson at-least-one (anytime TD); the spread parameter is
            fit from the league's own within-player game-to-game variance in the same master. The NORMAL family's spread
            is tied to the UNCALIBRATED mean (cv was measured against raw trailing means). One exception: NBA points
            prices the right-skewed 3 x threes + nonthree marginal from the PR #186 study (same mu, same sd, skewed
            shape; NBA_SKEWED_POINTS=0 reverts), see the block above p_over.
    evidence: python -m nfl.props.engine_eval (walk-forward replay of THIS code, accuracy only -- see
            docs/solutions/conventions/nfl-projection-engine-changes-need-walkforward-evidence.md)

It does NOT model injuries, lineups, minutes restrictions, weather, rest or teammate-absence redistribution, and none
of it has been validated against closing prices: every row is `validated: false`. The only evidence for any of this
repo's NFL/NHL/NBA projections is accuracy vs trailing-mean baselines; no edge against prices has been shown
(NHL SOG: NO_EDGE; NFL / NBA: nothing claimable; see CONTEXT.md s20/s21).

Beezy Score here is lib/beezy-score.ts v2 (edge 60 + consensus size 20 + hit probability 20, gated on having an edge)
with the edge input chosen by which engines support the play:
    confluence (model AND market agree)  -> min(model_edge, market_ev)   both engines must clear that edge
    only one engine has a positive edge   -> half of that edge            (one engine alone gets half credit)
    neither                               -> 0
so STRONG (>= 70) needs a large edge on BOTH sides of the dual engine.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.stats import gamma, nbinom, norm

from ev import rosters as _rosters
from ev.props import DFS_BOOKS, OFFSHORE_BOOKS, build_consensus, parse_event_props
from mlb_core.data import cockpit_form as cf
from mlb_core.odds.utils import american_to_decimal
from nba.data import tracking as _trk
from nba.props import config as _nbacfg
from nba.props import hierarchical as _hier
from nba.props import team_context as _tc
from nfl.models import hierarchical_live as _hl
from nfl.props import env_adjust as _ea
from nhl.props import hierarchical as _nh
from nhl.props import saves_hardening as _sh

log = logging.getLogger("ev.daily_projections")
_ET = ZoneInfo("America/New_York")
_CT = ZoneInfo("America/Chicago")               # the site clock: archive days and the Today / Yesterday split are Central

ENGINE = "empirical_ewm_v2"
ENGINE_NFL_HIER = _hl.ENGINE            # "hierarchical_v1": served for NFL only when NFL_ENGINE=hierarchical (default off)
SPORTS = ("nfl", "nhl", "nba")
OUT_KEY = "EVSweep/{sport}_props/projections_latest.json"
ARCHIVE_KEY = "EVSweep/{sport}_props/archive/{day}.json"      # {day} = the game's America/Chicago start date

# stat -> distribution family
PROJECT_STATS = {
    "nfl": {"pass_yds": "normal", "pass_tds": "negbin", "pass_completions": "normal", "pass_attempts": "normal",
            "interceptions": "negbin", "rush_yds": "normal", "rush_attempts": "normal", "rec_yds": "normal",
            "receptions": "negbin", "anytime_td": "anytime"},
    # NHL pp_points = power-play goals + assists. The boxscore has no such column; nhl.data.powerplay derives it from the
    # play-by-play (exact vs the box's own power_play_goals) and cockpit_form fills it on older rows from the shots masters.
    # NHL saves is priced by nhl.props.hierarchical.SavesMixture (a two-regime mixture), not by this Normal family.
    "nhl": {"sog": "negbin", "goals": "negbin", "assists": "negbin", "points": "negbin", "pp_points": "negbin",
            "saves": "normal", "blocked_shots": "negbin"},
    "nba": {"points": "normal", "rebounds": "negbin", "assists": "negbin", "threes": "negbin", "pra": "normal",
            "pr": "normal", "pa": "normal", "ra": "normal", "steals": "negbin", "blocks": "negbin"},
}
STAT_LABEL = {
    "pass_yds": "Pass Yds", "rush_yds": "Rush Yds", "rec_yds": "Rec Yds", "receptions": "Receptions",
    "pass_tds": "Pass TDs", "anytime_td": "Anytime TD", "pass_completions": "Completions",
    "pass_attempts": "Pass Attempts", "rush_attempts": "Carries", "interceptions": "Interceptions",
    "sog": "Shots on Goal", "goals": "Goals", "points": "Points",
    "assists": "Assists", "saves": "Saves", "blocked_shots": "Blocked Shots", "pp_points": "PP Points",
    "rebounds": "Rebounds", "threes": "3-Pointers", "pra": "Pts+Reb+Ast", "pr": "Pts+Reb", "pa": "Pts+Ast",
    "ra": "Reb+Ast", "steals": "Steals", "blocks": "Blocks",
}

HALFLIFE = {"nfl": 3.0, "nhl": 8.0, "nba": 8.0}      # games
# Per-stat overrides of (halflife, ewm_weight, opp_k) -- ONLY where a walk-forward replay on held-out seasons showed a
# gain past a multiplicity-corrected bar (nfl.props.engine_eval; selected on 2021-23 / 2021-22, scored on the
# seasons after, never used for selection: rec_yds log score +0.027 / +0.032 per row, MAE -0.23 / -0.27 yds;
# anytime_td log score +0.012, Brier -0.0033). Every other stat keeps the original hand-set values.
STAT_PARAMS = {
    # NHL pp_points: a sparse count (0.18 / game for forwards), so a long half-life; chosen on the 2023 season of
    # scripts/nhl_engine_replay.py (log score best of 8 / 15 / 30 at each prior strength), scored on 2024-25.
    "nhl": {"pp_points": {"halflife": 30.0}},
    "nfl": {"rec_yds": {"halflife": 7.0, "ewm_weight": 1.0, "opp_k": 200.0},
            "anytime_td": {"halflife": 7.0, "ewm_weight": 0.3, "opp_k": 200.0}},
}


def engine_id(sport: str) -> str:
    """The engine id of a sport's board payload. NFL is "empirical_ewm_v2+hierarchical_v1" (a mix; each row carries its own
    `engine`) only when NFL_ENGINE=hierarchical AND at least one stat is allowlisted; otherwise the legacy id. The hierarchical
    engine changes the price of the stats it serves, so it ships OFF and only for stats whose held-out evidence cleared the
    adoption bar (nfl/props/hierarchical_allowlist.json;
    docs/solutions/conventions/nfl-hierarchical-engine-adoption.md)."""
    if sport == "nfl" and _hl.enabled() and _hl.allowed_stats():
        return f"{ENGINE}+{ENGINE_NFL_HIER}"
    return ENGINE


def stat_params(sport: str, stat: str) -> tuple:
    """-> (halflife, ewm_weight, opp_shrink_k) for a sport/stat: the override if one was validated, else defaults."""
    o = STAT_PARAMS.get(sport, {}).get(stat, {})
    return (o.get("halflife", HALFLIFE[sport]), o.get("ewm_weight", EWM_WEIGHT), o.get("opp_k", OPP_SHRINK_K))

MIN_GAMES = 4                                        # fewer prior games than this -> no projection
HISTORY_GAMES = 60
OPP_SHRINK_K = 60.0                                  # player-games of evidence that fully trusts an opponent factor
OPP_CLIP = (0.85, 1.15)
EWM_WEIGHT = 0.5                                     # weight of the plain EWM vs the usage-adjusted rate estimate
MIN_DISP_GAMES = 10
ALPHA_RANGE = (0.01, 1.5)
MIN_SD_FRAC = 0.15                                   # sd floor as a fraction of mu

CONFLUENCE_MIN_BOOKS = 3
BLEND_C = 0.10                                       # model weight (in logit space) of the blended posterior; see blend_p_over
TRAINED_KEY = "EVSweep/nhl_props/trained_latest.json"      # written by nhl.props.serve_trained (the scoring job)
TRAINED_MAX_AGE_H = 36.0


def trained_enabled() -> bool:
    """NHL_TRAINED_PROPS=1 serves the trained models' means / distributions where the scoring job has a prediction for the
    player's game; anything else (the default) leaves the board on the empirical engine."""
    import os
    return os.environ.get("NHL_TRAINED_PROPS", "").strip() in ("1", "true", "yes", "on")


def nba_skewed_points_enabled() -> bool:
    """NBA_SKEWED_POINTS=0/false/no/off (or empty) reverts NBA points pricing to the symmetric Normal; anything else
    (the default) prices points with the right-skewed 3 x threes + nonthree marginal from the PR #186 calibration
    study (see _nba_component, which only supplies the threes split while this is on, so every consumer falls back
    to the exact old Normal when it is off)."""
    import os
    return os.environ.get("NBA_SKEWED_POINTS", "1").strip().lower() not in ("0", "false", "no", "off", "")


def load_trained(now: datetime | None = None) -> dict:
    """{(player_id, game ET date, stat): (mu, alpha, version)} from TRAINED_KEY; {} when absent, unreadable or older than
    TRAINED_MAX_AGE_H (a stale file must never feed a board)."""
    now = now or datetime.now(timezone.utc)
    try:
        from mlb_core import storage
        payload = json.loads(storage.read_bytes(TRAINED_KEY))
        age = (now - datetime.fromisoformat(str(payload["ts"]).replace("Z", "+00:00"))).total_seconds() / 3600.0
        if age > TRAINED_MAX_AGE_H:
            log.warning("trained NHL props are %.1f h old (> %.0f): ignored", age, TRAINED_MAX_AGE_H)
            return {}
        v = payload.get("version")
        return {(int(r["player_id"]), str(r["date"])[:10], r["stat"]): (float(r["mu"]), float(r["alpha"]), v)
                for r in payload.get("rows", [])}
    except Exception as exc:                                                            # noqa: BLE001
        log.info("trained NHL props unavailable: %s", exc)
        return {}


# Per-sport / per-stat override of BLEND_C (stat missing -> the sport's "default" entry, else BLEND_C). Feeds the blended posterior
# `p_over_blend` and `blend_ev` (the Official-tier gate of ev.model_picks); it does NOT feed the edge, the confluence flag or the
# Beezy Score. PROVENANCE, stated per stat:
#   MEASURED on THIS engine (docs/analysis/nba_engine_vs_market_2026-11-01.md, 362,777 real BettingPros props, `ship_noabs` = the
#   shipped engine with no absence info, the live pricer's closest variant): NBA threes +0.19, rebounds +0.13, assists +0.09,
#   pra +0.02, points +0.02. NHL (docs/analysis/nhl_engine_vs_market_2026-11-01.md): goals +0.03, assists +0.06, points +0.03;
#   saves pooled +0.48 but regime-dependent (-0.23 / +0.84 / +0.19 by season), so it keeps the 0.15 prior.
#   NBA SUPERSEDED 2026-11-10 (Argon props direction D3): the five core NBA stats now carry the empirical WALK-FORWARD
#   values from that direction -- rebounds .14, threes .07, assists .03, points .06, pra .06 -- replacing the single-study
#   values above for those stats only. Assists' .03 is a blend weight, not eligibility: assists is OUT of the official
#   signal allowlist (ev.model_picks) under the same direction.
#   NOT measured (BettingPros carries no NBA pr / pa / ra / steals / blocks, no NHL blocks / pp_points line): NBA ra .11, pr .06,
#   pa .05, steals .05, blocks .05 and NHL blocked_shots .10, pp_points .05 are priors set at or below the components' measured c
#   (the 2026-11-02 review's values), NHL sog .30 is the TRAINED SOG model's measured c applied as a prior. NFL has no table: the
#   BLEND_C default applies (the one measured NFL c is rush_yds +0.25, t 2.9, from the hierarchical-engine real-line study).
BLEND_C_BY_SPORT_STAT = {
    "nba": {"threes": 0.07, "rebounds": 0.14, "assists": 0.03, "ra": 0.11, "pr": 0.06, "pa": 0.05, "pra": 0.06,
            "points": 0.06, "steals": 0.05, "blocks": 0.05},
    "nhl": {"sog": 0.30, "saves": 0.15, "blocked_shots": 0.10, "points": 0.03, "goals": 0.03, "assists": 0.06,
            "pp_points": 0.05},
}
# The same weight for NHL rows that were served from the TRAINED models (row `nhl_model == "trained_v1"`, only with NHL_TRAINED_PROPS on).
# MEASURED on those models (docs/analysis/nhl_trained_serving_2026-11-01.md, BettingPros 2022-10..2026-10, walk-forward refits): goals +0.37
# (t 5.8), assists +0.34 (t 6.9), points +0.175 (t 3.5); saves pooled +0.75 but +0.37 / +1.02 / +0.35 by season, so the weakest season's
# value is used. Information, not money: the skater markets' ROI5 at the best price is -0.2% / -0.3% / -1.5%.
BLEND_C_TRAINED = {"nhl": {"goals": 0.37, "assists": 0.34, "points": 0.175, "saves": 0.35}}
# Per-stat intercept / slope of the blend, logit(p) = a + b logit(mkt) + c (logit(model) - logit(mkt)) (what the model-vs-market studies
# FIT; blend_p_over's default a = 0, b = 1 is the c-only form). Empty = every stat keeps (0, 1). Filled only from a real refit
# (scripts/nba_engine_vs_market.py), never by hand.
BLEND_AB_BY_SPORT_STAT: dict = {}
BLEND_MAX_SHIFT = 0.08                                # the study's clamp: the blend never moves more than this from the market
# Books whose price is the reference the pack follows (an exchange / the sharpest offshore line). A row is `sharp_agrees` unless these
# books, left out the book we would bet at, price OUR side meaningfully lower than the whole pack does. UNVALIDATED as a filter.
SHARP_BOOKS = frozenset({"pinnacle", "novig", "prophetx"})
SHARP_TOLERANCE = 0.010
# ------------------------------------------------------------------------------------------------ NFL live-serving gate
# Argon model-improvement direction D3 (2026-11-10): the hierarchical NFL stats FAILED the vs-market benchmark
# (LL gain -0.0066..-0.0447) and are removed from LIVE SERVING. The two Argon texts name different stat sets --
# props D2: rec_yds, receptions, pass_yds, pass_tds; the verbatim Pack-4 audit surface: rush_yds (rushing yards),
# rush_attempts (rushing attempts), rec_yds (receiving yards), pass_tds -- and the shipped allowlist
# (nfl/props/hierarchical_allowlist.json) actually serves pass_tds / rec_yds / rush_yds today. The conservative
# reconciliation is an ENGINE gate, not a stat list: no NFL row priced by the hierarchical engine is published,
# whatever its stat and whatever $NFL_HIER_STATS says. The models and the offline rebuild / replay path are
# untouched; the engine may still RUN (its rows are dropped at board assembly and at the serving read path), so
# production is safe even while the daily-projections job env sets NFL_ENGINE=hierarchical. Legacy-engine NFL rows
# are not hierarchical rows and are not touched by this gate. NOTE: the board-fed PROPS paper ledger
# (ev.model_picks) reads this same published board, so hierarchical rows stop entering it too; the separate
# Kalshi/Novig shadow executors (ev.props_exec, ev.novig) do not read this board and are unaffected.
NFL_HIER_AUDIT_STATS = frozenset({"rush_yds", "rush_attempts", "rec_yds", "pass_tds", "receptions", "pass_yds"})


def is_hierarchical_nfl_row(r: dict) -> bool:
    """True for an NFL board row priced by the hierarchical engine (row `engine` carries the hierarchical id)."""
    return str(r.get("sport") or "") == "nfl" and "hierarchical" in str(r.get("engine") or "")


def live_served_rows(rows: list) -> list:
    """The rows allowed on the published board: `rows` minus hierarchical NFL rows (see the gate above). Pure."""
    return [r for r in rows if not is_hierarchical_nfl_row(r)]


# -------------------------------------------------------------------------------- NHL trained-skater shadow cohort (D4)
# Argon props direction D4 (2026-11-10): trained skater goals / assists / points are OUT of standalone official
# eligibility (ev.model_picks), display-only. Goals + assists ONLY get a SHADOW cohort: a row enters it when an
# exchange (Novig / Kalshi) or soft-book (Fliff / Betr) two-sided quote on its line carries hold < 4.5%. Points
# has NO cohort. Judged 2026-12-28 or n=150: promote iff control-adjusted ROI >= +2.0% and mean CLV >= +1.5%
# (the judging constants live with the paper ledger in ev.model_picks). These sets mirror ev.model_picks'
# NHL_SHADOW_* constants; test_argon_d3_d4_gates asserts they stay equal.
NHL_SHADOW_STATS = frozenset({"goals", "assists"})
NHL_SHADOW_EXCHANGE_BOOKS = frozenset({"novig", "kalshi"})
NHL_SHADOW_SOFT_BOOKS = frozenset({"fliff", "betr"})
NHL_SHADOW_BOOKS = NHL_SHADOW_EXCHANGE_BOOKS | NHL_SHADOW_SOFT_BOOKS
NHL_SHADOW_MAX_HOLD = 0.045


def _quote_book_prices(q) -> tuple:
    """(book, over_american, under_american) from a Quote object or a quote dict; (None, None, None) if unusable."""
    if isinstance(q, dict):
        book, over, under = q.get("book"), q.get("over"), q.get("under")
        over = q.get("over_odds", over) if over is None else over
        under = q.get("under_odds", under) if under is None else under
    else:
        book, over, under = getattr(q, "book", None), getattr(q, "over", None), getattr(q, "under", None)
    try:
        return (str(book).strip().lower(), int(over), int(under)) if book and over is not None and under is not None else (None, None, None)
    except (TypeError, ValueError):
        return None, None, None


def nhl_shadow_cohort_fields(stat: str, trained: bool, quotes) -> dict:
    """Shadow-cohort stamp for one board row: {'shadow_cohort', 'shadow_book', 'shadow_hold', 'shadow_source'}.

    Only a TRAINED skater goals/assists row can enter, and only on a two-sided quote from an exchange / soft book
    in NHL_SHADOW_BOOKS whose own hold (implied P(over) + implied P(under) - 1) is < NHL_SHADOW_MAX_HOLD. The
    lowest-hold qualifying quote wins. Anything else stamps shadow_cohort=False with Nones."""
    out = {"shadow_cohort": False, "shadow_book": None, "shadow_hold": None, "shadow_source": None}
    if not trained or stat not in NHL_SHADOW_STATS:
        return out
    from mlb_core.odds.utils import american_to_implied_prob
    best = None
    for q in quotes or []:
        book, over, under = _quote_book_prices(q)
        if book is None or book not in NHL_SHADOW_BOOKS:
            continue
        hold = american_to_implied_prob(over) + american_to_implied_prob(under) - 1.0
        if hold < NHL_SHADOW_MAX_HOLD and (best is None or hold < best[1]):
            best = (book, hold)
    if best is not None:
        out.update(shadow_cohort=True, shadow_book=best[0], shadow_hold=round(float(best[1]), 5),
                   shadow_source="exchange" if best[0] in NHL_SHADOW_EXCHANGE_BOOKS else "soft_book")
    return out
# NBA engine feature switches, consulted at projection time; `nba_features(...)` flips them for a replay. A switch is ON only
# where nba/props/engine_eval.py showed a held-out gain past its bar (selected on 2023-24, scored on 2024-25 / 2025-26:
# docs/analysis/nba_engine_walkforward_2026-11-01.md): eta_scale 0.4 with steals / blocks excluded (the spec's 0.55-0.65
# elasticities were ~4x too strong) and shot_gamma 0.5. Rejected on that evidence and left OFF: dvp_k (positional DvP),
# pool_gamma (missed-shot-pool rebounds), zinb (zero-inflated steals / blocks).
#   dvp_k       shrinkage k of the (opponent, position-group) factor; None = team-wide factor only
#   pool_gamma  exponent on the missed-shot-pool ratio (rebounds); 0 = off
#   shot_gamma  exponent on the team-makes ratio (assists); 0 = off
#   zinb        zero-inflated NegBin steals / blocks
#   eta_scale / eta_off  multiplier on the absence-redistribution elasticities, and the stats that get none
#   trk_ast / trk_reb  exponent on the PRIOR-season tracking ratio (potential assists / rebound chances luck removal;
#                      nba.data.tracking); 0 = off. Needs GameLogs.nba_tracking, else no effect.
NBA_FEATURES = {"dvp_k": None, "pool_gamma": 0.0, "shot_gamma": 0.5, "zinb": False, "eta_scale": 0.4,
                "eta_off": ("steals", "blocks"), "trk_ast": 0.0, "trk_reb": 0.0}


@contextlib.contextmanager
def nba_features(**kw):
    unknown = set(kw) - set(NBA_FEATURES)
    if unknown:
        raise KeyError(f"unknown NBA feature switch(es): {sorted(unknown)}")
    old = dict(NBA_FEATURES)
    NBA_FEATURES.update(kw)
    try:
        yield
    finally:
        NBA_FEATURES.clear()
        NBA_FEATURES.update(old)


NBA_MODEL = "hier_v1"                                # NBA rows: role redistribution + minutes + attempt-first threes + coherent combos
EDGE_CAP = 0.15                                      # larger apparent edges are reference/model error (winner's curse)
MAX_AGE_MIN = 240.0                                  # banked lines older than this are not live: they are flagged stale
# The odds bankers run twice a day (17:30 / 22:30 UTC), so between the evening snapshot and the next afternoon one every
# line is older than MAX_AGE_MIN. Hiding them left every row empty for ~15 hours a day; lines up to this age are still
# shown, with payload `lines_stale: true` and the age, so the page says plainly how old the price is. Older than this
# (a missed banking run) they are dropped again.
CARRY_MAX_AGE_MIN = 1200.0
# How far ahead the rotation (no-line) rows and the `games` list reach. NFL is a weekly slate, so a week; NHL plays most
# nights but the books price ~2 days out, so 3 days; the NBA season opens ~4 days after this was written (2026-10-20) and the
# books already post its props, so a 48 h window left an empty board until the night before: 7 days.
HORIZON_HOURS = {"nfl": 168.0, "nhl": 72.0, "nba": 168.0}
STALE_GAMELOG_DAYS = 150
# Rotation rows per team per stat, counting the team's PRICED players in that stat toward the quota (a team the books
# already cover well gets no rotation filler). A 7-day NBA window is ~40 games: 9 stats x 4 x 2 teams = 72 rows a game.
ROTATION_PER_TEAM = {"nfl": 5, "nhl": 4, "nba": 4}
MAX_ROWS = 2400                                      # stored payload cap; rows are ordered so the cut drops the FARTHEST games

# usage thresholds that define a "rotation" player (avg over the last 10 games)
ROTATION_MIN_USAGE = {"nfl": {"rec_yds": 4.0, "receptions": 4.0, "rush_yds": 8.0, "pass_yds": 15.0,
                              "pass_attempts": 15.0, "pass_completions": 15.0, "rush_attempts": 8.0},
                      "nhl": {"sog": 14.0, "goals": 14.0, "points": 14.0, "assists": 14.0, "saves": 10.0,
                              "blocked_shots": 14.0, "pp_points": 14.0},
                      "nba": {"points": 20.0, "rebounds": 20.0, "assists": 20.0, "threes": 20.0, "pra": 20.0,
                              "pr": 20.0, "pa": 20.0, "ra": 20.0, "steals": 20.0, "blocks": 20.0}}
ROTATION_STATS = {"nfl": ("rec_yds", "receptions", "rush_yds", "pass_yds", "pass_attempts", "pass_completions",
                          "rush_attempts"),
                  # pp_points has NO sportsbook line on any feed we hold (probed 2026-11-02: ParlayAPI serves player_power_play_points
                  # only at the DFS apps sleeper / underdog, which are excluded as price sources; BettingPros and BestBook carry none), so
                  # the stat only ever reaches the board as a projection-only rotation row
                  "nhl": ("sog", "points", "saves", "blocked_shots", "pp_points"),
                  "nba": ("points", "rebounds", "assists", "pra", "pr", "pa", "ra", "steals", "blocks")}

_NFL_CANON = {"JAX": "JAC", "LA": "LAR", "STL": "LAR", "OAK": "LV", "SD": "LAC", "WSH": "WAS"}


# --------------------------------------------------------------------------------------------------- small helpers
def _f(x, nd: int = 4):
    """JSON-safe float (NaN / inf / None -> None)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def canon_team(sport: str, x) -> str | None:
    """Any team spelling (name / abbreviation) -> the sport's one canonical code (None if unknown)."""
    s = (str(x).strip() if x is not None else "")
    if not s or s.lower() == "nan":
        return None
    if sport == "nfl":
        up = s.upper()
        if len(up) <= 4:
            return _NFL_CANON.get(up, up)
        from ev.sports.nfl import TEAM_NAMES
        return TEAM_NAMES.get(s)
    if sport == "nhl":
        from nhl.config import team_abbrev
        return team_abbrev(s)
    from nba.props.data import sb_team
    return sb_team(s)


def beezy_score(edge: float | None, n_books: int | None, p_hit: float | None) -> int:
    """Python twin of beezy-vip/lib/beezy-score.ts scoreParts() (v2). `edge` is a FRACTION (0.08 = 8%)."""
    edge_pct = max(float(edge or 0.0), 0.0) * 100.0
    e = min(edge_pct * 4.0, 60.0)
    cons_raw = 10.0 if n_books is None else 20.0 * min(max((n_books - 3) / 9.0, 0.0), 1.0)
    hit_raw = 20.0 * min(max(((p_hit or 0.0) - 0.15) / 0.35, 0.0), 1.0)
    scale = min(edge_pct / 2.0, 1.0)
    return int(round(min(max(e + (cons_raw + hit_raw) * scale, 0.0), 100.0)))


# ------------------------------------------------------------------------------------------------------ calibration
_CALIB: dict | None = None


def calibration(sport: str, stat: str) -> dict | None:
    """The shipped calibration coefficients for an NFL stat ({"k"} or {"b0","b1","b2"}), else None. Loaded once."""
    global _CALIB
    if sport != "nfl":
        return None
    if _CALIB is None:
        _CALIB = _ea.load_calibration()
    return _CALIB.get(stat)


class EnvTuple(tuple):
    """2-tuple (implied_total, team_spread) that also exposes wind, roof, and temp attributes."""
    def __new__(cls, implied_total, team_spread, wind=None, roof=None, temp=None, season=None, week=None, home=None):
        return super().__new__(cls, (float(implied_total), float(team_spread)))

    def __init__(self, implied_total, team_spread, wind=None, roof=None, temp=None, season=None, week=None, home=None):
        self.implied_total = float(implied_total)
        self.team_spread = float(team_spread)
        self.wind = wind
        self.roof = roof
        self.temp = temp
        self.season = None if season is None or season != season else int(season)    # nflverse season / week of the game
        self.week = None if week is None or week != week else int(week)
        self.home = home                                                              # canonical HOME club (its stadium)


def _load_env_lines() -> dict:
    try:
        from nfl.data.read import load
        cols = ["game_id", "season", "week", "gameday", "home_team", "away_team", "spread_line", "total_line"]
        try:
            sched = load("schedules", columns=cols + ["roof", "wind", "temp"])
        except Exception:
            sched = load("schedules", columns=cols)
        t = _ea.game_env_table(sched)
    except Exception as exc:                                                            # noqa: BLE001
        log.info("nfl schedule lines unavailable (%s): game term falls back to the scale", exc)
        return {}
    t = t[t["implied_total"].notna() & t["team_spread"].notna()]
    has_w = "wind" in t.columns
    has_r = "roof" in t.columns
    has_tp = "temp" in t.columns
    return {
        (tm, str(gd)[:10]): EnvTuple(
            it, sp,
            wind=float(w) if has_w and pd.notna(w) else None,
            roof=str(r) if has_r and pd.notna(r) else None,
            temp=float(tp) if has_tp and pd.notna(tp) else None,
            season=se, week=wk, home=hm,
        )
        for tm, gd, it, sp, w, r, tp, se, wk, hm in zip(
            t["team"], t["gameday"], t["implied_total"], t["team_spread"],
            t["wind"] if has_w else [None] * len(t),
            t["roof"] if has_r else [None] * len(t),
            t["temp"] if has_tp else [None] * len(t),
            t["season"] if "season" in t.columns else [None] * len(t),
            t["week"] if "week" in t.columns else [None] * len(t),
            t["home_team"] if "home_team" in t.columns else [None] * len(t),
        )
    }


# ------------------------------------------------------------------------------------------------------ game logs
@dataclass
class StatContext:
    """League-level facts for one stat, derived once from the master (not per player)."""
    opp_factor: dict = field(default_factory=dict)      # canonical opponent code -> multiplier
    opp_raw: dict = field(default_factory=dict)         # canonical opponent code -> UNSHRUNK allowed/expected ratio
    opp_cnt: dict = field(default_factory=dict)         # canonical opponent code -> player-games behind that ratio
    alpha: float = 0.25                                  # NegBin dispersion (var = mu + alpha mu^2)
    cv: float = 0.5                                      # within-player sd / mean (Normal family)
    nba_opp_pos_raw: dict = field(default_factory=dict)     # (opponent, position group) -> UNSHRUNK allowed/expected ratio (NBA)
    nba_opp_pos_cnt: dict = field(default_factory=dict)     # (opponent, position group) -> player-games behind that ratio
    nba_zi_pi: float = 0.0                                   # NBA steals / blocks: structural-zero probability (0 = not inflated)
    nba_zi_alpha: float | None = None                        # NBA steals / blocks: NegBin dispersion fitted jointly with nba_zi_pi
    league_mean: float = 0.0
    n_players: int = 0


def _hist(rows: list, stat_def: tuple) -> list:
    """Chronological player rows -> [(val, usage|None, season, opp_raw)] (rows with no usable value dropped)."""
    val_cols, use_cols, _ = stat_def
    out = []
    for r in rows:
        v = cf._sum_cols(r, val_cols)
        if v is None:
            continue
        s = r.get("season") if r.get("season") is not None else r.get("season_year")
        out.append((v, cf._sum_cols(r, use_cols), s if s == s else None, r.get("_opp")))
    return out[-HISTORY_GAMES:]


class GameLogs:
    """A sport's player game-log index (cockpit_form shape) plus lazily built per-stat league contexts."""

    def __init__(self, sport: str, idx: dict, opp_shrink_k: float | None = None):
        self.sport = sport
        self.idx = idx
        self.opp_shrink_k = opp_shrink_k          # None = the per-stat validated value (stat_params)
        self._ctx: dict = {}
        self._env: dict | None = None
        self._nba_teams: dict | None = None       # team -> {player_norm: rows}, built lazily (nba_team_rows)
        self._nba_team_games = None                   # nba.props.team_context.TeamGames, built lazily (nba_team_games)
        self.nba_tracking = None                      # nba.data.tracking.Tracking (prior-season tables) or None
        self._nba_roles: dict = {}                # (team, as_of) -> {player_norm: {stat: multiplier}}
        self._spreads: dict = {}                  # NBA game_key -> |consensus spread| (set_nba_spreads)
        self.nba_replay = False                       # True: replaying a PAST board -> NBA history is cut before the tip's UTC day
        self._latest: object = _AUTO
        self._pace: dict = {}
        self._by_team: dict = {}
        self._vac: dict = {}
        self._sv: dict = {}
        self._tracker = None
        self._trained: dict = {}
        self._saves_history = None                   # settled saves rows for the D1 hardening state (set_saves_history)
        self._saves_states: dict = {}
        self._opp_g: dict = {}
        self._lock = threading.Lock()

    @classmethod
    def load(cls, sport: str) -> "GameLogs | None":
        loader = cf._LOADERS.get(sport)
        if loader is None:
            return None
        idx = cf._cached(f"form:{sport}", loader)         # same 10-minute cache the /edge cockpit uses
        return cls(sport, idx) if idx else None

    # -- tables ---------------------------------------------------------------------------------------------
    def table_and_def(self, stat: str):
        if self.sport == "nfl":
            return self.idx["all"], cf.NFL_STATS.get(stat)
        if self.sport == "nba":
            return self.idx["all"], cf.NBA_STATS.get(stat)
        if stat in cf.NHL_GOALIE_STATS:
            return self.idx.get("goalie") or {}, cf.NHL_GOALIE_STATS[stat]
        return self.idx.get("skater") or {}, cf.NHL_SKATER_STATS.get(stat)

    def rows_for(self, stat: str, player_norm: str):
        table, sdef = self.table_and_def(stat)
        if sdef is None:
            return None, None
        return table.get(player_norm), sdef

    def team_of(self, rows: list) -> str | None:
        last = rows[-1]
        raw = (last.get("recent_team") or last.get("team")) if self.sport == "nfl" else last.get("team_abbr")
        return canon_team(self.sport, raw)

    def env_lines(self) -> dict:
        """{(canonical team, ET kickoff date): (implied_team_total, team_spread, ...)} from the mirrored schedules, NFL only
        ({} for other sports or when the mirror is unreadable: the game term then falls back to the scale alone)."""
        if self.sport != "nfl":
            return {}
        with self._lock:
            if self._env is None:
                self._env = _load_env_lines()
            return self._env

    def nfl_team_ctx(self) -> dict:
        """Per-team trailing pass attempts / carries / targets and per-player shares (hierarchical engine), built once."""
        with self._lock:
            if getattr(self, "_nfl_team", None) is None:
                try:
                    self._nfl_team = _hl.build_team_ctx(self.idx["all"]) if self.sport == "nfl" else {}
                except Exception as exc:                                                # noqa: BLE001
                    log.warning("nfl team context failed (%s): hierarchical engine falls back", exc)
                    self._nfl_team = {}
            return self._nfl_team

    def nfl_live(self):
        """The optional injury / pbp / participation layers (hierarchical engine), loaded once and never raising.
        $NFL_HIER_LAYERS = 'none' (default: nothing is loaded -- the layers failed their ablation) | 'all' | 'absence,shell,...'."""
        with self._lock:
            if getattr(self, "_nfl_live", None) is None:
                import os
                self._nfl_live = False
                raw = os.environ.get("NFL_HIER_LAYERS", "none").strip().lower()
                layers = () if raw in ("", "none") else (_hl.LAYERS if raw == "all" else
                                                         tuple(x.strip() for x in raw.split(",") if x.strip() in _hl.LAYERS))
                if self.sport == "nfl" and layers:
                    try:
                        newest = max((r[-1].get("season") or 0) for r in self.idx["all"].values() if r)
                        self._nfl_live = _hl.LiveContext.load(
                            int(newest), with_pbp=os.environ.get("NFL_HIER_CONTEXT", "1") != "0" and layers != ("absence",),
                            layers=layers)
                    except Exception as exc:                                            # noqa: BLE001
                        log.warning("nfl live context failed (%s): neutral context", exc)
            return self._nfl_live or None

    def nba_absences(self) -> dict:
        """{team_code: {player_norm: P(absent)}} from the latest NBA injury report."""
        if self.sport != "nba":
            return {}
        with self._lock:
            if not hasattr(self, "_nba_abs") or self._nba_abs is None:
                self._nba_abs = {}
                try:
                    from nba.data import espn_injuries
                    from mlb_core.data.player_names import norm_name
                    frame = espn_injuries.fetch_injuries()
                    if frame.attrs.get("ok", False):
                        out = {}
                        for code, p, st in zip(frame["team_abbrev"], frame["player"], frame["status"]):
                            w = float(espn_injuries.STATUS_ABSENCE_PROB.get(st, 0.0))
                            if w > 0:
                                out.setdefault(code, {})[norm_name(p)] = w
                        self._nba_abs = out
                except Exception as e:
                    log.info("nba injuries fetch failed: %s", e)
            return self._nba_abs

    def set_nba_spreads(self, spreads: dict | None) -> None:
        """{game_key: |consensus point spread|} for the minutes model's blowout term. Unset = no spread = no blowout trim."""
        with self._lock:
            self._spreads = dict(spreads or {})

    def nba_spread(self, game_key: str | None) -> float | None:
        v = self._spreads.get(game_key)
        return None if v is None else float(v)

    def set_nba_tracking(self, tracking) -> None:
        """Prior-season player tracking for the assist / rebound luck-removal factors (None = none loaded)."""
        with self._lock:
            self.nba_tracking = tracking

    def nba_team_games(self):
        """The per-team game table (missed-shot pool / team makes) behind the rebound and assist environment factors."""
        with self._lock:
            if self._nba_team_games is None:
                self._nba_team_games = _tc.TeamGames(self.idx.get("all") or {})
            return self._nba_team_games

    def nba_team_rows(self, team: str) -> dict:
        """{player_norm: chronological rows} of the players whose LATEST game was for `team` and within 45 days of the
        newest game in the master (a long-term absentee is not vacating anything tonight)."""
        if self.sport != "nba":
            return {}
        with self._lock:
            if self._nba_teams is None:
                table = self.idx.get("all") or {}
                newest = max((rows[-1].get("_sort") or "" for rows in table.values() if rows), default="")
                try:
                    cutoff = (datetime.fromisoformat(str(newest)[:10]) - timedelta(days=45)).date().isoformat()
                except ValueError:
                    cutoff = ""
                teams: dict = {}
                for name, rows in table.items():
                    if str(name).startswith("id:") or not rows:
                        continue
                    if str(rows[-1].get("_sort") or "")[:10] < cutoff:
                        continue
                    t = canon_team("nba", rows[-1].get("team_abbr"))
                    if t:
                        teams.setdefault(t, {})[name] = rows
                self._nba_teams = teams
            return self._nba_teams.get(team, {})

    def nba_role_boosts(self, team: str | None, as_of: str | None) -> dict:
        """{player_norm: {stat: multiplier}} for the active rotation of `team` given tonight's injury-report absences
        (nba.props.hierarchical.compute_vacated_shares). {} when nobody on the team is listed or the team is unknown."""
        if self.sport != "nba" or not team:
            return {}
        absences = self.nba_absences().get(team, {})
        if not absences:
            return {}
        key = (team, as_of, tuple(sorted(absences.items())), NBA_FEATURES["eta_scale"], NBA_FEATURES["eta_off"])
        with self._lock:
            hit = self._nba_roles.get(key)
        if hit is None:
            hit = _hier.compute_vacated_shares(self.nba_team_rows(team), absences, as_of,
                                               eta_scale=NBA_FEATURES["eta_scale"], eta_off=NBA_FEATURES["eta_off"])["boost"]
            with self._lock:
                self._nba_roles[key] = hit
        return hit

    def latest_date(self) -> str | None:
        """Newest game date in the master (YYYY-MM-DD; NFL: 'season Wweek')."""
        best = None
        table = self.idx.get("all") or self.idx.get("skater") or {}
        for rows in table.values():
            d = rows[-1].get("_sort")
            if d is not None and (best is None or d > best):
                best = d
        if self.sport == "nfl" and best is not None:
            return f"{int(best) // 100} W{int(best) % 100}"
        return str(best)[:10] if best is not None else None

    # -- league context -------------------------------------------------------------------------------------
    def _eff_as_of(self, as_of: str | None) -> str | None:
        """The as-of date that actually has to cut the logs: NHL only, and only when the master holds games on or after it
        (the live board's games are all in the future, so nothing is cut and the cached whole-master contexts are used)."""
        if not as_of or self.sport != "nhl":
            return None
        with self._lock:
            if self._latest is _AUTO:
                best = None
                for tbl in (self.idx.get("skater") or {}, self.idx.get("goalie") or {}):
                    for rows in tbl.values():
                        d = rows[-1].get("_sort")
                        if d is not None and (best is None or str(d) > best):
                            best = str(d)
                self._latest = None if best is None else best[:10]
            latest = self._latest
        return as_of if latest is not None and as_of <= latest else None

    def context(self, stat: str, as_of: str | None = None) -> StatContext:
        """League context of `stat`. `as_of` (NHL, YYYY-MM-DD) rebuilds it from games strictly before that date."""
        as_of = self._eff_as_of(as_of)
        with self._lock:
            ctx = self._ctx.get((stat, as_of))
            if ctx is None:
                ctx = self._ctx[(stat, as_of)] = self._build_context(stat, as_of)
            return ctx

    def team_pace(self, as_of: str | None = None) -> dict:
        """NHL {team: {sog_for, sog_against, sa_ewm, n, ...}, '_league': {...}} over each team's trailing games before
        `as_of` (nhl.props.hierarchical.team_pace); {} without the team box master."""
        if self.sport != "nhl":
            return {}
        as_of = self._eff_as_of(as_of)
        with self._lock:
            if as_of not in self._pace:
                self._pace[as_of] = _nh.team_pace(self.idx.get("team") or {}, as_of,
                                                  canon=lambda ab: canon_team("nhl", ab))
            return self._pace[as_of]

    def league_sv(self, as_of: str | None = None) -> float | None:
        """League save % over every goalie's last 30 appearances before `as_of` that fall within 400 days of it (None below
        5,000 shots): the prior a goalie's save % is shrunk to. It drifts (.908 in 2021 -> .897 in 2025), so a fixed
        constant would bias every saves projection by a fraction of a save."""
        if self.sport != "nhl":
            return None
        eff = self._eff_as_of(as_of)
        with self._lock:
            if eff not in self._sv:
                ref = eff or self.latest_date() or ""
                lo = (datetime.fromisoformat(ref) - timedelta(days=400)).date().isoformat() if ref else ""
                sv = sa = 0.0
                for name, rows in (self.idx.get("goalie") or {}).items():
                    if str(name).startswith("id:"):
                        continue
                    for r in _nh.before(rows, eff)[-_nh.SV_WINDOW:]:
                        d = str(r.get("_date") or "")[:10]
                        s_, a_ = _nh._num(r.get("saves")), _nh._num(r.get("shots_against"))
                        if d >= lo and s_ is not None and a_ is not None:
                            sv, sa = sv + s_, sa + a_
                self._sv[eff] = sv / sa if sa >= 5000 else None
            return self._sv[eff]

    def set_trained(self, preds: dict) -> None:
        """Install {(player_id, ET game date, stat): (mu, alpha, version)} (see load_trained)."""
        self._trained = preds or {}

    def set_saves_history(self, rows) -> None:
        """Install settled saves history rows (nhl.props.saves_hardening history shape) for the D1
        centering / calibration / regime state. Per-date HardeningStates are built lazily and cached."""
        self._saves_history = list(rows or [])
        self._saves_states = {}

    def saves_state(self, as_of: str | None):
        """The HardeningState for a game date, or None when no history was installed (unit tests, a
        ledger outage): centering / calibration then leave the probability untouched, while the
        starter and price stamps are still written (see _saves_hardening_fields)."""
        if getattr(self, "_saves_history", None) is None or not as_of:
            return None
        cache = getattr(self, "_saves_states", None)
        if cache is None:
            cache = self._saves_states = {}
        if as_of not in cache:
            try:
                cache[as_of] = _sh.build_state(self._saves_history, as_of)
            except Exception as exc:                                                # noqa: BLE001
                log.info("saves hardening state unavailable for %s: %s", as_of, exc)
                cache[as_of] = None
        return cache[as_of]

    def trained_pred(self, player_id, as_of: str | None, stat: str):
        if self.sport != "nhl" or not self._trained or player_id is None or not as_of:
            return None
        try:
            return self._trained.get((int(player_id), as_of, stat))
        except (TypeError, ValueError):
            return None

    def goalie_tracker(self):
        """nhl.game.goalie_model.GoalieTracker over every goalie appearance in the master, recorded in GLOBAL date order (the
        tracker's team start lists assume it). Every query below passes `before_date`, so it is as-of safe."""
        with self._lock:
            if self._tracker is None:
                from nhl.game.goalie_model import GoalieTracker
                tr = GoalieTracker()
                rows = [r for name, rs in (self.idx.get("goalie") or {}).items() if not str(name).startswith("id:")
                        for r in rs]
                rows.sort(key=lambda r: str(r.get("_date") or ""))
                for r in rows:
                    toi, sa, ga = (_nh._num(r.get(k)) for k in ("toi_sec", "shots_against", "goals_against"))
                    if toi is None or sa is None or ga is None or toi <= 0:
                        continue
                    tr.record_start(str(r.get("player_id")), str(r.get("name")), str(r.get("team_abbr")),
                                    str(r.get("_date"))[:10], str(r.get("game_id")), toi / 60.0, sa, ga)
                self._tracker = tr
            return self._tracker

    def opp_goalie_gsax(self, team: str | None, as_of: str | None) -> float | None:
        """Shrunk GSAx/60 (goals saved above the league's expected starters, per 60) of the goalie `team` is expected to start
        on `as_of`: GoalieTracker.project_team_goalie (recent-starts share, back-to-back backup inference), centred on the
        mean over every team so a league-wide save % drift is not read as a goalie effect. None when unknown."""
        if self.sport != "nhl" or team is None or not as_of:
            return None
        tr = self.goalie_tracker()
        key = (team, as_of)
        with self._lock:
            if key in self._opp_g:
                return self._opp_g[key]
        mean = self._gsax_center(as_of, tr)
        t = tr.team_starts and tr.project_team_goalie(team, as_of)
        val = None if not t or t.starter_id is None else float(t.projected_gsax_60) - mean
        with self._lock:
            self._opp_g[key] = val
        return val

    def _gsax_center(self, as_of: str, tr) -> float:
        key = ("_center", as_of)
        with self._lock:
            if key in self._opp_g:
                return self._opp_g[key]
        vals = [tr.project_team_goalie(t, as_of) for t in tr.team_starts]
        vals = [v.projected_gsax_60 for v in vals if v.starter_id is not None]
        c = float(np.mean(vals)) if vals else 0.0
        with self._lock:
            self._opp_g[key] = c
        return c

    def _players_by_team(self, as_of: str | None) -> dict:
        """{team: [(player_norm, position, last <=20 rows before as_of)]} keyed by each player's latest team as of then."""
        out = self._by_team.get(as_of)
        if out is None:
            out = {}
            for name, rows in (self.idx.get("skater") or {}).items():
                if str(name).startswith("id:") or not rows:
                    continue
                cut = _nh.before(rows, as_of)[-20:]
                if not cut:
                    continue
                team = canon_team("nhl", cut[-1].get("team_abbr"))
                if team:
                    out.setdefault(team, []).append((name, cut[-1].get("position"), cut))
            self._by_team[as_of] = out
        return out

    def nhl_vacated(self, team: str | None, as_of: str | None, roster, extra_absent=()) -> dict:
        """{player_norm: {sog, assists, toi}} handed to this team's replacements tonight (hierarchical.vacated_opportunity);
        {} without a roster layer (nobody can be called absent) or when nobody top-line is off the roster."""
        if self.sport != "nhl" or team is None or (roster is None and not extra_absent):
            return {}
        eff = self._eff_as_of(as_of)
        key = (team, eff, id(roster), tuple(sorted(extra_absent)))
        with self._lock:
            if key not in self._vac:
                def on_roster(nm):
                    if roster is None:                       # explicit absences only: everyone else is presumed active
                        return True
                    state, rteam = roster.lookup(nm, team)
                    return (rteam == team) if state == "on" else (False if state == "off" else None)
                self._vac[key] = _nh.vacated_opportunity(self._players_by_team(eff).get(team, []), on_roster,
                                                         extra_absent, eff)
            return self._vac[key]

    def _build_context(self, stat: str, as_of: str | None = None) -> StatContext:
        table, sdef = self.table_and_def(stat)
        ctx = StatContext()
        if sdef is None or not table:
            return ctx
        got: dict = {}
        exp: dict = {}
        cnt: dict = {}
        pgot: dict = {}
        pexp: dict = {}
        pcnt: dict = {}
        zi_pairs: list = []
        var_sum = mean_sq_sum = mean_sum = 0.0
        n_players = 0
        for name, rows in table.items():
            if str(name).startswith("id:") or len(rows) < MIN_GAMES:
                continue
            h = _hist(_nh.before(rows, as_of), sdef)
            if len(h) < MIN_GAMES:
                continue
            cur = h[-1][2]
            season = [g for g in h if g[2] == cur] or h
            base = sum(g[0] for g in season) / len(season)
            if base <= 0:
                continue
            grp = _nbacfg.POS_GROUP.get(str(rows[-1].get("position") or "").upper()) if self.sport == "nba" else None
            if self.sport == "nba" and stat in ("steals", "blocks"):
                zi_pairs.extend((g[0], base) for g in season)
            for v, _, s, opp in season:
                oc = canon_team(self.sport, opp)
                if oc is None:
                    continue
                got[oc] = got.get(oc, 0.0) + v
                exp[oc] = exp.get(oc, 0.0) + base
                cnt[oc] = cnt.get(oc, 0) + 1
                if grp is not None:
                    pgot[(oc, grp)] = pgot.get((oc, grp), 0.0) + v
                    pexp[(oc, grp)] = pexp.get((oc, grp), 0.0) + base
                    pcnt[(oc, grp)] = pcnt.get((oc, grp), 0) + 1
            recent = [g[0] for g in h[-20:]]
            if len(recent) >= MIN_DISP_GAMES:
                m = sum(recent) / len(recent)
                if m > 0:
                    var_sum += float(np.var(recent, ddof=1))
                    mean_sq_sum += m * m
                    mean_sum += m
                    n_players += 1
        k_opp = self.opp_shrink_k if self.opp_shrink_k is not None else stat_params(self.sport, stat)[2]
        for oc, e in exp.items():
            if e > 0:
                w = cnt[oc] / (cnt[oc] + k_opp)
                ctx.opp_factor[oc] = min(max(1.0 + (got[oc] / e - 1.0) * w, OPP_CLIP[0]), OPP_CLIP[1])
                ctx.opp_raw[oc] = got[oc] / e
                ctx.opp_cnt[oc] = cnt[oc]
        for key, e in pexp.items():
            if e > 0:
                ctx.nba_opp_pos_raw[key] = pgot[key] / e
                ctx.nba_opp_pos_cnt[key] = pcnt[key]
        if len(zi_pairs) >= 500:
            ctx.nba_zi_pi, ctx.nba_zi_alpha = _fit_zinb(zi_pairs)
        if n_players and mean_sq_sum > 0:
            # method of moments over the whole population: var = mu + alpha*mu^2 (NegBin), var = (cv*mu)^2 (Normal)
            ctx.alpha = min(max((var_sum - mean_sum) / mean_sq_sum, ALPHA_RANGE[0]), ALPHA_RANGE[1])
            ctx.cv = min(max(math.sqrt(var_sum / mean_sq_sum), 0.1), 2.0)
            ctx.league_mean = mean_sum / n_players
        ctx.n_players = n_players
        return ctx


def _fit_zinb(pairs: list) -> tuple:
    """(pi, alpha) of a zero-inflated NegBin by maximum likelihood over (observed count, the player's mean) pairs: each
    count ~ pi*0 + (1-pi)*NB(mean/(1-pi), alpha), so the player's mean is preserved. pi < 0.01 -> 0 (not inflated)."""
    from scipy.optimize import minimize
    from scipy.special import expit
    v = np.array([p[0] for p in pairs], dtype=float)
    base = np.array([p[1] for p in pairs], dtype=float)
    zero = v == 0

    def nll(theta):
        alpha, pi = math.exp(theta[0]), float(expit(theta[1]))
        n = 1.0 / alpha
        m = base / (1.0 - pi)
        pmf = nbinom.pmf(v, n, n / (n + m))
        like = np.where(zero, pi + (1.0 - pi) * pmf, (1.0 - pi) * pmf)
        return float(-np.sum(np.log(np.maximum(like, 1e-300))))

    best = minimize(nll, np.array([math.log(0.3), -3.0]), method="Nelder-Mead", options={"maxiter": 120, "xatol": 1e-3,
                                                                                      "fatol": 1e-3})
    alpha, pi = math.exp(float(best.x[0])), float(expit(best.x[1]))
    alpha = min(max(alpha, ALPHA_RANGE[0]), ALPHA_RANGE[1])
    return (pi if pi >= 0.01 else 0.0), alpha


# ------------------------------------------------------------------------------------------- projection + P(over)
def project_mu(hist: list, halflife: float, opp_factor: float = 1.0, ewm_weight: float | None = None) -> dict | None:
    """Chronological [(val, usage, season, opp)] -> {'mu', 'n_games', 'usage_proj', 'opp_factor'} or None (too little)."""
    n = len(hist)
    if n < MIN_GAMES:
        return None
    vals = np.array([g[0] for g in hist], dtype=float)
    use = np.array([np.nan if g[1] is None else g[1] for g in hist], dtype=float)
    w = 0.5 ** ((n - 1 - np.arange(n)) / max(halflife, 1e-6))
    ewm_v = float(np.sum(w * vals) / np.sum(w))
    mu = ewm_v
    usage_proj = None
    ok = np.isfinite(use)
    if ok.sum() >= max(MIN_GAMES, int(0.7 * n)) and float(np.nansum(use * w)) > 0:
        wu = w[ok]
        ewm_u = float(np.sum(wu * use[ok]) / np.sum(wu))
        szn_u = float(np.mean(use[ok][-min(int(ok.sum()), 20):]))
        usage_proj = 0.6 * ewm_u + 0.4 * szn_u
        rate = float(np.sum(wu * vals[ok]) / np.sum(wu * use[ok]))
        wgt = EWM_WEIGHT if ewm_weight is None else ewm_weight
        mu = wgt * ewm_v + (1.0 - wgt) * rate * usage_proj
    return {"mu": max(mu * opp_factor, 0.0), "n_games": n, "usage_proj": usage_proj, "opp_factor": opp_factor}


# -------------------------------------------------------------------------------- NBA points skewed marginal
# The PR #186 walk-forward (nba/sim, docs/analysis/nba_joint_simulator_2026-11-05.md) found the board's symmetric
# Normal for NBA points badly calibrated (ECE 0.047 at the L10 proxy line) where the simulator's points draws --
# points built as 3 x threes + nonthree points, each leg calibrated to the engine's own (mu, sd) -- cut ECE to
# 0.005 and gained +0.0047 / +0.0071 log score (z +9.0 / +25.3) on 34,429 held-out player-games. This is the closed
# form of that marginal at the board's (mu, sd), so the projected mean and the measured spread are preserved
# exactly and only the shape changes:
#   threes leg    = the board's own NegBin threes marginal (mu3, sd3 from the player's threes component);
#   nonthree leg  = residual mean mu - 3 mu3 and residual variance sd^2 - 9 sd3^2 under independence (the
#                   simulator's measured corr(threes, nonthree) in its draws is 0.05-0.13, second-order), as a
#                   NegBin when over-dispersed and a continuity-corrected gamma (nba.props.dist's device for
#                   points) when it is not.
# PR #191's held-out verification of this marginal (34,451 player-games, 11,828 real book lines) found the
# original degenerate-input handling cost more than it saved, so the split now REFUSES inputs it cannot carry
# and every consumer prices the exact Normal instead:
#   - non-finite or non-positive inputs (a NaN threes projection crashed int(nbinom.ppf(...)) outright);
#   - mu - 3 mu3 < 0.05 mu (the old code floored the residual mean at 0.05 mu, inflating the total mean: one
#     verified row's mean jumped 10.45 -> 11.63);
#   - sd^2 - 9 sd3^2 < _SKEW_NT_VAR_FRAC (mu - 3 mu3) (the old threes-variance-shrink and single-gamma ladders
#     lost -0.006..-0.010 log score on the ~8% of rows that hit them);
#   - and any line below mu - sd, where the closed form has no zero-point injury / early-exit spike
#     (P(0) 0.3% vs 2.9% realised) and lost -0.0166 log score at z = -1.5.
# On the rows the split does carry, the same study measured it gaining +0.0055..+0.0069 log score over the
# Normal. Kill switch: NBA_SKEWED_POINTS=0/false/no/off (see nba_skewed_points_enabled); _nba_component then
# supplies no split and every price is the old Normal.
_SKEW_NT_VAR_FRAC = 0.35          # floor on the nonthree leg's variance, as a fraction of its mean


def _negbin_n_p(mu: float, var: float) -> tuple[float, float]:
    """(n, p) of a NegBin with this mean and variance (alpha clipped at 1e-6, the nba.sim.Marginal convention)."""
    a = max((var - mu) / max(mu * mu, 1e-9), 1e-6)
    n = 1.0 / a
    return n, n / (n + mu)


def _gamma_disc_params(mu: float, var: float) -> tuple[float, float]:
    """(shape, scale) of a gamma to be read through the continuity correction P(Y <= k) = F(k + 0.5): binning a
    continuous density onto the integers adds ~1/12 of variance (Sheppard), so the shape is fit to var - 1/12."""
    gv = var - 1.0 / 12.0 if var > 0.5 else var
    gv = max(gv, 1e-6)
    return mu * mu / gv, gv / mu


def _skew_comp_cdf(comp: tuple, k) -> np.ndarray:
    """P(Y <= k) for integer k (array) under a ("negbin", n, p) or ("gamma", shape, scale) component; k < 0 -> 0."""
    kind, a, b = comp
    if kind == "negbin":
        return nbinom.cdf(k, a, b)
    return gamma.cdf(np.asarray(k, dtype=float) + 0.5, a, scale=b)


def _nba_points_skew_components(mu: float, sd: float, mu3: float, sd3: float):
    """(threes, nonthree) components of the skewed points marginal, or None when the split cannot be carried:
    any input non-finite or non-positive, a residual mean mu - 3 mu3 under the 0.05 mu floor, or a residual
    variance sd^2 - 9 sd3^2 under _SKEW_NT_VAR_FRAC of the residual mean (see the block above). None means
    every consumer prices the exact Normal; there are no partial-split fallback ladders (PR #191 measured
    the ladders this replaced losing log score on the rows that hit them)."""
    if not all(math.isfinite(x) for x in (mu, sd, mu3, sd3)):
        return None
    if mu <= 0 or sd <= 0 or mu3 <= 0 or sd3 <= 0:
        return None
    mu_nt = mu - 3.0 * mu3
    if mu_nt < 0.05 * mu:
        return None
    var_nt = sd * sd - 9.0 * sd3 * sd3
    if var_nt < _SKEW_NT_VAR_FRAC * mu_nt:
        return None
    t = ("negbin",) + _negbin_n_p(mu3, sd3 * sd3)
    nt = ("negbin",) + _negbin_n_p(mu_nt, var_nt) if var_nt >= mu_nt else ("gamma",) + _gamma_disc_params(mu_nt, var_nt)
    return t, nt


def nba_points_skew_cdf(k: float, mu: float, sd: float, mu3: float, sd3: float) -> float:
    """P(X <= k) for NBA points X = 3T + N under the skewed marginal above. On the split path k is floored,
    matching the count families' convention in p_over: a whole-number line is a push, neither over nor under.
    When the split refuses the inputs, or the line sits below mu - sd (the closed form carries no zero-point
    spike down there, PR #191), the price is the exact Normal at the line as passed -- which is what makes
    p_over's delegation to this function price its own fallbacks identically."""
    comps = _nba_points_skew_components(mu, sd, mu3, sd3)
    if comps is None or k < mu - 1.0 * sd:
        return float(norm.cdf((k - mu) / max(sd, 1e-6)))
    k = math.floor(k)
    t, nt = comps
    top = int(nbinom.ppf(1.0 - 1e-10, t[1], t[2])) + 2
    ts = np.arange(top + 1)
    pmf = nbinom.pmf(ts, t[1], t[2])
    return float(np.sum(pmf * _skew_comp_cdf(nt, k - 3.0 * ts)))


def _nba_points_skew_pmf(mu: float, sd: float, mu3: float, sd3: float, kmax: int) -> np.ndarray:
    """P(X = x) for x = 0..kmax under the skewed marginal (proj_range's quantiles)."""
    out = np.zeros(kmax + 1)
    xs = np.arange(kmax + 1)
    comps = _nba_points_skew_components(mu, sd, mu3, sd3)
    if comps is None:
        # the split refused the inputs: the pmf of the exact Normal nba_points_skew_cdf falls back to,
        # read onto the integers through the same continuity correction the gamma legs use.
        cdf = norm.cdf(xs + 0.5, mu, max(sd, 1e-6)) if mu > 0 else (xs >= 0).astype(float)
        out[0] = cdf[0]
        out[1:] = np.diff(cdf)
        return out
    t, nt = comps
    if nt[0] == "negbin":
        pmf_n = nbinom.pmf(xs, nt[1], nt[2])
    else:
        cdfn = gamma.cdf(xs + 0.5, nt[1], scale=nt[2])
        pmf_n = np.empty(kmax + 1)
        pmf_n[0] = cdfn[0]
        pmf_n[1:] = np.diff(cdfn)
    top = int(nbinom.ppf(1.0 - 1e-10, t[1], t[2])) + 2
    for tt in range(top + 1):
        w = float(nbinom.pmf(tt, t[1], t[2]))
        if w <= 1e-13:
            continue
        shift = 3 * tt
        if shift > kmax:
            break
        out[shift:] += w * pmf_n[:kmax + 1 - shift]
    return out


def p_over(family: str, mu: float, line: float, ctx: StatContext, sd_mu: float | None = None,
           sd: float | None = None, alpha: float | None = None, zi: float = 0.0,
           mu3: float | None = None, sd3: float | None = None) -> float | None:
    """P(X > line) under the stat's family. `line` is the book's number (x.5 for nearly everything). `sd_mu` is the mean
    the NORMAL family's spread is computed from (default: mu) -- a calibrated mean keeps the uncalibrated spread.
    `sd` / `alpha` (NBA hierarchical path) replace the league-wide Normal sd / NegBin dispersion outright. `zi` > 0 makes
    the NegBin zero-inflated while keeping `mu` the overall mean (the NegBin part has mean mu / (1 - zi)).
    `mu3` / `sd3` (the player's own threes marginal, supplied only by _nba_component and only for NBA points) switch
    the price to the skewed 3 x threes + nonthree marginal above; without them the price is exactly what it was."""
    if mu is None or not math.isfinite(mu) or line is None:
        return None
    if family == "anytime":
        return float(1.0 - math.exp(-max(mu, 0.0))) if line < 1.0 else float(1.0 - _pois_cdf(math.floor(line), mu))
    if mu <= 0:
        return 0.0 if line >= 0 else 1.0
    if family == "negbin":
        n = 1.0 / (ctx.alpha if alpha is None else alpha)
        if zi > 0:
            if line < 0:
                return 1.0
            m = mu / (1.0 - zi)
            return float((1.0 - zi) * (1.0 - nbinom.cdf(math.floor(line), n, n / (n + m))))
        return float(1.0 - nbinom.cdf(math.floor(line), n, n / (n + mu)))
    if mu3 is not None and sd3 is not None and sd is not None:
        return float(1.0 - nba_points_skew_cdf(line, mu, sd, mu3, sd3))
    if sd is not None:
        return float(1.0 - norm.cdf((line - mu) / max(sd, 1e-6)))
    ref = mu if sd_mu is None else sd_mu
    sd = max(ctx.cv * ref, MIN_SD_FRAC * ref, 1e-6)
    return float(1.0 - norm.cdf((line - mu) / sd))


def blend_c(sport: str | None, stat: str | None, trained: bool = False) -> float:
    """The blend weight for a sport/stat: BLEND_C_TRAINED for a row served from the trained models, else BLEND_C_BY_SPORT_STAT, else
    that sport's "default", else BLEND_C."""
    if trained:
        c = BLEND_C_TRAINED.get(sport or "", {}).get(stat)
        if c is not None:
            return float(c)
    table = BLEND_C_BY_SPORT_STAT.get(sport or "", {})
    return float(table.get(stat, table.get("default", BLEND_C)))


def blend_ab(sport: str | None, stat: str | None) -> tuple[float, float]:
    """(a, b) of the blend for a sport/stat: BLEND_AB_BY_SPORT_STAT, else the c-only form (0.0, 1.0)."""
    a, b = BLEND_AB_BY_SPORT_STAT.get(sport or "", {}).get(stat, (0.0, 1.0))
    return float(a), float(b)


def blend_p_over(p_model_over: float | None, p_mkt_over: float | None, c: float | None = None,
                 sport: str | None = None, stat: str | None = None, a: float | None = None, b: float | None = None,
                 trained: bool = False) -> float | None:
    """Market-anchored posterior: logit(p) = a + b logit(market) + c (logit(model) - logit(market)).

    This is the form the repo's own model-vs-market studies test (blend coefficient `c`; docs/analysis/
    bettingpros_model_vs_market_2026-10-15.md): c = 0 trusts the market entirely, c = 1 trusts the model entirely. Those
    studies measured c = +0.02 .. +0.30 for the TRAINED research models; the per-stat table (BLEND_C_BY_SPORT_STAT) says which
    weights are measured on this engine and which are priors. `a` / `b` default to the c-only form (0, 1) unless
    BLEND_AB_BY_SPORT_STAT carries a refit for the stat; with a refit the result is clamped to the market +/- BLEND_MAX_SHIFT, as in the
    studies. It is display-only: it feeds neither the edge, the confluence flag nor the Beezy Score (the Official-tier `blend_ev`
    gate of ev.model_picks aside). None unless both engines have a probability.

    `c` explicit wins; else the per-stat weight for (sport, stat) (blend_c); else BLEND_C."""
    if p_model_over is None or p_mkt_over is None:
        return None
    if c is None:
        c = blend_c(sport, stat, trained)
    explicit_ab = a is not None or b is not None
    if a is None or b is None:
        fa, fb = blend_ab(sport, stat)
        a, b = fa if a is None else a, fb if b is None else b
    lo, hi = 0.01, 0.99
    pm = min(max(float(p_model_over), lo), hi)
    pk = min(max(float(p_mkt_over), lo), hi)
    lk = math.log(pk / (1 - pk))
    z = a + b * lk + c * (math.log(pm / (1 - pm)) - lk)
    p = 1.0 / (1.0 + math.exp(-z))
    if explicit_ab or (a, b) != (0.0, 1.0):
        p = min(max(p, pk - BLEND_MAX_SHIFT), pk + BLEND_MAX_SHIFT)
    return p


def sharp_fields(cons, side: str | None, best: dict) -> dict:
    """{p_sharp_over, n_sharp, sharp_agrees} from the consensus's per-book fair P(over).

    p_sharp_over = median fair P(over) of the SHARP_BOOKS that two-side the line, leaving out the book we would bet at (a venue
    cannot confirm its own price). sharp_agrees is False only when such a book exists and prices OUR side more than SHARP_TOLERANCE
    below the whole pack's consensus; with no sharp book (Pinnacle never quotes BettingPros props) it is True and `n_sharp` is 0 --
    fail-open, so absence of a sharp reference never blocks a row."""
    out = {"p_sharp_over": None, "n_sharp": 0, "sharp_agrees": True}
    if cons is None or getattr(cons, "book_fair", None) is None:
        return out
    bet_book = (best.get(side) or (None,))[0] if side in ("over", "under") else None
    vals = [float(p) for bk, p in cons.book_fair if bk in SHARP_BOOKS and bk != bet_book]
    if not vals:
        return out
    p_sharp = float(np.median(vals))
    out.update(p_sharp_over=p_sharp, n_sharp=len(vals))
    if side in ("over", "under"):
        p_sharp_side = p_sharp if side == "over" else 1.0 - p_sharp
        p_mkt_side = cons.p_over if side == "over" else 1.0 - cons.p_over
        out["sharp_agrees"] = bool(p_sharp_side >= p_mkt_side - SHARP_TOLERANCE)
    return out


def blend_side_ev(p_blend_over: float | None, side: str | None, best: dict) -> tuple:
    """(P(side) under the blended posterior, EV of the best quote on that side at that probability); None where the blend, the
    side or a real quote on the side is missing. `blend_ev` is the market-anchored counterpart of `market_ev` (which prices the
    best quote at the bare consensus): with a small measured c it stays near market_ev, and it is what the engine's own
    information is worth once shrunk to what it was measured to add."""
    if p_blend_over is None or side not in ("over", "under"):
        return None, None
    p_side = p_blend_over if side == "over" else 1.0 - p_blend_over
    b = best.get(side)
    return p_side, (p_side * b[1] - 1.0) if b else None


def proj_range(family: str, mu: float, ctx: StatContext, lo: float = 0.10, hi: float = 0.90,
               sd_mu: float | None = None, sd: float | None = None, alpha: float | None = None, zi: float = 0.0,
               mu3: float | None = None, sd3: float | None = None):
    """(floor, ceiling) = the lo / hi quantiles of the projection's own distribution, or (None, None) for the
    one-sided anytime market. Yards are floored at 0 (the board shows a rate-style range, not a loss). These describe
    the MODEL'S spread (league-wide within-player variance), not an interval with validated coverage."""
    if mu is None or not math.isfinite(mu) or family == "anytime":
        return None, None
    if mu <= 0:
        return 0.0, 0.0
    if family == "negbin":
        n = 1.0 / (ctx.alpha if alpha is None else alpha)
        if zi > 0:
            pr = n / (n + mu / (1.0 - zi))

            def q(level):
                return 0.0 if level <= zi else float(nbinom.ppf((level - zi) / (1.0 - zi), n, pr))
            return q(lo), q(hi)
        pr = n / (n + mu)
        return float(nbinom.ppf(lo, n, pr)), float(nbinom.ppf(hi, n, pr))
    if sd is None:
        ref = mu if sd_mu is None else sd_mu
        sd = max(ctx.cv * ref, MIN_SD_FRAC * ref, 1e-6)
    if mu3 is not None and sd3 is not None:
        # NBA points: quantiles of the same skewed marginal p_over prices, so the shown range and the shown
        # probabilities describe one distribution. When the split refuses the inputs, the range is the
        # Normal's, exactly like the prices (PR #191).
        if _nba_points_skew_components(mu, sd, mu3, sd3) is None:
            return float(max(norm.ppf(lo, mu, sd), 0.0)), float(norm.ppf(hi, mu, sd))
        kmax = int(mu + 12.0 * sd) + 20
        cdf = np.cumsum(_nba_points_skew_pmf(mu, sd, mu3, sd3, kmax))
        return float(min(int(np.searchsorted(cdf, lo)), kmax)), float(min(int(np.searchsorted(cdf, hi)), kmax))
    return float(max(norm.ppf(lo, mu, sd), 0.0)), float(norm.ppf(hi, mu, sd))


def _pois_cdf(k: int, mu: float) -> float:
    return float(sum(math.exp(-mu) * mu ** i / math.factorial(i) for i in range(int(k) + 1)))


# -------------------------------------------------------------------------------------------------------- the market
def _real(qs):
    return [q for q in qs if q.book not in DFS_BOOKS]


def pick_main_line(qs: list):
    """Quotes for ONE (player, stat) -> (point, consensus | None, quotes_at_point) or None.

    The consensus line is the point with the most two-sided real books (ties: closest to a coin flip). Alternate-ladder
    quotes are ignored when any main-line quote exists. A player with only one-sided 'Yes' prices (anytime TD, goals)
    gets the point with the most such quotes and no consensus (a one-sided price cannot be de-vigged)."""
    real = _real(qs)
    if not real:
        return None
    main = [q for q in real if not q.alt] or real
    cons = build_consensus(main)
    best = None
    for (pn, stat, pt), c in cons.items():
        key = (c.n_books, -abs(c.p_over - 0.5), -pt)
        if best is None or key > best[0]:
            best = (key, pt, c)
    if best is not None:
        _, pt, c = best
        return pt, c, [q for q in main if q.point == pt]
    counts: dict = {}
    for q in main:
        counts.setdefault(q.point, []).append(q)
    pt = max(counts, key=lambda p: (len(counts[p]), -p))
    return pt, None, counts[pt]


def best_prices(qs_at_point: list) -> dict:
    """Best over / under American price (highest decimal) and its book among real, ONSHORE books at the point (an offshore
    book is a reference price in the consensus, never the book we point a reader at)."""
    best = {"over": None, "under": None}
    for q in _real(qs_at_point):
        if q.book in OFFSHORE_BOOKS:
            continue
        for side, price in (("over", q.over), ("under", q.under)):
            if price is None:
                continue
            d = american_to_decimal(price)
            if best[side] is None or d > best[side][1]:
                best[side] = (q.book, d, int(price))
    return best


# --------------------------------------------------------------------------------------------------------- row math
def edge_fields(p_model_over, p_mkt_over, best: dict, n_books: int | None) -> dict:
    """Side, edges and confluence from the model's and the market's P(over) and the best prices."""
    out = {"side": None, "model_edge": None, "market_ev": None, "confluence": False, "score_basis": "none",
           "edge_capped": False}
    if p_model_over is None:
        return out
    if p_mkt_over is None:
        out["side"] = "over" if p_model_over >= 0.5 else "under"
        return out
    side = "over" if p_model_over >= p_mkt_over else "under"
    p_model = p_model_over if side == "over" else 1.0 - p_model_over
    p_mkt = p_mkt_over if side == "over" else 1.0 - p_mkt_over
    b = best.get(side)
    model_edge = p_model - p_mkt
    market_ev = (p_mkt * b[1] - 1.0) if b else None
    out.update(side=side, model_edge=model_edge, market_ev=market_ev)
    capped = model_edge > EDGE_CAP or (market_ev is not None and market_ev > EDGE_CAP)
    out["edge_capped"] = bool(capped)
    out["confluence"] = bool(model_edge > 0 and market_ev is not None and market_ev > 0 and not capped
                             and (n_books or 0) >= CONFLUENCE_MIN_BOOKS)
    return out


def score_inputs(ef: dict, n_books, p_model_side, p_mkt_side) -> tuple[int, str]:
    """-> (beezy_score, basis). See the module docstring for the edge rule."""
    me, ev = ef["model_edge"], ef["market_ev"]
    me_pos, ev_pos = (me or 0.0) > 0, (ev or 0.0) > 0
    if ef["edge_capped"]:
        return 0, "capped"
    if ef["confluence"]:
        edge, basis = min(me, ev), "confluence"
    elif ev_pos and not me_pos:
        edge, basis = 0.5 * ev, "market"
    elif me_pos and not ev_pos:
        edge, basis = 0.5 * me, "model"
    else:
        return 0, "none"
    p_hit = p_model_side if p_model_side is not None else p_mkt_side
    return beezy_score(edge, n_books, p_hit), basis


def resolve_team(sport: str, rows: list, pn: str, logs: "GameLogs", roster, teams: tuple | None = None) -> tuple:
    """-> (team code | None, how). `how` says where the team came from, for the payload's roster counters:
    'roster' (the roster names it), 'log' (last game-log team, the old behaviour), 'none' (no usable team code).

    With `teams` (the two clubs of the game the player is quoted in) the roster team is used only when it is one of them,
    else the game-log team when it is: a quote on THIS game is the ground truth that the player is on one of these two
    clubs, so a stale roster must not drag him off it. A player on no roster (retired, a partial pull, an ambiguous name)
    keeps the last-game-log team exactly as before."""
    log_team = logs.team_of(rows) if rows else None
    if roster is None:
        return log_team, ("log" if log_team else "none")
    state, rteam = roster.lookup(pn, log_team)
    if state == "on" and (teams is None or rteam in teams):
        return rteam, "roster"
    return log_team, ("log" if log_team else "none")


def build_line_rows(sport: str, events: list, logs: GameLogs, now: datetime, roster=None) -> tuple[list, dict]:
    """Pure core: banked book events + game logs -> projection rows (one per player x stat at its main line).

    `roster` (ev.rosters.Roster | None) supplies each player's CURRENT team. Without it the team is the last game-log
    row's, which is wrong after a trade and dropped quoted players as `ambiguous_team` (an offseason NBA problem).
    `roster_recovered` counts rows that the roster saved from that drop; `roster_team_changed` counts rows whose team
    differs from the game-log team; `ambiguous_team` is what is still dropped."""
    stats_cfg = PROJECT_STATS[sport]
    out, st = [], {"events": 0, "events_started": 0, "players_quoted": 0, "no_log": 0, "thin_history": 0,
                   "ambiguous_team": 0, "roster_recovered": 0, "roster_team_changed": 0, "rows": 0}
    for ev in events:
        try:
            start = datetime.fromisoformat(str(ev.get("commence_time")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if start <= now:
            st["events_started"] += 1
            continue
        away, home = canon_team(sport, ev.get("away_team")), canon_team(sport, ev.get("home_team"))
        st["events"] += 1
        quotes, _ = parse_event_props(ev, sport)
        by_ps: dict = {}
        for q in quotes:
            if q.stat in stats_cfg:
                by_ps.setdefault((q.player_norm, q.stat), []).append(q)
        gk = f"{sport}:{start.astimezone(_ET).date().isoformat()}:{away}@{home}"
        mins = (start - now).total_seconds() / 60.0
        for (pn, stat), qs in by_ps.items():
            picked = pick_main_line(qs)
            if picked is None:
                continue
            st["players_quoted"] += 1
            pt, cons, at_pt = picked
            rows, sdef = logs.rows_for(stat, pn)
            if not rows:
                st["no_log"] += 1
                continue
            log_team = logs.team_of(rows)
            team, how = resolve_team(sport, rows, pn, logs, roster, (away, home))
            if how == "roster" and team != log_team:
                st["roster_team_changed"] += 1
                if log_team is not None and log_team not in (away, home):
                    st["roster_recovered"] += 1
            if team is not None and team not in (away, home):
                st["ambiguous_team"] += 1
                continue                                   # traded / stale roster: do not project against a guess
            opp = None if team is None else (home if team == away else away)
            row = make_row(sport, stat, qs[0].player, pn, rows, sdef, logs, pt, cons, at_pt, team, opp, gk, start, mins,
                           roster=roster, all_quotes=qs)
            if row is None:
                st["thin_history"] += 1
                continue
            out.append(row)
    out.sort(key=_sort_key)
    st["rows"] = len(out)
    return out, st


def _nba_days(logs: GameLogs, start) -> tuple:
    """-> (day, as_of) in the master's date system (the UTC date of the tip; the master labels games that way). `day` dates
    tonight for the rest calculation; `as_of` is the history cutoff and is None for live serving (the master holds only
    finished games, and an ET cutoff would drop last night's late game, whose UTC date is tonight's ET date)."""
    if start is None:
        return None, None
    day = start.astimezone(timezone.utc).date().isoformat()
    return day, (day if logs.nba_replay else None)


def _nba_component(stat: str, pn: str, rows: list, logs: GameLogs, team, opp, gk, start) -> dict | None:
    """One NBA stat's calibrated marginal -> {"mu", "family", "sd", "alpha", "proj"} or None (thin history).
    Points rows also carry "mu3" / "sd3" (the player's threes marginal) while NBA_SKEWED_POINTS is on.

    mu = engine EWM (threes: EWM(3PA) x shrunk 3P%) x opponent factor x the stat's role-redistribution multiplier x the
    minutes factor (blowout / back-to-back trim). The spread is the league-wide one (Normal: cv, floored; NegBin: alpha),
    widened by the shot-diet scale (points) and by the minutes distribution's EXTRA relative variance. Uses only rows
    dated before the replay cutoff (none when serving live)."""
    day, as_of = _nba_days(logs, start)
    sdef = cf.NBA_STATS[stat]
    ctx = logs.context(stat)
    family = PROJECT_STATS["nba"][stat]
    prior = _hier.prior_rows(rows, as_of)
    hl, ewm_w, _ = stat_params("nba", stat)
    opp_f = ctx.opp_factor.get(opp, 1.0) if opp else 1.0
    k_dvp = NBA_FEATURES["dvp_k"]
    grp = _nbacfg.POS_GROUP.get(str(rows[-1].get("position") or "").upper()) if rows else None
    if k_dvp and opp and grp and (opp, grp) in ctx.nba_opp_pos_raw:
        c = ctx.nba_opp_pos_cnt[(opp, grp)]
        opp_f = min(max(1.0 + (ctx.nba_opp_pos_raw[(opp, grp)] - 1.0) * c / (c + k_dvp), OPP_CLIP[0]), OPP_CLIP[1])
    proj = project_mu(_hist(prior, sdef), hl, opp_f, ewm_w)
    if proj is None:
        return None
    mu = proj["mu"]
    if team and opp and day:
        if stat == "rebounds" and NBA_FEATURES["pool_gamma"]:
            mu *= logs.nba_team_games().pool_factor(prior, team, opp, day, NBA_FEATURES["pool_gamma"])
        elif stat == "assists" and NBA_FEATURES["shot_gamma"]:
            mu *= logs.nba_team_games().shotmaking_factor(prior, team, opp, day, NBA_FEATURES["shot_gamma"])
    if day and getattr(logs, "nba_tracking", None) is not None:
        if stat == "assists" and NBA_FEATURES["trk_ast"]:
            mu *= logs.nba_tracking.assist_factor(pn, _trk.season_year_of(day), NBA_FEATURES["trk_ast"])
        elif stat == "rebounds" and NBA_FEATURES["trk_reb"]:
            mu *= logs.nba_tracking.rebound_factor(pn, _trk.season_year_of(day), NBA_FEATURES["trk_reb"])
    if stat == "threes":
        t = _hier.project_nba_threes(prior)                           # None when the master carries no attempts
        if t is not None:
            mu = max(t["mu_3pm"] * opp_f, 0.0)
    mu *= logs.nba_role_boosts(team, as_of).get(pn, {}).get(stat, 1.0)
    extra = 0.0
    mu_min = _hier.ewm_minutes(prior)
    if mu_min:
        md = _hier.project_minutes_distribution(mu_min, spread=logs.nba_spread(gk), rest=_hier.rest_days(prior, day),
                                                pf36=_hier.pf_per36(prior))
        mu *= md["factor"]
        extra = md["extra_rel_var"]
    alpha = sd = None
    zi = 0.0
    mu3 = sd3 = None
    if family == "negbin":
        if NBA_FEATURES["zinb"] and stat in ("steals", "blocks") and ctx.nba_zi_pi > 0 and ctx.nba_zi_alpha:
            zi = ctx.nba_zi_pi
            alpha = ctx.nba_zi_alpha + extra
            m = mu / (1.0 - zi)
            sd = math.sqrt(max((1.0 - zi) * (m + alpha * m * m) + zi * (1.0 - zi) * m * m, 0.0))
        else:
            alpha = ctx.alpha + extra                                 # var = mu + alpha mu^2: extra relative variance adds to alpha
            sd = math.sqrt(max(mu + alpha * mu * mu, 0.0))
    else:
        sd = max(ctx.cv * mu, MIN_SD_FRAC * mu, 1e-6)
        if stat == "points":
            sd *= _hier.shot_diet_sd_scale(prior)
        sd = math.sqrt(sd * sd + extra * mu * mu)
    if stat == "points" and nba_skewed_points_enabled():
        # the threes split of the skewed points marginal (see the block above p_over): the player's OWN threes
        # component, so the points leg and the threes leg share one threes distribution. Left None when the kill
        # switch is off, and every consumer then prices the exact old Normal.
        t3 = _nba_component("threes", pn, rows, logs, team, opp, gk, start)
        if t3 is not None:
            mu3, sd3 = t3["mu"], t3["sd"]
    return {"mu": mu, "family": family, "sd": sd, "alpha": alpha, "zi": zi, "proj": proj, "mu3": mu3, "sd3": sd3}


def _nba_projection(stat: str, pn: str, rows: list, logs: GameLogs, team, opp, gk, start) -> dict | None:
    """-> {"mu", "family", "sd", "alpha", "proj"} for any NBA stat. pra / pr / pa / ra are NEVER projected on their own:
    they are built from the points / rebounds / assists marginals (each rounded to the 3 decimals the board shows, so the
    published means add up exactly) and the player's shrunken within-player correlation matrix:
    mu_C = sum mu_k; var_C = sum sd_k^2 + 2 sum_{j<k} R_jk sd_j sd_k, distributed Normal."""
    members = _hier.COMBOS.get(stat)
    if members is None:
        return _nba_component(stat, pn, rows, logs, team, opp, gk, start)
    comps = {}
    for k in members:
        c = _nba_component(k, pn, rows, logs, team, opp, gk, start)
        if c is None:
            return None
        c["mu"] = round(c["mu"], 3)
        comps[k] = c
    corr = _hier.within_player_corr(rows, _nba_days(logs, start)[1])
    mu, sd = _hier.combo_moments({k: (c["mu"], c["sd"]) for k, c in comps.items()}, corr, members)
    wsum = sum(c["mu"] for c in comps.values()) or 1.0
    first = comps[members[0]]["proj"]
    proj = {"mu": mu, "n_games": min(c["proj"]["n_games"] for c in comps.values()), "usage_proj": first["usage_proj"],
            "opp_factor": sum(c["mu"] * c["proj"]["opp_factor"] for c in comps.values()) / wsum}
    return {"mu": mu, "family": "normal", "sd": sd, "alpha": None, "zi": 0.0, "proj": proj}


def _starter_rows(rows: list) -> list:
    """Goalie rows of games he started (the `starter` flag), or every row when the flag is absent from the data."""
    flagged = [r for r in rows if r.get("starter") is not None and r.get("starter") == r.get("starter")]
    if not flagged:
        return rows
    return [r for r in rows if str(r.get("starter")).strip().lower() in ("true", "1", "1.0")]


def nhl_as_of(start) -> str | None:
    """The game's ET date (YYYY-MM-DD): the NHL engine reads only games strictly before it."""
    return None if start is None else start.astimezone(_ET).date().isoformat()


def project_nhl(stat: str, rows: list, sdef: tuple, logs: GameLogs, team, opp, as_of: str | None, roster=None,
                pn: str | None = None, absent=()) -> tuple:
    """The hierarchical NHL estimate for one player-stat (nhl.props.hierarchical) -> (proj, dist, info).

    `proj` has the project_mu shape ({mu, n_games, usage_proj, opp_factor}) with `mu` the full projection (opponent terms
    included), `dist` is a SavesMixture for goalie saves and None otherwise (the stat's NegBin family prices it), and `info`
    is a dict of extra row fields. Every input is cut to games strictly before `as_of`, league contexts included.
    A stat whose extra inputs are missing (a history without goals / sog columns, no team box master) falls back to the
    generic project_mu estimate for that term, so a thin master never drops a row the old engine would have produced."""
    rows = _nh.before(rows, as_of)
    info = {"nhl_model": "hierarchical_v1"}
    served = logs.trained_pred(rows[-1].get("player_id") if rows else None, as_of, stat)
    if served is not None and len(_hist(rows, sdef)) >= MIN_GAMES:
        mu_t, alpha_t, ver = served
        info.update(nhl_model="trained_v1", trained_version=ver)
        proj = {"mu": mu_t, "n_games": len(_hist(rows, sdef)), "usage_proj": None, "opp_factor": 1.0}
        return proj, _nh.NegBinDist(mu_t, alpha_t), info

    def ctx_of(st):
        return logs.context(st, as_of)

    def generic(st, sd, opp_factor=1.0, halflife=None):
        hl, w, _ = stat_params("nhl", st)
        return project_mu(_hist(rows, sd), halflife or hl, opp_factor, w)

    pace = logs.team_pace(as_of)
    league_sog = (pace.get("_league") or {}).get("sog_for")
    pos = rows[-1].get("position") if rows else None

    if stat == "saves":
        h = _hist(rows, sdef)
        if len(h) < MIN_GAMES:
            return None, None, info
        own_sa = _nh.ewm(_nh.col_values(_starter_rows(rows), "shots_against"), stat_params("nhl", stat)[0])
        t, o = pace.get(team) or {}, pace.get(opp) or {}
        sa = _nh.expected_shots_against(t.get("sa_ewm"), o.get("sog_for"), own_sa)
        if sa is None:
            return generic(stat, sdef, ctx_of(stat).opp_factor.get(opp, 1.0)), None, info
        sv = _nh.eb_save_pct(rows, prior=logs.league_sv(as_of) or _nh.SV_PRIOR)
        mu = _nh.START_SHARE * sa * sv
        info.update(sv_pct_eb=_f(sv, 4), exp_shots_against=_f(sa, 2))
        proj = {"mu": mu, "n_games": len(h), "usage_proj": sa,
                "opp_factor": _nh.pace_factor(o.get("sog_for"), league_sog, o.get("n", _nh.PACE_WINDOW))}
        return proj, _nh.SavesMixture.fit(mu, ctx_of(stat).cv), info

    vac = (logs.nhl_vacated(team, as_of, roster, absent) if (roster is not None or absent) else {}).get(pn)
    sk = cf.NHL_SKATER_STATS
    gctx = ctx_of("goals")
    opp_g = gctx.opp_factor.get(opp, 1.0) if opp else 1.0
    if _nh.GOALIE_BETA and opp:
        gf = _nh.goalie_factor(logs.opp_goalie_gsax(opp, as_of))
        opp_g *= gf
        if gf != 1.0:
            info["opp_goalie_factor"] = _f(gf, 4)

    def goals_part():
        g_pj, s_pj = generic("goals", sk["goals"]), generic("sog", sk["sog"])
        sh = _nh.shooting_pct_from_rows(rows, pos)
        if g_pj is None:
            return None, None
        if s_pj is None or sh is None:                       # no shots / finishing history: the plain goals estimate
            return g_pj["mu"] * opp_g, g_pj
        mu_sog = s_pj["mu"] + (vac["sog"] if vac else 0.0)
        mu = _nh.goals_mu(g_pj["mu"], mu_sog, sh) * opp_g
        info.update(sh_pct_eb=_f(sh, 4))
        return mu, g_pj

    def assists_part():
        a_pj = generic("assists", sk["assists"], halflife=_nh.HALFLIFE_ASSISTS)
        if a_pj is None:
            return None, None
        return (a_pj["mu"] + (vac["assists"] if vac else 0.0)) * opp_g, a_pj

    if stat == "goals":
        mu, base = goals_part()
        if mu is None or base is None:
            return None, None, info
        proj = dict(base, mu=mu, opp_factor=opp_g)
    elif stat == "assists":
        mu, base = assists_part()
        if mu is None:
            return None, None, info
        proj = dict(base, mu=mu, opp_factor=opp_g)
    elif stat == "points":
        mg, bg = goals_part()
        ma, ba = assists_part()
        if mg is None or ma is None or bg is None:
            return generic(stat, sdef, ctx_of(stat).opp_factor.get(opp, 1.0)), None, info
        info.update(mu_goals=_f(mg, 3), mu_assists=_f(ma, 3))
        proj = dict(bg, mu=_nh.points_mu(mg, ma), opp_factor=opp_g, usage_proj=ba["usage_proj"])
    elif stat == "pp_points":
        h = _hist(rows, sdef)
        if len(h) < MIN_GAMES:
            return None, None, info
        vals = [g[0] for g in h]
        n = len(vals)
        hl = stat_params("nhl", stat)[0]
        n_eff = float(np.sum(0.5 ** ((n - 1 - np.arange(n)) / hl)))
        mu = _nh.pp_prior_rate(_nh.ewm(vals, hl), n_eff, pos) * ctx_of(stat).opp_factor.get(opp, 1.0)
        proj = {"mu": mu, "n_games": n, "usage_proj": None, "opp_factor": ctx_of(stat).opp_factor.get(opp, 1.0)}
    else:                       # sog, blocked_shots: trailing rate x the opponent-allowed factor (see the replay doc on blocks)
        pj = generic(stat, sdef)
        if pj is None:
            return None, None, info
        f = ctx_of(stat).opp_factor.get(opp, 1.0) if opp else 1.0
        boost = vac["sog"] if (vac and stat == "sog") else 0.0
        proj = dict(pj, mu=(pj["mu"] + boost) * f, opp_factor=f)
    if vac:
        info["vacated"] = {k: _f(v, 3) for k, v in vac.items()}
    return proj, None, info


def make_row(sport, stat, player, pn, rows, sdef, logs, line, cons, at_pt, team, opp, gk, start, mins,
             has_line: bool = True, roster=None, all_quotes=None) -> dict | None:
    family = PROJECT_STATS[sport][stat]
    as_of = nhl_as_of(start) if sport == "nhl" else None
    ctx = logs.context(stat, as_of)
    dist, extra = None, {}
    nb = None
    if sport == "nba":
        # 1. NBA: injury scratch, then the hierarchical projection (role-weighted teammate redistribution, minutes
        #    distribution, attempt-first threes, covariance-coherent combos -- nba.props.hierarchical)
        if team is not None and logs.nba_absences().get(team, {}).get(pn, 0.0) >= 0.8:
            return None
        nb = _nba_projection(stat, pn, rows, logs, team, opp, gk, start)
        if nb is None:
            return None
        proj, mu_raw = nb["proj"], nb["mu"]
    elif sport == "nhl":
        proj, dist, extra = project_nhl(stat, rows, sdef, logs, team, opp, as_of, roster, pn)
        if proj is None:
            return None
        mu_raw = proj["mu"]
    else:
        hl, ewm_w, _ = stat_params(sport, stat)
        proj = project_mu(_hist(rows, sdef), hl, ctx.opp_factor.get(opp, 1.0) if opp else 1.0, ewm_w)
        if proj is None:
            return None
        mu_raw = proj["mu"]

    # 3. NFL dynamic game environment + stadium weather
    coef = calibration(sport, stat)
    mult = 1.0
    if sport == "nfl" and team is not None and start is not None:
        env = logs.env_lines().get((team, start.astimezone(_ET).date().isoformat()))
        if env is not None:
            it, sp = env[0], env[1]
            wind = getattr(env, "wind", None)
            roof = getattr(env, "roof", None)
            temp = getattr(env, "temp", None)
            mult = _ea.multiplier(coef, implied_total=it, team_spread=sp, wind_mph=wind, roof=roof, temp_f=temp, stat=stat)
        elif coef:
            mult = _ea.multiplier(coef, stat=stat)
    elif coef:
        mult = _ea.multiplier(coef)

    mu = mu_raw * mult
    if nb is not None:
        # NBA points only: the threes split of the skewed marginal, scaled like mu itself (the spread is not
        # scaled, as with sd). Combo dicts carry no split, so pra / pr / pa keep the Normal.
        s_mu3 = nb["mu3"] * mult if nb.get("mu3") is not None else None
        s_sd3 = nb.get("sd3")
        p_model_over = (p_over(nb["family"], mu, line, ctx, sd=nb["sd"], alpha=nb["alpha"], zi=nb["zi"],
                               mu3=s_mu3, sd3=s_sd3)
                        if line is not None else None)
        p_floor, p_ceil = proj_range(nb["family"], mu, ctx, sd=nb["sd"], alpha=nb["alpha"], zi=nb["zi"],
                                     mu3=s_mu3, sd3=s_sd3)
    elif dist is not None:
        p_model_over = dist.p_over(line) if line is not None else None
        p_floor, p_ceil = dist.ppf(0.10), dist.ppf(0.90)
    else:
        p_model_over = p_over(family, mu, line, ctx, sd_mu=mu_raw) if line is not None else None
        p_floor, p_ceil = proj_range(family, mu, ctx, sd_mu=mu_raw)
    engine = ENGINE
    if sport == "nfl" and team is not None and _hl.stat_enabled(stat):
        env_t = logs.env_lines().get((team, start.astimezone(_ET).date().isoformat())) if start is not None else None
        hier = _price_hier(stat, rows, team, opp, env_t, logs, coef, mu_raw, start)
        if hier is not None:
            engine = ENGINE_NFL_HIER
            mu, mu_raw, mult = hier.mean, hier.mean, hier.vol_mult
            p_model_over = hier.p_over(line) if line is not None else None
            p_floor, p_ceil = (None, None) if family == "anytime" else (hier.dist.ppf(0.10), hier.dist.ppf(0.90))
    form = cf.shape_form(rows, sdef, line)
    p_mkt_over = cons.p_over if cons is not None else None
    # NHL trained saves hardening (Argon Props D1 / Pack 4 D1 / Data D2; nhl.props.saves_hardening):
    # 28-day log-odds centering + rolling residual calibration replace the raw model probability BEFORE
    # the blend and every edge below is computed; the gate inputs are stamped on the row for
    # ev.model_picks.qualifies. Other stats / engines are untouched.
    saves_hard = None
    if sport == "nhl" and stat == "saves" and extra.get("nhl_model") == "trained_v1" and p_model_over is not None:
        p_model_over, saves_hard = _saves_hardening_fields(logs, p_model_over, p_mkt_over, team, player, as_of)
    n_books = cons.n_books if cons is not None else (len(_real(at_pt)) if at_pt else None)
    best = best_prices(at_pt) if at_pt else {"over": None, "under": None}
    ef = edge_fields(p_model_over, p_mkt_over, best, cons.n_books if cons is not None else None)
    if saves_hard is not None:
        # the price gate's inputs: the (hardened) model's side edge over the median consensus and the
        # number of books behind that median (ev.props.build_consensus p_over IS the per-book median)
        saves_hard.update(saves_median_edge=None if ef["model_edge"] is None else round(float(ef["model_edge"]), 6),
                          saves_median_n_books=None if cons is None else int(cons.n_books))
    side = ef["side"]
    p_model_side = None if p_model_over is None or side is None else (p_model_over if side == "over" else 1 - p_model_over)
    p_mkt_side = None if p_mkt_over is None or side is None else (p_mkt_over if side == "over" else 1 - p_mkt_over)
    score, basis = score_inputs(ef, cons.n_books if cons is not None else None, p_model_side, p_mkt_side)
    bo, bu = best["over"], best["under"]
    p_blend_over = blend_p_over(p_model_over, p_mkt_over, sport=sport, stat=stat, trained=extra.get("nhl_model") == "trained_v1")
    p_blend_side, blend_ev = blend_side_ev(p_blend_over, side, best)
    sharp = sharp_fields(cons, side, best)
    row = {
        "sport": sport, "stat": stat, "stat_label": STAT_LABEL.get(stat, stat), "player": player, "player_norm": pn,
        "team": team, "opp": opp, "game_key": gk, "commence": None if start is None else start.isoformat(),
        "mins_to_start": None if mins is None else round(mins, 1),
        "has_line": bool(has_line and line is not None), "line": _f(line, 2), "consensus_line": _f(line, 2),
        "side": side, "proj_mean": _f(mu, 3), "proj_raw": _f(mu_raw, 3), "calib_mult": _f(mult, 4),
        "proj_p10": _f(p_floor, 1), "proj_p90": _f(p_ceil, 1),
        "p_over_model": _f(p_model_over),
        "p_over_blend": _f(p_blend_over), "p_blend_side": _f(p_blend_side), "blend_ev": _f(blend_ev),
        "p_sharp_over": _f(sharp["p_sharp_over"]), "n_sharp": sharp["n_sharp"], "sharp_agrees": sharp["sharp_agrees"],
        "p_model_side": _f(p_model_side), "p_mkt_side": _f(p_mkt_side),
        "l5_hit_rate": form["l5_rate"] if form else None, "l10_hit_rate": form["l10_rate"] if form else None,
        "szn_rate": form["szn_rate"] if form else None, "hit_basis": "over",
        "szn_avg": form["szn_avg"] if form else None, "avg_l10": form["avg_val"] if form else None,
        "usage_label": sdef[2], "usage_proj": _f(proj["usage_proj"], 2), "opp_factor": _f(proj["opp_factor"], 3),
        "n_games": proj["n_games"],
        "best_over_odds": bo[2] if bo else None, "best_over_book": bo[0] if bo else None,
        "best_under_odds": bu[2] if bu else None, "best_under_book": bu[0] if bu else None,
        "p_over_market": _f(p_mkt_over), "n_books": n_books,
        "model_edge": _f(ef["model_edge"]), "market_ev": _f(ef["market_ev"]), "confluence": ef["confluence"],
        "edge_capped": ef["edge_capped"], "beezy_score": score, "score_basis": basis,
        "validated": False, "engine": engine, **extra,
        **({"nba_model": NBA_MODEL} if nb is not None else {}),
    }
    if sport == "nhl":
        # RotoWire power-play unit / lineup status for the day: DISPLAY ONLY (no history to validate a PP multiplier against).
        row.update(_nhl_lineup_context(team, player, start))
        # D4 shadow cohort: search ALL quotes for this player/stat (not just the main-line real books in at_pt --
        # Fliff / Betr are DFS-classed and never reach at_pt or the consensus), falling back to at_pt.
        row.update(nhl_shadow_cohort_fields(stat, extra.get("nhl_model") == "trained_v1", all_quotes or at_pt))
        if saves_hard is not None:
            row.update(saves_hard)
    row["pick_tier"] = pick_tier(row)
    return row


_SAVES_RW_MEMO: dict = {}


def _saves_rotowire_confirmed(day: str | None) -> dict:
    """RotoWire-confirmed starters for a game day (ET), memoised to one cache read per minute.
    {} (nobody confirmed) on any failure: the dual gate is fail-closed, see nhl.props.saves_hardening."""
    if not day:
        return {}
    key = (day, int(time.time() // 60))
    if _SAVES_RW_MEMO.get("key") != key:
        _SAVES_RW_MEMO.update(key=key, data=_sh.rotowire_confirmed_for(day))
    return _SAVES_RW_MEMO["data"]


def _saves_hardening_fields(logs, p_model_over: float, p_mkt_over, team, player, as_of) -> tuple:
    """(hardened P(over), stamp dict) for one trained saves row (nhl.props.saves_hardening).

    The probability is centered (28-day log-odds) and, when a calibration fit exists, recalibrated
    against the de-vigged median market. The stamps carry every officials-gate input except the price
    gate's (the caller adds saves_median_edge / saves_median_n_books once the side is known): the
    dual-confirmation starter verdict (RotoWire cache + the fail-closed ESPN stub), and the regime
    verdict from the trailing 14-day realized over-rate. With no settled history installed the state
    is neutral (no centering, regime not blocked) but the starter stamp still blocks: the stub
    confirms nobody, by design, until a real ESPN fetcher replaces it."""
    state = logs.saves_state(as_of) if hasattr(logs, "saves_state") else None
    if state is not None:
        h = state.harden(p_model_over, p_mkt_over)
        p_final = h["p_final"]
        stamps = {"saves_p_model_raw": _f(p_model_over), "saves_p_centered": _f(h["p_centered"]),
                  "saves_center_residual": _f(h["center_residual"]), "saves_center_n": h["center_n"],
                  "saves_calibrated": bool(h["calibrated"]),
                  "saves_regime_blocked": bool(state.regime.blocked),
                  "saves_regime_over_rate": _f(state.regime.over_rate), "saves_regime_n": state.regime.n}
    else:
        p_final = p_model_over
        stamps = {"saves_p_model_raw": _f(p_model_over), "saves_p_centered": _f(p_model_over),
                  "saves_center_residual": None, "saves_center_n": 0, "saves_calibrated": False,
                  "saves_regime_blocked": False, "saves_regime_over_rate": None, "saves_regime_n": 0}
    starter = _sh.dual_confirmed_starter(team=str(team or ""), goalie=str(player or ""), date=str(as_of or ""),
                                         rotowire_confirmed=_saves_rotowire_confirmed(as_of),
                                         second_source=_sh.default_second_source())
    stamps.update(saves_starter_dual_confirmed=bool(starter.official_ok),
                  saves_starter_rotowire_confirmed=bool(starter.rotowire_confirmed),
                  saves_starter_second_source=starter.second.source,
                  saves_starter_second_status=starter.second.status,
                  saves_starter_gate_reason=starter.reason)
    return p_final, stamps


_LINEUP_MEMO: dict = {}


def _nhl_lineup_context(team, player, start) -> dict:
    """{'pp_unit', 'lineup_status'} from the RotoWire cache for a game starting today (ET); Nones otherwise. Never raises."""
    try:
        from nhl.data import rotowire_lineups as rl
        day = (start.astimezone(ZoneInfo("America/New_York")) if start else datetime.now(ZoneInfo("America/New_York"))).date().isoformat()
        key = (day, int(time.time() // 60))                      # one storage read per minute, not one per row
        if _LINEUP_MEMO.get("key") != key:
            _LINEUP_MEMO.update(key=key, data=rl.cached_lineups(day))
        return rl.player_context(_LINEUP_MEMO["data"], team, player)
    except Exception:                                                                  # noqa: BLE001
        return {"pp_unit": None, "lineup_status": None}


def pick_tier(row: dict) -> str | None:
    """'official' | 'tracked' | None: the row's tier under ev.model_picks.qualifies, the ONE definition of a pick. Stamped on every row
    so the site's Daily Card and boards read the backend's verdict instead of re-deriving the gates (the allowlist of stats with
    measured signal, the blended-EV bar and the sharp-book check live only in Python). Lazy import: model_picks imports this module."""
    try:
        from ev import model_picks
        return model_picks.qualifies(row)
    except Exception:                                                                  # noqa: BLE001
        return None


def _price_hier(stat, rows, team, opp, env_t, logs, coef, mu_raw, start):
    """The hierarchical price for one NFL row, or None (the caller keeps the legacy price). Never raises.

    The served configuration is the TESTED one: no context layers (NFL_HIER_LAYERS, default none -- the ablation found no layer that
    helps and two that hurt) and no weather (NFL_HIER_WEATHER=1 opts in: the schedule's wind is realised game-day data and the
    replay evidence was produced without it)."""
    import os
    try:
        use_weather = os.environ.get("NFL_HIER_WEATHER", "0") == "1"
        weather = None
        if use_weather and env_t is not None and getattr(env_t, "home", None):
            from nfl.data import weather as _wx
            weather = _wx.kickoff_weather(env_t.home, start, schedule_wind=env_t.wind, schedule_temp=env_t.temp,
                                          schedule_roof=env_t.roof, fetch=os.environ.get("NFL_WEATHER_FETCH", "1") != "0")
            if weather["source"] == "none":
                weather = None
        return _hl.price_row(stat, rows, team, logs.nfl_team_ctx(), env_t, _hl.AlphaLookup(logs.context), coef,
                             live=logs.nfl_live(), mu_engine=mu_raw if stat == "anytime_td" else None, weather=weather,
                             pid=rows[-1].get("player_id"), opp=opp, use_weather=use_weather)
    except Exception as exc:                                                            # noqa: BLE001
        log.warning("hierarchical price failed for %s (%s): legacy price kept", stat, exc)
        return None


def _sort_key(r: dict):
    return (not r["has_line"], not r["confluence"], -(r["beezy_score"] or 0), -abs(r["model_edge"] or 0.0),
            -(r["n_books"] or 0), r["player"])


# ----------------------------------------------------------------------------------------------- rotation fallback
ESPN_PATH = {"nfl": "football/nfl", "nhl": "hockey/nhl", "nba": "basketball/nba"}


def schedule_games(sport: str, now: datetime, hours: float = 36.0, fetch=None) -> list:
    """Scheduled, not-yet-finished games starting within `hours`: [{away, home, start}] (canonical codes). Public ESPN
    scoreboard; any failure -> []. `fetch(url) -> dict` is injectable for tests."""
    import requests
    fetch = fetch or (lambda u: requests.get(u, timeout=10, headers={"User-Agent": "beezy-fyi/1.0"}).json())
    out = []
    days = sorted({(now + timedelta(hours=h)).astimezone(_ET).strftime("%Y%m%d")
                   for h in range(0, int(hours) + 12, 12)})
    for d in days:
        try:
            data = fetch(f"https://site.api.espn.com/apis/site/v2/sports/{ESPN_PATH[sport]}/scoreboard?dates={d}")
        except Exception as exc:                                                       # noqa: BLE001
            log.info("%s schedule fetch failed: %s", sport, exc)
            continue
        for ev in (data or {}).get("events", []) or []:
            try:
                comp = ev["competitions"][0]
                if comp.get("status", {}).get("type", {}).get("completed"):
                    continue
                start = datetime.fromisoformat(str(ev.get("date")).replace("Z", "+00:00"))
                if not (now - timedelta(minutes=0) <= start <= now + timedelta(hours=hours)):
                    continue
                teams = {c.get("homeAway"): canon_team(sport, c["team"].get("abbreviation")) for c in comp["competitors"]}
                if teams.get("home") and teams.get("away"):
                    out.append({"away": teams["away"], "home": teams["home"], "start": start})
            except (KeyError, IndexError, ValueError, TypeError):
                continue
    return out


def game_key_of(sport: str, start: datetime, away: str, home: str) -> str:
    return f"{sport}:{start.astimezone(_ET).date().isoformat()}:{away}@{home}"


def rotation_rows(sport: str, logs: GameLogs, games: list, now: datetime, skip: set, roster=None,
                  priced: dict | None = None) -> tuple[list, dict]:
    """Projection-only rows (no line yet) for the highest-usage players of teams that play soon. `skip` holds
    (player_norm, stat) pairs that already have a line row; `priced` = {(team, stat): n priced players} counts toward each
    team's ROTATION_PER_TEAM quota so a team the books already cover gets no filler.

    `roster` (ev.rosters.Roster | None): only players the roster carries are projected, against the ROSTER team and its
    game. A player a COMPLETE roster does not carry (retired, released) is skipped and counted in `rotation_off_roster`;
    one the roster cannot judge (partial pull) keeps the last-game-log team. No roster = the old behaviour."""
    out, st = [], {"rotation_games": len(games), "rotation_rows": 0, "rotation_off_roster": 0,
                   "rotation_team_changed": 0}
    priced = priced or {}
    quota_per_team = ROTATION_PER_TEAM[sport]
    team_game = {}
    for g in games:
        team_game[g["away"]] = (g, g["home"])
        team_game[g["home"]] = (g, g["away"])
    for stat in ROTATION_STATS[sport]:
        table, sdef = logs.table_and_def(stat)
        if sdef is None:
            continue
        threshold = ROTATION_MIN_USAGE[sport][stat]
        per_team: dict = {}
        for name, rows in table.items():
            if str(name).startswith("id:") or len(rows) < MIN_GAMES or (name, stat) in skip:
                continue
            log_team = logs.team_of(rows)
            team, off = log_team, False
            if roster is not None:
                state, rteam = roster.lookup(name, log_team)
                off = state == "off"
                if state == "on":
                    team = rteam
            if team not in team_game:
                continue
            h = _hist(rows, sdef)
            usage = [g[1] for g in h[-10:] if g[1] is not None]
            if len(h) < MIN_GAMES or not usage or sum(usage) / len(usage) < threshold:
                continue
            if off:
                st["rotation_off_roster"] += 1                 # would have been listed without the roster
                continue
            per_team.setdefault(team, []).append((sum(usage) / len(usage), name, rows, team != log_team))
        for team, cands in per_team.items():
            room = quota_per_team - priced.get((team, stat), 0)
            if room <= 0:
                continue
            g, opp = team_game[team]
            gk = game_key_of(sport, g["start"], g["away"], g["home"])
            mins = (g["start"] - now).total_seconds() / 60.0
            for _, name, rows, moved in sorted(cands, key=lambda c: -c[0])[:room]:
                row = make_row(sport, stat, _display_name(rows, name), name, rows, sdef, logs, None, None, [], team, opp,
                               gk, g["start"], mins, has_line=False, roster=roster)
                if row is not None:
                    out.append(row)
                    st["rotation_team_changed"] += int(moved)
    out.sort(key=lambda r: (r["commence"] or "", r["game_key"], r["stat"], -(r["proj_mean"] or 0)))
    st["rotation_rows"] = len(out)
    return out, st


def _display_name(rows: list, fallback: str) -> str:
    last = rows[-1]
    for k in ("player_display_name", "player_name", "name"):
        v = last.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return fallback.title()


# ------------------------------------------------------------------------------------------------------------ IO
def load_parlay_events(sport: str, now: datetime, max_age_min: float = MAX_AGE_MIN):
    """Newest banked/live ParlayAPI prop events -> (events | None, meta).

    nhl / nba : the odds banker's pointer (OddsAccum/{parlay_key}/latest_props.json) -> raw snapshot
    nfl       : the sweep's per-event raw payloads (EVSweep/nfl_props/raw/{ET day}/{event}_{HHMMSS}.json), newest each
    """
    from mlb_core import storage
    if sport in ("nhl", "nba"):
        from ev import sweep_props_alerts as sw
        return sw.read_latest_props(sw.SPORTS[sport], now, max_age_min)
    events = {}
    for d in (now.astimezone(_ET).date(), (now + timedelta(days=1)).astimezone(_ET).date()):
        prefix = f"EVSweep/nfl_props/raw/{d.isoformat()}/"
        for key in storage.list_keys(prefix):
            base = key.rsplit("/", 1)[-1]
            if not base.endswith(".json") or "_" not in base:
                continue
            eid, hhmmss = base[:-5].rsplit("_", 1)
            if eid not in events or hhmmss > events[eid][0]:
                events[eid] = (hhmmss, key)
    if not events:
        return None, {"reason": "no NFL raw prop payloads banked for today/tomorrow"}
    out, newest_key, newest_stamp = [], None, ""
    for eid, (hhmmss, key) in events.items():
        try:
            obj = json.loads(storage.read_bytes(key))
        except Exception:                                                              # noqa: BLE001
            continue
        out.append(obj)
        if key.rsplit("/", 2)[-2] + hhmmss > newest_stamp:          # '{ET day}HHMMSS' sorts chronologically
            newest_stamp, newest_key = key.rsplit("/", 2)[-2] + hhmmss, key
    if not out:
        return None, {"reason": "NFL raw prop payloads unreadable"}
    age = None
    try:
        st = storage.stat(newest_key) or {}
        age = (now - st["mtime_utc"]).total_seconds() / 60.0 if st.get("mtime_utc") else None
    except Exception:                                                                  # noqa: BLE001
        age = None
    if age is not None and age > max_age_min:
        return None, {"reason": f"newest NFL payload is {age:.0f} min old (> {max_age_min:.0f})", "age_min": round(age, 1)}
    return out, {"age_min": None if age is None else round(age, 1), "banked_events": len(out)}


# ---- the second line source: BettingPros live (ev.bettingpros_events; free, hourly crawl, ParlayAPI event shape) ----------
def load_bp_events(sport: str, now: datetime, max_age_min: float = MAX_AGE_MIN):
    from ev import bettingpros_events as be
    return be.read_latest(sport, now, max_age_min)


# ---- the third line source: BestBook live (ev.bestbook_events; free backup filler feed, ParlayAPI event shape) -------------
def load_bestbook_events(sport: str, now: datetime, max_age_min: float = MAX_AGE_MIN):
    from ev import bestbook_events as bbe
    return bbe.read_latest(sport, now, max_age_min)


def _event_identity(sport: str, ev: dict) -> tuple | None:
    """(canonical away, canonical home, ET start date): the same game across feeds, whatever the team spelling."""
    try:
        start = datetime.fromisoformat(str(ev.get("commence_time")).replace("Z", "+00:00"))
    except ValueError:
        return None
    away, home = canon_team(sport, ev.get("away_team")), canon_team(sport, ev.get("home_team"))
    if not away or not home:
        return None
    return away, home, start.astimezone(_ET).date().isoformat()


def _book_stat_markets(sport: str, book: dict) -> dict:
    """{stat | market key (unmapped): [market dict]} for one bookmaker (main and alternate keys of a stat together)."""
    from ev.props import stat_of
    out: dict = {}
    for m in book.get("markets") or []:
        stat, _alt = stat_of(str(m.get("key", "")), sport)
        out.setdefault(stat or m.get("key"), []).append(m)
    return out


def merge_event_pair(sport: str, parlay_ev: dict, bp_ev: dict, parlay_age: float | None, bp_age: float | None,
                     counts: dict) -> dict:
    """One game seen by both feeds -> a copy of the base event whose bookmakers also carry what only incoming feed
    has. A (book, stat) present in BOTH goes to the FRESHER feed (strictly fresher incoming wins; a tie or an unknown
    age goes to base) and is never doubled, so a book is counted once per stat in every consensus."""
    from mlb_core.alerts.discord_routes import canonical_book
    books: dict = {}                                     # canonical key -> bookmaker dict (copies)
    for b in parlay_ev.get("bookmakers") or []:
        key = canonical_book(b.get("key", ""))
        books[key] = {**b, "markets": list(b.get("markets") or [])}
    bp_wins = bp_age is not None and parlay_age is not None and bp_age < parlay_age       # unknown age: base
    for b in bp_ev.get("bookmakers") or []:
        key = canonical_book(b.get("key", ""))
        mine = books.get(key)
        if mine is None:
            books[key] = {**b, "markets": list(b.get("markets") or [])}
            counts["books_added"] += 1
            continue
        have = _book_stat_markets(sport, mine)
        for stat, mkts in _book_stat_markets(sport, b).items():
            if stat in have:
                if bp_wins:
                    drop = {id(m) for m in have[stat]}
                    mine["markets"] = [m for m in mine["markets"] if id(m) not in drop] + list(mkts)
                    counts["book_stats_from_bp"] += 1
                else:
                    counts["book_stats_from_parlay"] += 1
            else:
                mine["markets"].extend(mkts)
                counts["book_stats_added"] += 1
    base_srcs = list(parlay_ev.get("sources") or [parlay_ev.get("source", "parlayapi")])
    inc_srcs = list(bp_ev.get("sources") or [bp_ev.get("source", "bettingpros")])
    merged_sources = list(dict.fromkeys(base_srcs + inc_srcs))
    return {**parlay_ev, "bookmakers": list(books.values()), "sources": merged_sources}


def union_events(sport: str, parlay: tuple, bp: tuple, bestbook: tuple | None = None) -> tuple:
    """(events, meta) of the line feeds, each `(events | None, meta)`.

    Which feeds count: those inside the LIVE window (MAX_AGE_MIN); only when none is, every feed inside the carry window
    (the board then flags `lines_stale`). That keeps a 10-hour-old banked snapshot from diluting a fresh feed's
    consensus. Games are matched on (canonical away, canonical home, ET start date); a game only one feed has is taken
    whole. `lines_source` lists the feeds used, `age_min` is the NEWEST used feed's age, and a feed that is down or too
    old only shows up under `sources` with its reason: one outage never blanks the board."""
    sources = {"parlayapi": parlay, "bettingpros": bp}
    if bestbook is not None:
        sources["bestbook"] = bestbook
    detail = {name: {**(meta or {}), "used": False, "events": len(evs or [])} for name, (evs, meta) in sources.items()}
    avail = {name: evs for name, (evs, _m) in sources.items() if evs}
    if not avail:
        reasons = "; ".join(f"{n}: {(detail[n].get('reason') or 'no events')}" for n in detail)
        return None, {"reason": reasons, "lines_source": [], "sources": detail}

    def age_of(n):
        return detail[n].get("age_min")

    live = [n for n in avail if age_of(n) is None or age_of(n) <= MAX_AGE_MIN]
    used = live or list(avail)
    for n in used:
        detail[n]["used"] = True
    ages = [age_of(n) for n in used if age_of(n) is not None]
    meta = {"age_min": min(ages) if ages else None, "lines_source": [n for n in ("parlayapi", "bettingpros", "bestbook") if n in used],
            "sources": detail}
    if len(used) == 1:
        return avail[used[0]], meta
    counts = {"games_matched": 0, "games_bp_only": 0, "games_parlay_only": 0, "books_added": 0, "book_stats_added": 0,
              "book_stats_from_bp": 0, "book_stats_from_parlay": 0}
    by_id: dict = {}
    out: list = []
    base_src = "parlayapi" if "parlayapi" in used else used[0]
    for ev in avail[base_src]:
        out.append(ev)
        ident = _event_identity(sport, ev)
        if ident is not None:
            by_id[ident] = len(out) - 1
    matched: set = set()
    other_sources = [n for n in used if n != base_src]
    for n in other_sources:
        for ev in avail[n]:
            ident = _event_identity(sport, ev)
            i = by_id.get(ident) if ident is not None else None
            if i is None:
                out.append(ev)
                key_only = "games_bp_only" if n == "bettingpros" else f"games_{n}_only"
                counts[key_only] = counts.get(key_only, 0) + 1
                if ident is not None:
                    by_id[ident] = len(out) - 1
                continue
            out[i] = merge_event_pair(sport, out[i], ev, age_of(base_src), age_of(n), counts)
            matched.add(i)
            counts["games_matched"] += 1
    if "parlayapi" in used:
        # ParlayAPI events are `out[0:len(avail["parlayapi"])]` (it is the base feed whenever it is used); `matched` also holds
        # indices of BP-only events that a third feed matched, so a plain length difference could go negative (NFL showed -10)
        counts["games_parlay_only"] = sum(1 for i in range(len(avail["parlayapi"])) if i not in matched)
    meta["merge"] = counts
    return out, meta


def load_events(sport: str, now: datetime, max_age_min: float = MAX_AGE_MIN):
    """Prop events from EVERY line source -> (events | None, meta); see union_events. Each feed is read inside its own
    guard: an exception or a missing file in one never costs the other."""
    parlay, bp, bb = (None, {"reason": "not loaded"}), (None, {"reason": "not loaded"}), (None, {"reason": "not loaded"})
    try:
        parlay = load_parlay_events(sport, now, max_age_min)
    except Exception as exc:                                                           # noqa: BLE001
        log.warning("%s ParlayAPI lines unavailable: %s", sport, exc)
        parlay = (None, {"reason": f"{type(exc).__name__}: {str(exc)[:160]}"})
    try:
        bp = load_bp_events(sport, now, max_age_min)
    except Exception as exc:                                                           # noqa: BLE001
        log.warning("%s BettingPros lines unavailable: %s", sport, exc)
        bp = (None, {"reason": f"{type(exc).__name__}: {str(exc)[:160]}"})
    try:
        bb = load_bestbook_events(sport, now, max_age_min)
    except Exception as exc:                                                           # noqa: BLE001
        log.warning("%s BestBook lines unavailable: %s", sport, exc)
        bb = (None, {"reason": f"{type(exc).__name__}: {str(exc)[:160]}"})
    return union_events(sport, parlay, bp, bb)


def _status(has_lines: bool, has_rotation: bool, logs_ok: bool) -> str:
    if not logs_ok:
        return "unavailable"
    if has_lines:
        return "live"
    return "awaiting_lines" if has_rotation else "idle"


_AUTO = object()


def ct_day(iso) -> str | None:
    """ISO start time -> its America/Chicago calendar date 'YYYY-MM-DD' (the site clock), None when unparseable."""
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(_CT).date().isoformat()


def order_and_cut(rows: list, limit: int = MAX_ROWS) -> tuple[list, int]:
    """Rows -> (the first `limit` in board order, number cut).

    Board order = nearest game day first (Central), and inside a day priced rows before rotation rows, then the
    display order (_sort_key). The cut therefore drops the FARTHEST games' rotation rows first, then their priced rows;
    it never removes a priced row of a near game to keep a rotation row of a far one. (Before 2026-10-17 the order was
    priced-first across ALL days, so a long window could cut a whole near day's rotation rows while keeping far days'.)"""
    ordered = sorted(rows, key=lambda r: (ct_day(r.get("commence")) or "9999-99-99", _sort_key(r)))
    return ordered[:limit], max(len(ordered) - limit, 0)


def build_games(sport: str, sched: list, events: list | None, now: datetime) -> list:
    """Every upcoming game the board knows: the schedule's games plus the ones only the lines know, deduplicated on
    game_key. -> [{game_key, away, home, start (ISO UTC)}] by start time. An event's own start time wins over the
    schedule's (it is the time the rows carry)."""
    out: dict = {}

    def put(away, home, start, prefer):
        if not away or not home or start is None or start <= now:
            return
        gk = game_key_of(sport, start, away, home)
        if gk not in out or prefer:
            out[gk] = {"game_key": gk, "away": away, "home": home, "start": start.astimezone(timezone.utc).isoformat()}

    for g in sched or []:
        put(g.get("away"), g.get("home"), g.get("start"), False)
    for ev in events or []:
        try:
            start = datetime.fromisoformat(str(ev.get("commence_time")).replace("Z", "+00:00"))
        except ValueError:
            continue
        put(canon_team(sport, ev.get("away_team")), canon_team(sport, ev.get("home_team")), start, True)
    return sorted(out.values(), key=lambda g: (g["start"], g["game_key"]))


def _run_game_models(sport: str, events, now: datetime) -> bool:
    """Refresh the NBA / NHL quantitative GAME-LINE payload (EVSweep/{sport}_games/predictions_latest.json) after a props
    compute. `events` are this compute's in-memory PROP events; they are handed to the game runner, which uses them only if
    they carry h2h/spreads/totals markets (prop feeds normally do not) and otherwise loads the banked game_lines snapshot
    itself. Failures are logged WITH the traceback and never fail the props compute. -> True if the runner completed.
    (Tests neutralise this hook in tests/conftest.py; production code carries no test detection.)"""
    try:
        if sport == "nba":
            from nba.game.run_daily import run_nba_game_predictions as run
        elif sport == "nhl":
            from nhl.game.run_daily import run_nhl_game_predictions as run
        else:
            return False
        run(write_gcs=True, now=now, events=events)
        return True
    except Exception:                                                                  # noqa: BLE001
        log.exception("%s game predictions run failed", sport)
        return False


def _run_game_board(sport: str, now: datetime) -> bool:
    """Refresh the NBA / NHL per-book GAME-LINE price board (EVSweep/{sport}_games/odds_board_latest.json) from the banked
    game_lines snapshot (no extra credits). A failed refresh keeps the previous file; failures are logged WITH the traceback and
    never fail the props compute. (Tests neutralise this hook in tests/conftest.py.)"""
    try:
        if sport not in ("nba", "nhl"):
            return False
        from ev import game_odds_board as _gob
        _gob.run(sport, now)
        return True
    except Exception:                                                                  # noqa: BLE001
        log.exception("%s game odds board run failed", sport)
        return False


def _run_soccer_game_predictions(now: datetime) -> bool:
    """Refresh the soccer game-line payload + per-book board (EVSweep/soccer_games/predictions_latest.json and
    odds_board_latest.json, the latter inside the soccer runner). Soccer has no props board in this module, so the
    scheduled --all --write-gcs run is its ONLY refresh: without this hook the soccer files would never be written
    in production. The runner itself refreshes ev.game_odds_board for "soccer" from the banked league snapshots
    (soccer_epl / soccer_uefa_champs_league, banked by the odds-bank game_lines jobs). Failures are logged WITH
    the traceback and never fail the other sports. (Tests neutralise this hook in tests/conftest.py.)"""
    try:
        from soccer.game.run_daily import run_soccer_predictions
        run_soccer_predictions(now=now, write=True)
        return True
    except Exception:                                                                  # noqa: BLE001
        log.exception("soccer game predictions run failed")
        return False


def _load_nba_tracking(now: datetime):
    """Prior-season tracking tables from the NBA/tracking mirror (nba.data.tracking), or None when nothing is mirrored. Only
    the prior COMPLETED season is read. (Tests neutralise this hook in tests/conftest.py.)"""
    try:
        tr = _trk.load([_trk.season_year_of(now.astimezone(timezone.utc).date().isoformat())])
        return tr if tr.years else None
    except Exception as exc:                                                           # noqa: BLE001
        log.info("nba tracking unavailable: %s", exc)
        return None


def _load_nba_spreads(now: datetime) -> dict:
    """{game_key: |consensus home spread|} from the banked NBA game-line snapshot (no extra credits); {} on any failure or a
    stale snapshot -- the minutes model then simply has no blowout term. (Tests neutralise this hook in tests/conftest.py.)"""
    out: dict = {}
    try:
        from nba.game import odds_feed
        from nba.game.run_daily import extract_game_market_odds
        events, _ = odds_feed.read_banked_game_lines("basketball_nba", now)
        for ev in events or []:
            try:
                start = datetime.fromisoformat(str(ev.get("commence_time")).replace("Z", "+00:00"))
            except ValueError:
                continue
            away, home = canon_team("nba", ev.get("away_team")), canon_team("nba", ev.get("home_team"))
            line = (extract_game_market_odds(ev).get("spreads") or {}).get("line")
            if away and home and line is not None and math.isfinite(float(line)):
                out[game_key_of("nba", start, away, home)] = abs(float(line))
    except Exception as exc:                                                           # noqa: BLE001
        log.info("nba spreads unavailable: %s", exc)
    return out


def run_sport(sport: str, now: datetime | None = None, logs: GameLogs | None = None, events=None, meta=None,
              games=None, write: bool = True, roster=_AUTO) -> dict:
    """Compute one sport's board. `logs` / `events` / `games` / `roster` are injectable (tests, local runs; roster=None
    means "no roster layer"). -> the payload dict that is also written to EVSweep/{sport}_props/projections_latest.json
    (and merged into that day's archive)."""
    now = now or datetime.now(timezone.utc)
    if sport not in PROJECT_STATS:
        raise ValueError(f"unknown sport {sport!r}; expected one of {SPORTS}")
    logs = logs or GameLogs.load(sport)
    base = {"sport": sport, "engine": engine_id(sport), "validated": False, "ts": now.isoformat(), "rows": [], "count": 0,
            "stats": list(PROJECT_STATS[sport]), "status": "unavailable", "lines_age_minutes": None, "lines_stale": False,
            "lines_source": [], "games": [], "horizon_hours": HORIZON_HOURS[sport],
            "gamelog_latest": None, "stale_gamelog": None, "summary": {}}
    if logs is None:
        base["summary"] = {"reason": f"{sport} game-log master unavailable"}
        return _finish(base, sport, write, now)
    if sport == "nhl" and trained_enabled():
        logs.set_trained(load_trained(now))
        try:
            logs.set_saves_history(_sh.load_settled_saves_history(now))
        except Exception as exc:                                                # noqa: BLE001
            log.info("saves hardening history unavailable: %s", exc)
    if sport == "nba" and write:
        logs.set_nba_spreads(_load_nba_spreads(now))
        logs.set_nba_tracking(_load_nba_tracking(now))
    base["gamelog_latest"] = logs.latest_date()
    gl = base["gamelog_latest"]
    if sport != "nfl" and gl:
        try:
            base["stale_gamelog"] = (now.date() - datetime.fromisoformat(gl).date()).days > STALE_GAMELOG_DAYS
        except ValueError:
            pass
    if events is None:
        try:
            events, meta = load_events(sport, now, CARRY_MAX_AGE_MIN)
        except Exception as exc:                                                       # noqa: BLE001
            log.warning("%s lines unavailable: %s", sport, exc)
            events, meta = None, {"reason": str(exc)[:200]}
    meta = meta or {}
    if roster is _AUTO:
        try:
            roster = _rosters.load(sport, now, write=write)
        except Exception as exc:                                                       # noqa: BLE001
            log.warning("%s roster layer unavailable: %s", sport, exc)
            roster = None
    line_rows, lstats = ([], {})
    if events:
        line_rows, lstats = build_line_rows(sport, events, logs, now, roster)
    covered = {(r["player_norm"], r["stat"]) for r in line_rows}
    priced: dict = {}
    for r in line_rows:
        priced[(r["team"], r["stat"])] = priced.get((r["team"], r["stat"]), 0) + 1
    rot, rstats, sched = [], {}, []
    try:
        sched = games if games is not None else schedule_games(sport, now, HORIZON_HOURS[sport])
        rot, rstats = rotation_rows(sport, logs, sched, now, covered, roster, priced)
    except Exception as exc:                                                           # noqa: BLE001
        log.warning("%s rotation fallback failed: %s", sport, exc)
    rows, cut = order_and_cut(line_rows + rot)
    if sport == "nfl":
        # Live-serving gate (Argon D3, see NFL_HIER_AUDIT_STATS): hierarchical rows never reach the published
        # board, regardless of NFL_ENGINE / NFL_HIER_STATS. The rows were still computed above (the engine runs;
        # only publication is gated), and they are counted here so a silent disappearance is visible in the summary.
        served = live_served_rows(rows)
        if len(served) != len(rows):
            lstats["hier_rows_gated"] = len(rows) - len(served)
            rows = served
    game_list = build_games(sport, sched, events, now)
    per_game: dict = {}
    for r in rows:
        n = per_game.setdefault(r["game_key"], [0, 0])
        n[0] += 1
        n[1] += int(bool(r["has_line"]))
    for g in game_list:
        g["n_rows"], g["n_priced"] = per_game.get(g["game_key"], (0, 0))
    age = meta.get("age_min")
    source = meta.get("lines_source")
    if source is None:
        source = ["parlayapi"] if events else []
    base.update(rows=rows, count=len(rows), lines_age_minutes=age, lines_source=list(source), games=game_list,
                lines_stale=bool(line_rows) and age is not None and age > MAX_AGE_MIN,
                status=_status(bool(line_rows), bool(rot), True),
                summary={**{k: v for k, v in meta.items() if k not in ("age_min", "lines_source")}, **lstats, **rstats,
                         "line_rows": len(line_rows), "confluence": sum(1 for r in line_rows if r["confluence"]),
                         "rows_cut": cut, "games": len(game_list),
                         "roster": None if roster is None else roster.summary()})
    if write and events:
        # the /ev/odds screen's full per-book matrix rides the same compute: same unioned events, no extra credits
        from ev import odds_board as _ob
        _ob.write_board(_ob.build_board(sport, events, now, meta), sport)
        _run_game_models(sport, events, now)
        _run_game_board(sport, now)
    return _finish(base, sport, write, now)


# ------------------------------------------------------------------------------------------------- yesterday archive
# EVSweep/{sport}_props/archive/{YYYY-MM-DD}.json, one file per America/Chicago GAME day (the day of the game's start,
# not of the compute). Every compute merges its rows in, so the day's file ends up holding each row's FINAL pre-game state:
# a row replaces its older version, and a row the live payload no longer carries (its game started) is KEPT. The one
# exception: a priced row is never replaced by a no-line rotation row (a book pulling a prop must not erase the price
# that was posted for hours). Writes are best-effort (a compute never fails on them). `date=YYYY-MM-DD` on
# GET /api/public/projections reads a day back; today's own file also works and includes games already under way.
def row_key(r: dict) -> str:
    return f"{r.get('game_key')}|{r.get('player_norm')}|{r.get('stat')}"


def merge_archive(existing: dict | None, rows: list, games: list, payload: dict, day: str, now: datetime) -> dict:
    """Pure: the archive file for `day` after merging `rows` / `games` (all of them that day's) into `existing`."""
    arch = existing if isinstance(existing, dict) else {}
    cur_rows = dict(arch.get("rows") or {})
    for r in rows:
        k = row_key(r)
        old = cur_rows.get(k)
        if old is not None and old.get("has_line") and not r.get("has_line"):
            continue
        cur_rows[k] = r
    cur_games = dict(arch.get("games") or {})
    for g in games:
        cur_games[g["game_key"]] = {**cur_games.get(g["game_key"], {}), **g}
    return {"sport": payload["sport"], "day": day, "engine": payload.get("engine"), "updated_at": now.isoformat(),
            "stats": payload.get("stats"), "gamelog_latest": payload.get("gamelog_latest"),
            "lines_source": payload.get("lines_source"), "lines_age_minutes": payload.get("lines_age_minutes"),
            "rows": cur_rows, "games": cur_games}


def write_archive(payload: dict, now: datetime) -> list:
    """Merge a computed payload into the per-day archive files. -> the days written. Never raises."""
    written = []
    try:
        from mlb_core import storage
        sport = payload["sport"]
        by_day: dict = {}
        for r in payload.get("rows") or []:
            d = ct_day(r.get("commence"))
            if d:
                by_day.setdefault(d, ([], []))[0].append(r)
        for g in payload.get("games") or []:
            d = ct_day(g.get("start"))
            if d:
                by_day.setdefault(d, ([], []))[1].append(g)
        for day, (rows, games) in sorted(by_day.items()):
            key = ARCHIVE_KEY.format(sport=sport, day=day)
            try:
                try:
                    existing = json.loads(storage.read_bytes(key)) if storage.exists(key) else None
                except Exception:                                                      # noqa: BLE001
                    existing = None
                merged = merge_archive(existing, rows, games, payload, day, now)
                storage.write_bytes(json.dumps(merged, default=str).encode(), key)
                written.append(day)
            except Exception as exc:                                                   # noqa: BLE001
                log.warning("archive write failed for %s %s: %s", sport, day, exc)
    except Exception as exc:                                                           # noqa: BLE001
        log.warning("archive write failed: %s", exc)
    return written


def archive_to_payload(arch: dict) -> dict:
    """A stored archive file -> the board payload shape (+ archive: true, date)."""
    rows = sorted((arch.get("rows") or {}).values(), key=lambda r: (r.get("commence") or "", _sort_key(r)))
    games = sorted((arch.get("games") or {}).values(), key=lambda g: (g.get("start") or "", g.get("game_key") or ""))
    per_game: dict = {}
    for r in rows:
        n = per_game.setdefault(r["game_key"], [0, 0])
        n[0] += 1
        n[1] += int(bool(r.get("has_line")))
    for g in games:
        g["n_rows"], g["n_priced"] = per_game.get(g["game_key"], (0, 0))
    has_lines = any(r.get("has_line") for r in rows)
    return {"sport": arch.get("sport"), "engine": arch.get("engine"), "validated": False,
            "status": _status(has_lines, bool(rows), True), "ts": arch.get("updated_at"), "rows": rows, "count": len(rows),
            "games": games, "stats": arch.get("stats") or [], "gamelog_latest": arch.get("gamelog_latest"),
            "stale_gamelog": None, "lines_age_minutes": None, "lines_stale": False,
            "lines_source": arch.get("lines_source") or [], "archive": True, "date": arch.get("day"),
            "summary": {"archive_day": arch.get("day"), "rows": len(rows), "line_rows": sum(1 for r in rows if r.get("has_line")),
                        "games": len(games)}}


_ARCHIVE_CACHE: dict = {}                   # (sport, day) -> (time.monotonic(), payload | None)
ARCHIVE_CACHE_SEC = 300


def get_archive(sport: str, day: str) -> dict | None:
    """The archived board for a Central game day, or None when there is none. Cached 5 minutes per (sport, day)."""
    from mlb_core import storage
    sport = (sport or "").lower()
    if sport not in PROJECT_STATS:
        raise ValueError(f"unknown sport {sport!r}; expected one of {SPORTS}")
    k = (sport, day)
    with _PROJ_LOCK:
        hit = _ARCHIVE_CACHE.get(k)
    if hit and time.monotonic() - hit[0] < ARCHIVE_CACHE_SEC:
        return hit[1]
    payload = None
    try:
        payload = archive_to_payload(json.loads(storage.read_bytes(ARCHIVE_KEY.format(sport=sport, day=day))))
    except Exception:                                                                  # noqa: BLE001
        payload = None
    with _PROJ_LOCK:
        _ARCHIVE_CACHE[k] = (time.monotonic(), payload)
    return payload


def _finish(payload: dict, sport: str, write: bool, now: datetime | None = None) -> dict:
    if write and payload["status"] != "unavailable":
        try:
            from mlb_core import storage
            storage.write_bytes(json.dumps(payload, default=str).encode(), OUT_KEY.format(sport=sport))
        except Exception as exc:                                                       # noqa: BLE001
            log.warning("projections write failed for %s: %s", sport, exc)
        write_archive(payload, now or datetime.now(timezone.utc))
    log.info("PROJECTIONS %s", json.dumps({k: payload[k] for k in ("sport", "status", "count", "lines_source", "summary")},
                                          default=str))
    return payload


# ------------------------------------------------------------------------------------------------ serving fast path
# The HTTP route must never compute a board inline (a cold compute downloads the sport's game-log master from GCS and
# blocks SSR for 5-15 s). get_daily_projections() is the web path: process cache -> the precomputed JSON in GCS -> if
# that is missing or stale, kick ONE background recompute and answer immediately with whatever exists (stale-while-
# revalidate). Only the CLI / scheduled job passes allow_compute=True. NOTE: the background thread only makes progress
# while Cloud Run has CPU allocated; the scheduled job (deploy/setup_daily_projections_job.sh) is the dependable path.
PROJ_CACHE_TTL_SEC = 5 * 60            # the scheduled job rewrites the file every 30 min; a 30-min process cache doubled the lag
PROJ_STALE_MINUTES = 90
PROJ_RECOMPUTE_SEC = 600
_PROJ_CACHE: dict = {}                      # sport -> (time.monotonic() loaded, payload)
_PROJ_LOCK = threading.Lock()
_PROJ_LAST_TRY: dict = {}                   # sport -> time.monotonic() of the last compute attempt (success or not)


def read_precomputed(sport: str) -> dict | None:
    """The stored board (one small JSON), or None when it has not been produced yet."""
    from mlb_core import storage
    try:
        return json.loads(storage.read_bytes(OUT_KEY.format(sport=sport)))
    except Exception:                                                                  # noqa: BLE001
        return None


def payload_age_min(payload: dict | None, now: datetime | None = None) -> float | None:
    try:
        return ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(str(payload.get("ts")))).total_seconds() / 60.0
    except Exception:                                                                  # noqa: BLE001
        return None


def _cache_put(sport: str, payload: dict) -> None:
    with _PROJ_LOCK:
        _PROJ_CACHE[sport] = (time.monotonic(), payload)


def _bg_compute_and_save(sport: str) -> None:
    """Thread target: compute, write EVSweep/{sport}_props/projections_latest.json, refresh the process cache."""
    try:
        fresh = run_sport(sport, write=True)
    except Exception:                                                                  # noqa: BLE001
        log.exception("background projections compute failed for %s", sport)
        return
    if fresh.get("status") != "unavailable":
        _cache_put(sport, fresh)


def _kick_background(sport: str) -> bool:
    """Start one daemon recompute thread unless one was started within PROJ_RECOMPUTE_SEC (success or not)."""
    with _PROJ_LOCK:
        if time.monotonic() - _PROJ_LAST_TRY.get(sport, -1e9) < PROJ_RECOMPUTE_SEC:
            return False
        _PROJ_LAST_TRY[sport] = time.monotonic()
    threading.Thread(target=_bg_compute_and_save, args=(sport,), daemon=True, name=f"proj-{sport}").start()
    return True


def get_daily_projections(sport: str, limit: int = MAX_ROWS, allow_compute: bool = False) -> dict | None:
    """One sport's board payload (rows capped at `limit`), or None when none exists yet (a background compute is then
    already running). allow_compute=False (every web request) never computes inline; True (CLI/job) does."""
    sport = (sport or "").lower()
    if sport not in PROJECT_STATS:
        raise ValueError(f"unknown sport {sport!r}; expected one of {SPORTS}")
    now = datetime.now(timezone.utc)
    with _PROJ_LOCK:
        hit = _PROJ_CACHE.get(sport)
    payload = hit[1] if hit and time.monotonic() - hit[0] < PROJ_CACHE_TTL_SEC else None
    if payload is None:
        payload = read_precomputed(sport)
        if payload is not None:
            _cache_put(sport, payload)
    age = payload_age_min(payload, now) if payload else None
    if payload is None or age is None or age > PROJ_STALE_MINUTES:
        if allow_compute:
            fresh = run_sport(sport, now=now, write=True)
            if fresh.get("status") != "unavailable" or payload is None:
                payload = fresh
                _cache_put(sport, fresh)
        else:
            _kick_background(sport)
    if not payload:
        return None
    # Serving gate on the READ path too: a board written before the gate existed (or by any other writer) must
    # not serve hierarchical NFL rows either. See NFL_HIER_AUDIT_STATS.
    rows = live_served_rows(payload.get("rows") or [])
    if len(rows) != len(payload.get("rows") or []):
        payload = {**payload, "rows": rows, "count": len(rows)}
    return payload if len(rows) <= limit else {**payload, "rows": rows[:limit]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sport", choices=[*SPORTS, "mlb", "all"], default="all")
    ap.add_argument("--all", action="store_true", help="every sport (the default; spelled out for the scheduled job)")
    ap.add_argument("--write-gcs", action="store_true",
                    help="write EVSweep/{sport}_props/projections_latest.json (the default; spelled out for the job)")
    ap.add_argument("--no-write", action="store_true", help="print the summary, do not write to GCS/local storage")
    ap.add_argument("--nfl-engine", choices=["ewm", "hierarchical"], default=None,
                    help="NFL pricing engine (default: $NFL_ENGINE, else ewm). hierarchical = nfl.models.hierarchical_props")
    a = ap.parse_args(argv)
    if a.nfl_engine:
        import os
        os.environ["NFL_ENGINE"] = a.nfl_engine
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.write_gcs and a.no_write:
        ap.error("--write-gcs and --no-write are mutually exclusive")
    everything = a.all or a.sport == "all"
    sports = SPORTS if everything else (() if a.sport == "mlb" else (a.sport,))
    failed = []
    if everything or a.sport == "mlb":
        # MLB has no projection board: only the odds-board matrix, read from the snapshot job's banked props
        from ev import odds_board as _ob
        try:
            b = _ob.run_mlb(write=not a.no_write)
            print(f"mlb: odds board status={b['status']} rows={b['count']} summary={json.dumps(b.get('summary'), default=str)}")
        except Exception:                                                              # noqa: BLE001
            log.exception("mlb odds board failed")
    for sp in sports:
        # the job path: synchronous compute, one sport at a time; a failing sport never costs the others their refresh
        try:
            p = run_sport(sp, write=not a.no_write)
        except Exception:                                                              # noqa: BLE001
            log.exception("projections compute failed for %s", sp)
            failed.append(sp)
            continue
        print(f"{sp}: status={p['status']} rows={p['count']} games={len(p.get('games') or [])} "
              f"lines_source={p.get('lines_source')} lines_age={p['lines_age_minutes']} "
              f"gamelog_latest={p['gamelog_latest']} summary={json.dumps(p['summary'], default=str)}")
        if p["status"] == "unavailable":
            failed.append(sp)
    if everything and not a.no_write:
        # soccer has no props board above; the full scheduled run is its only predictions + board refresh
        if _run_soccer_game_predictions(datetime.now(timezone.utc)):
            print("soccer: game predictions + odds board refreshed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
