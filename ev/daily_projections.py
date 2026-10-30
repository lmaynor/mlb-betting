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
            is tied to the UNCALIBRATED mean (cv was measured against raw trailing means).
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
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
from scipy.stats import nbinom, norm

from ev import rosters as _rosters
from ev.props import DFS_BOOKS, OFFSHORE_BOOKS, build_consensus, parse_event_props
from mlb_core.data import cockpit_form as cf
from mlb_core.odds.utils import american_to_decimal
from nfl.props import env_adjust as _ea

log = logging.getLogger("ev.daily_projections")
_ET = ZoneInfo("America/New_York")
_CT = ZoneInfo("America/Chicago")               # the site clock: archive days and the Today / Yesterday split are Central

ENGINE = "empirical_ewm_v2"
SPORTS = ("nfl", "nhl", "nba")
OUT_KEY = "EVSweep/{sport}_props/projections_latest.json"
ARCHIVE_KEY = "EVSweep/{sport}_props/archive/{day}.json"      # {day} = the game's America/Chicago start date

# stat -> distribution family
PROJECT_STATS = {
    "nfl": {"pass_yds": "normal", "pass_tds": "negbin", "pass_completions": "normal", "pass_attempts": "normal",
            "interceptions": "negbin", "rush_yds": "normal", "rush_attempts": "normal", "rec_yds": "normal",
            "receptions": "negbin", "anytime_td": "anytime"},
    # pp_points is NOT here: the NHL skater master carries power_play_goals but no power-play assists, so a PP-points
    # projection cannot be built from it (see CONTEXT s21). It is registered on the site as a data gap, not faked.
    "nhl": {"sog": "negbin", "goals": "negbin", "assists": "negbin", "points": "negbin", "saves": "normal",
            "blocked_shots": "negbin"},
    "nba": {"points": "normal", "rebounds": "negbin", "assists": "negbin", "threes": "negbin", "pra": "normal",
            "pr": "normal", "pa": "normal", "ra": "normal", "steals": "negbin", "blocks": "negbin"},
}
STAT_LABEL = {
    "pass_yds": "Pass Yds", "rush_yds": "Rush Yds", "rec_yds": "Rec Yds", "receptions": "Receptions",
    "pass_tds": "Pass TDs", "anytime_td": "Anytime TD", "pass_completions": "Completions",
    "pass_attempts": "Pass Attempts", "rush_attempts": "Carries", "interceptions": "Interceptions",
    "sog": "Shots on Goal", "goals": "Goals", "points": "Points",
    "assists": "Assists", "saves": "Saves", "blocked_shots": "Blocked Shots",
    "rebounds": "Rebounds", "threes": "3-Pointers", "pra": "Pts+Reb+Ast", "pr": "Pts+Reb", "pa": "Pts+Ast",
    "ra": "Reb+Ast", "steals": "Steals", "blocks": "Blocks",
}

HALFLIFE = {"nfl": 3.0, "nhl": 8.0, "nba": 8.0}      # games
# Per-stat overrides of (halflife, ewm_weight, opp_k) -- ONLY where a walk-forward replay on held-out seasons showed a
# gain past a multiplicity-corrected bar (nfl.props.engine_eval; selected on 2021-23 / 2021-22, scored on the
# seasons after, never used for selection: rec_yds log score +0.027 / +0.032 per row, MAE -0.23 / -0.27 yds;
# anytime_td log score +0.012, Brier -0.0033). Every other stat keeps the original hand-set values.
STAT_PARAMS = {
    "nfl": {"rec_yds": {"halflife": 7.0, "ewm_weight": 1.0, "opp_k": 200.0},
            "anytime_td": {"halflife": 7.0, "ewm_weight": 0.3, "opp_k": 200.0}},
}


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
                              "blocked_shots": 14.0},
                      "nba": {"points": 20.0, "rebounds": 20.0, "assists": 20.0, "threes": 20.0, "pra": 20.0,
                              "pr": 20.0, "pa": 20.0, "ra": 20.0, "steals": 20.0, "blocks": 20.0}}
ROTATION_STATS = {"nfl": ("rec_yds", "receptions", "rush_yds", "pass_yds", "pass_attempts", "pass_completions",
                          "rush_attempts"),
                  "nhl": ("sog", "points", "saves", "blocked_shots"),
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
    def __new__(cls, implied_total, team_spread, wind=None, roof=None, temp=None):
        return super().__new__(cls, (float(implied_total), float(team_spread)))

    def __init__(self, implied_total, team_spread, wind=None, roof=None, temp=None):
        self.implied_total = float(implied_total)
        self.team_spread = float(team_spread)
        self.wind = wind
        self.roof = roof
        self.temp = temp


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
        )
        for tm, gd, it, sp, w, r, tp in zip(
            t["team"], t["gameday"], t["implied_total"], t["team_spread"],
            t["wind"] if has_w else [None] * len(t),
            t["roof"] if has_r else [None] * len(t),
            t["temp"] if has_tp else [None] * len(t),
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
    def context(self, stat: str) -> StatContext:
        with self._lock:
            ctx = self._ctx.get(stat)
            if ctx is None:
                ctx = self._ctx[stat] = self._build_context(stat)
            return ctx

    def _build_context(self, stat: str) -> StatContext:
        table, sdef = self.table_and_def(stat)
        ctx = StatContext()
        if sdef is None or not table:
            return ctx
        got: dict = {}
        exp: dict = {}
        cnt: dict = {}
        var_sum = mean_sq_sum = mean_sum = 0.0
        n_players = 0
        for name, rows in table.items():
            if str(name).startswith("id:") or len(rows) < MIN_GAMES:
                continue
            h = _hist(rows, sdef)
            if len(h) < MIN_GAMES:
                continue
            cur = h[-1][2]
            season = [g for g in h if g[2] == cur] or h
            base = sum(g[0] for g in season) / len(season)
            if base <= 0:
                continue
            for v, _, s, opp in season:
                oc = canon_team(self.sport, opp)
                if oc is None:
                    continue
                got[oc] = got.get(oc, 0.0) + v
                exp[oc] = exp.get(oc, 0.0) + base
                cnt[oc] = cnt.get(oc, 0) + 1
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
        if n_players and mean_sq_sum > 0:
            # method of moments over the whole population: var = mu + alpha*mu^2 (NegBin), var = (cv*mu)^2 (Normal)
            ctx.alpha = min(max((var_sum - mean_sum) / mean_sq_sum, ALPHA_RANGE[0]), ALPHA_RANGE[1])
            ctx.cv = min(max(math.sqrt(var_sum / mean_sq_sum), 0.1), 2.0)
            ctx.league_mean = mean_sum / n_players
        ctx.n_players = n_players
        return ctx


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


def p_over(family: str, mu: float, line: float, ctx: StatContext, sd_mu: float | None = None) -> float | None:
    """P(X > line) under the stat's family. `line` is the book's number (x.5 for nearly everything). `sd_mu` is the mean
    the NORMAL family's spread is computed from (default: mu) -- a calibrated mean keeps the uncalibrated spread."""
    if mu is None or not math.isfinite(mu) or line is None:
        return None
    if family == "anytime":
        return float(1.0 - math.exp(-max(mu, 0.0))) if line < 1.0 else float(1.0 - _pois_cdf(math.floor(line), mu))
    if mu <= 0:
        return 0.0 if line >= 0 else 1.0
    if family == "negbin":
        n = 1.0 / ctx.alpha
        return float(1.0 - nbinom.cdf(math.floor(line), n, n / (n + mu)))
    ref = mu if sd_mu is None else sd_mu
    sd = max(ctx.cv * ref, MIN_SD_FRAC * ref, 1e-6)
    return float(1.0 - norm.cdf((line - mu) / sd))


def blend_p_over(p_model_over: float | None, p_mkt_over: float | None, c: float = BLEND_C) -> float | None:
    """Market-anchored posterior: logit(p) = logit(market) + c * (logit(model) - logit(market)).

    This is the form the repo's own model-vs-market studies test (blend coefficient `c`; docs/analysis/
    bettingpros_model_vs_market_2026-10-15.md): c = 0 trusts the market entirely, c = 1 trusts the model entirely. Those
    studies measured c = +0.02 .. +0.30 for the TRAINED research models; nothing has been measured for this empirical
    engine, so BLEND_C is a deliberately small, UNVALIDATED shrinkage prior. It is display-only: it feeds neither the
    edge, the confluence flag nor the Beezy Score. None unless both engines have a probability."""
    if p_model_over is None or p_mkt_over is None:
        return None
    lo, hi = 0.01, 0.99
    pm = min(max(float(p_model_over), lo), hi)
    pk = min(max(float(p_mkt_over), lo), hi)
    z = math.log(pk / (1 - pk)) + c * (math.log(pm / (1 - pm)) - math.log(pk / (1 - pk)))
    return 1.0 / (1.0 + math.exp(-z))


def proj_range(family: str, mu: float, ctx: StatContext, lo: float = 0.10, hi: float = 0.90,
               sd_mu: float | None = None):
    """(floor, ceiling) = the lo / hi quantiles of the projection's own distribution, or (None, None) for the
    one-sided anytime market. Yards are floored at 0 (the board shows a rate-style range, not a loss). These describe
    the MODEL'S spread (league-wide within-player variance), not an interval with validated coverage."""
    if mu is None or not math.isfinite(mu) or family == "anytime":
        return None, None
    if mu <= 0:
        return 0.0, 0.0
    if family == "negbin":
        n = 1.0 / ctx.alpha
        pr = n / (n + mu)
        return float(nbinom.ppf(lo, n, pr)), float(nbinom.ppf(hi, n, pr))
    ref = mu if sd_mu is None else sd_mu
    sd = max(ctx.cv * ref, MIN_SD_FRAC * ref, 1e-6)
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
            row = make_row(sport, stat, qs[0].player, pn, rows, sdef, logs, pt, cons, at_pt, team, opp, gk, start, mins)
            if row is None:
                st["thin_history"] += 1
                continue
            out.append(row)
    out.sort(key=_sort_key)
    st["rows"] = len(out)
    return out, st


def make_row(sport, stat, player, pn, rows, sdef, logs, line, cons, at_pt, team, opp, gk, start, mins,
             has_line: bool = True) -> dict | None:
    family = PROJECT_STATS[sport][stat]
    ctx = logs.context(stat)
    hl, ewm_w, _ = stat_params(sport, stat)
    proj = project_mu(_hist(rows, sdef), hl, ctx.opp_factor.get(opp, 1.0) if opp else 1.0, ewm_w)
    if proj is None:
        return None
    mu_raw = proj["mu"]

    # 1. NBA injury scratch & teammate usage redistribution
    if sport == "nba" and team is not None:
        team_abs = logs.nba_absences().get(team, {})
        if team_abs.get(pn, 0.0) >= 0.8:
            return None
        teammate_abs_wt = sum(w for p, w in team_abs.items() if p != pn)
        if teammate_abs_wt > 0:
            nba_boost = min(1.20, 1.0 + 0.04 * min(teammate_abs_wt, 4.0))
            mu_raw = mu_raw * nba_boost

    # 2. NHL xG shot quality & finishing regression
    if sport == "nhl" and stat in ("goals", "points"):
        recent_goals = sum(float(r.get("goals") if isinstance(r, dict) else r[0])
                           for r in rows[-15:]
                           if ((isinstance(r, dict) and r.get("goals") is not None)
                               or (isinstance(r, (list, tuple)) and len(r) > 0 and r[0] is not None)))
        recent_sog = sum(float((r.get("shots") if r.get("shots") is not None else r.get("sog")) if isinstance(r, dict) else r[1])
                         for r in rows[-15:]
                         if ((isinstance(r, dict) and (r.get("shots") is not None or r.get("sog") is not None))
                             or (isinstance(r, (list, tuple)) and len(r) > 1 and r[1] is not None)))
        if recent_sog >= 15.0:
            fin_ratio = (recent_goals + 3.0) / (recent_sog * 0.100 + 3.0)
            regress = float(np.clip(1.0 / (0.4 * fin_ratio + 0.6), 0.85, 1.15)) if stat == "goals" else float(np.clip(1.0 / (0.6 * fin_ratio + 0.4), 0.90, 1.10))
            mu_raw = mu_raw * regress

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
    p_model_over = p_over(family, mu, line, ctx, sd_mu=mu_raw) if line is not None else None
    p_floor, p_ceil = proj_range(family, mu, ctx, sd_mu=mu_raw)
    form = cf.shape_form(rows, sdef, line)
    p_mkt_over = cons.p_over if cons is not None else None
    n_books = cons.n_books if cons is not None else (len(_real(at_pt)) if at_pt else None)
    best = best_prices(at_pt) if at_pt else {"over": None, "under": None}
    ef = edge_fields(p_model_over, p_mkt_over, best, cons.n_books if cons is not None else None)
    side = ef["side"]
    p_model_side = None if p_model_over is None or side is None else (p_model_over if side == "over" else 1 - p_model_over)
    p_mkt_side = None if p_mkt_over is None or side is None else (p_mkt_over if side == "over" else 1 - p_mkt_over)
    score, basis = score_inputs(ef, cons.n_books if cons is not None else None, p_model_side, p_mkt_side)
    bo, bu = best["over"], best["under"]
    return {
        "sport": sport, "stat": stat, "stat_label": STAT_LABEL.get(stat, stat), "player": player, "player_norm": pn,
        "team": team, "opp": opp, "game_key": gk, "commence": None if start is None else start.isoformat(),
        "mins_to_start": None if mins is None else round(mins, 1),
        "has_line": bool(has_line and line is not None), "line": _f(line, 2), "consensus_line": _f(line, 2),
        "side": side, "proj_mean": _f(mu, 3), "proj_raw": _f(mu_raw, 3), "calib_mult": _f(mult, 4),
        "proj_p10": _f(p_floor, 1), "proj_p90": _f(p_ceil, 1),
        "p_over_model": _f(p_model_over),
        "p_over_blend": _f(blend_p_over(p_model_over, p_mkt_over)),
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
        "validated": False, "engine": ENGINE,
    }


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
                               gk, g["start"], mins, has_line=False)
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
    """One game seen by both feeds -> a copy of the ParlayAPI event whose bookmakers also carry what only BettingPros
    has. A (book, stat) present in BOTH goes to the FRESHER feed (strictly fresher BettingPros wins; a tie or an unknown
    age goes to ParlayAPI) and is never doubled, so a book is counted once per stat in every consensus."""
    from mlb_core.alerts.discord_routes import canonical_book
    books: dict = {}                                     # canonical key -> bookmaker dict (copies)
    for b in parlay_ev.get("bookmakers") or []:
        key = canonical_book(b.get("key", ""))
        books[key] = {**b, "markets": list(b.get("markets") or [])}
    bp_wins = bp_age is not None and parlay_age is not None and bp_age < parlay_age       # unknown age: ParlayAPI
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
    return {**parlay_ev, "bookmakers": list(books.values()), "sources": ["parlayapi", "bettingpros"]}


def union_events(sport: str, parlay: tuple, bp: tuple) -> tuple:
    """(events, meta) of the two feeds, each `(events | None, meta)`.

    Which feeds count: those inside the LIVE window (MAX_AGE_MIN); only when none is, every feed inside the carry window
    (the board then flags `lines_stale`). That keeps a 10-hour-old banked snapshot from diluting a fresh feed's
    consensus. Games are matched on (canonical away, canonical home, ET start date); a game only one feed has is taken
    whole. `lines_source` lists the feeds used, `age_min` is the NEWEST used feed's age, and a feed that is down or too
    old only shows up under `sources` with its reason: one outage never blanks the board."""
    sources = {"parlayapi": parlay, "bettingpros": bp}
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
    meta = {"age_min": min(ages) if ages else None, "lines_source": [n for n in ("parlayapi", "bettingpros") if n in used],
            "sources": detail}
    if len(used) == 1:
        return avail[used[0]], meta
    counts = {"games_matched": 0, "games_bp_only": 0, "games_parlay_only": 0, "books_added": 0, "book_stats_added": 0,
              "book_stats_from_bp": 0, "book_stats_from_parlay": 0}
    by_id: dict = {}
    out: list = []
    for ev in avail["parlayapi"]:
        out.append(ev)
        ident = _event_identity(sport, ev)
        if ident is not None:
            by_id[ident] = len(out) - 1
    matched: set = set()
    for ev in avail["bettingpros"]:
        ident = _event_identity(sport, ev)
        i = by_id.get(ident) if ident is not None else None
        if i is None:
            out.append(ev)
            counts["games_bp_only"] += 1
            continue
        out[i] = merge_event_pair(sport, out[i], ev, age_of("parlayapi"), age_of("bettingpros"), counts)
        matched.add(i)
        counts["games_matched"] += 1
    counts["games_parlay_only"] = len(avail["parlayapi"]) - len(matched)
    meta["merge"] = counts
    return out, meta


def load_events(sport: str, now: datetime, max_age_min: float = MAX_AGE_MIN):
    """Prop events from EVERY line source -> (events | None, meta); see union_events. Each feed is read inside its own
    guard: an exception or a missing file in one never costs the other."""
    parlay, bp = (None, {"reason": "not loaded"}), (None, {"reason": "not loaded"})
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
    return union_events(sport, parlay, bp)


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


def run_sport(sport: str, now: datetime | None = None, logs: GameLogs | None = None, events=None, meta=None,
              games=None, write: bool = True, roster=_AUTO) -> dict:
    """Compute one sport's board. `logs` / `events` / `games` / `roster` are injectable (tests, local runs; roster=None
    means "no roster layer"). -> the payload dict that is also written to EVSweep/{sport}_props/projections_latest.json
    (and merged into that day's archive)."""
    now = now or datetime.now(timezone.utc)
    if sport not in PROJECT_STATS:
        raise ValueError(f"unknown sport {sport!r}; expected one of {SPORTS}")
    logs = logs or GameLogs.load(sport)
    base = {"sport": sport, "engine": ENGINE, "validated": False, "ts": now.isoformat(), "rows": [], "count": 0,
            "stats": list(PROJECT_STATS[sport]), "status": "unavailable", "lines_age_minutes": None, "lines_stale": False,
            "lines_source": [], "games": [], "horizon_hours": HORIZON_HOURS[sport],
            "gamelog_latest": None, "stale_gamelog": None, "summary": {}}
    if logs is None:
        base["summary"] = {"reason": f"{sport} game-log master unavailable"}
        return _finish(base, sport, write, now)
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
    rows = payload.get("rows") or []
    return payload if len(rows) <= limit else {**payload, "rows": rows[:limit]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sport", choices=[*SPORTS, "mlb", "all"], default="all")
    ap.add_argument("--all", action="store_true", help="every sport (the default; spelled out for the scheduled job)")
    ap.add_argument("--write-gcs", action="store_true",
                    help="write EVSweep/{sport}_props/projections_latest.json (the default; spelled out for the job)")
    ap.add_argument("--no-write", action="store_true", help="print the summary, do not write to GCS/local storage")
    a = ap.parse_args(argv)
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
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
