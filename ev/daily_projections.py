"""
ev.daily_projections -- one daily prop-projection board for NFL / NHL / NBA.

For every prop the books offer on the upcoming slate (or, when no lines have posted yet, the top rotation players on
today's scheduled teams) it produces ONE row joining:

  * a projection built from the sport's player game-log master    proj_mean, p_over_model, l5/l10 hit rate, szn_avg
  * the market, from the latest banked/live book quotes            consensus_line, best over/under price + book,
                                                                   p_over_market (median de-vigged P(over))
  * the two engines' agreement                                     model_edge, market_ev, confluence, beezy_score

    PYTHONPATH=. python -m ev.daily_projections --sport nfl|nhl|nba|all [--local] [--no-write]

Served by mlb/runners/public_api.py::get_sport_projections() at GET /api/public/projections?sport=nfl|nhl|nba, from
EVSweep/{sport}_props/projections_latest.json (or computed on demand when that file is missing/stale).

WHAT THE ENGINE IS (and is not). The trained research models (nfl/models/props.py, nhl/props/train.py,
nba/props/train.py) keep their artifacts LOCAL (never in GCS, never in the Cloud Run image), so the live board cannot
load them. It runs an empirical estimator instead -- engine "empirical_ewm_v1":

    mu    = 0.5 * EWM(value) + 0.5 * EWM(value)/EWM(usage) * projected usage          (usage = targets / carries /
            attempts / TOI / minutes), then x an opponent-allowed factor (ratio of what that opponent's opposition
            produced vs each producer's own baseline, shrunk toward 1 and clipped to [0.85, 1.15])
    dist  = NegBin (counts) / Normal (yards, points, saves) / Poisson at-least-one (anytime TD); the spread parameter is
            fit from the league's own within-player game-to-game variance in the same master.

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

from ev.props import DFS_BOOKS, build_consensus, parse_event_props
from mlb_core.data import cockpit_form as cf
from mlb_core.odds.utils import american_to_decimal

log = logging.getLogger("ev.daily_projections")
_ET = ZoneInfo("America/New_York")

ENGINE = "empirical_ewm_v1"
SPORTS = ("nfl", "nhl", "nba")
OUT_KEY = "EVSweep/{sport}_props/projections_latest.json"

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
MAX_AGE_MIN = 240.0                                  # banked lines older than this are not shown as live lines
STALE_GAMELOG_DAYS = 150
ROTATION_PER_TEAM = 6
MAX_ROWS = 1200

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


# ------------------------------------------------------------------------------------------------------ game logs
@dataclass
class StatContext:
    """League-level facts for one stat, derived once from the master (not per player)."""
    opp_factor: dict = field(default_factory=dict)      # canonical opponent code -> multiplier
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

    def __init__(self, sport: str, idx: dict):
        self.sport = sport
        self.idx = idx
        self._ctx: dict = {}
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
        for oc, e in exp.items():
            if e > 0:
                w = cnt[oc] / (cnt[oc] + OPP_SHRINK_K)
                ctx.opp_factor[oc] = min(max(1.0 + (got[oc] / e - 1.0) * w, OPP_CLIP[0]), OPP_CLIP[1])
        if n_players and mean_sq_sum > 0:
            # method of moments over the whole population: var = mu + alpha*mu^2 (NegBin), var = (cv*mu)^2 (Normal)
            ctx.alpha = min(max((var_sum - mean_sum) / mean_sq_sum, ALPHA_RANGE[0]), ALPHA_RANGE[1])
            ctx.cv = min(max(math.sqrt(var_sum / mean_sq_sum), 0.1), 2.0)
            ctx.league_mean = mean_sum / n_players
        ctx.n_players = n_players
        return ctx


# ------------------------------------------------------------------------------------------- projection + P(over)
def project_mu(hist: list, halflife: float, opp_factor: float = 1.0) -> dict | None:
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
        mu = EWM_WEIGHT * ewm_v + (1.0 - EWM_WEIGHT) * rate * usage_proj
    return {"mu": max(mu * opp_factor, 0.0), "n_games": n, "usage_proj": usage_proj, "opp_factor": opp_factor}


def p_over(family: str, mu: float, line: float, ctx: StatContext) -> float | None:
    """P(X > line) under the stat's family. `line` is the book's number (x.5 for nearly everything)."""
    if mu is None or not math.isfinite(mu) or line is None:
        return None
    if family == "anytime":
        return float(1.0 - math.exp(-max(mu, 0.0))) if line < 1.0 else float(1.0 - _pois_cdf(math.floor(line), mu))
    if mu <= 0:
        return 0.0 if line >= 0 else 1.0
    if family == "negbin":
        n = 1.0 / ctx.alpha
        return float(1.0 - nbinom.cdf(math.floor(line), n, n / (n + mu)))
    sd = max(ctx.cv * mu, MIN_SD_FRAC * mu, 1e-6)
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
    """Best over / under American price (highest decimal) and its book among real books at the point."""
    best = {"over": None, "under": None}
    for q in _real(qs_at_point):
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


def build_line_rows(sport: str, events: list, logs: GameLogs, now: datetime) -> tuple[list, dict]:
    """Pure core: banked book events + game logs -> projection rows (one per player x stat at its main line)."""
    stats_cfg = PROJECT_STATS[sport]
    out, st = [], {"events": 0, "events_started": 0, "players_quoted": 0, "no_log": 0, "thin_history": 0,
                   "ambiguous_team": 0, "rows": 0}
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
            team = logs.team_of(rows)
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
    proj = project_mu(_hist(rows, sdef), HALFLIFE[sport], ctx.opp_factor.get(opp, 1.0) if opp else 1.0)
    if proj is None:
        return None
    mu = proj["mu"]
    p_model_over = p_over(family, mu, line, ctx) if line is not None else None
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
        "side": side, "proj_mean": _f(mu, 3), "p_over_model": _f(p_model_over),
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
    days = sorted({(now + timedelta(hours=h)).astimezone(_ET).strftime("%Y%m%d") for h in (0, 12, 24, 36)})
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


def rotation_rows(sport: str, logs: GameLogs, games: list, now: datetime, skip: set) -> tuple[list, dict]:
    """Projection-only rows (no line yet) for the highest-usage players of teams that play soon. `skip` holds
    (player_norm, stat) pairs that already have a line row."""
    out, st = [], {"rotation_games": len(games), "rotation_rows": 0}
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
            if str(name).startswith("id:") or len(rows) < MIN_GAMES:
                continue
            team = logs.team_of(rows)
            if team not in team_game or (name, stat) in skip:
                continue
            h = _hist(rows, sdef)
            usage = [g[1] for g in h[-10:] if g[1] is not None]
            if len(h) < MIN_GAMES or not usage or sum(usage) / len(usage) < threshold:
                continue
            per_team.setdefault(team, []).append((sum(usage) / len(usage), name, rows))
        for team, cands in per_team.items():
            g, opp = team_game[team]
            gk = f"{sport}:{g['start'].astimezone(_ET).date().isoformat()}:{g['away']}@{g['home']}"
            mins = (g["start"] - now).total_seconds() / 60.0
            for _, name, rows in sorted(cands, key=lambda c: -c[0])[:ROTATION_PER_TEAM]:
                row = make_row(sport, stat, _display_name(rows, name), name, rows, sdef, logs, None, None, [], team, opp,
                               gk, g["start"], mins, has_line=False)
                if row is not None:
                    out.append(row)
    out.sort(key=lambda r: (r["game_key"], r["stat"], -(r["proj_mean"] or 0)))
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
def load_events(sport: str, now: datetime, max_age_min: float = MAX_AGE_MIN):
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


def _status(has_lines: bool, has_rotation: bool, logs_ok: bool) -> str:
    if not logs_ok:
        return "unavailable"
    if has_lines:
        return "live"
    return "awaiting_lines" if has_rotation else "idle"


def run_sport(sport: str, now: datetime | None = None, logs: GameLogs | None = None, events=None, meta=None,
              games=None, write: bool = True) -> dict:
    """Compute one sport's board. `logs` / `events` / `games` are injectable (tests, local runs). -> the payload dict
    that is also written to EVSweep/{sport}_props/projections_latest.json."""
    now = now or datetime.now(timezone.utc)
    if sport not in PROJECT_STATS:
        raise ValueError(f"unknown sport {sport!r}; expected one of {SPORTS}")
    logs = logs or GameLogs.load(sport)
    base = {"sport": sport, "engine": ENGINE, "validated": False, "ts": now.isoformat(), "rows": [], "count": 0,
            "stats": list(PROJECT_STATS[sport]), "status": "unavailable", "lines_age_minutes": None,
            "gamelog_latest": None, "stale_gamelog": None, "summary": {}}
    if logs is None:
        base["summary"] = {"reason": f"{sport} game-log master unavailable"}
        return _finish(base, sport, write)
    base["gamelog_latest"] = logs.latest_date()
    gl = base["gamelog_latest"]
    if sport != "nfl" and gl:
        try:
            base["stale_gamelog"] = (now.date() - datetime.fromisoformat(gl).date()).days > STALE_GAMELOG_DAYS
        except ValueError:
            pass
    if events is None:
        try:
            events, meta = load_events(sport, now)
        except Exception as exc:                                                       # noqa: BLE001
            log.warning("%s lines unavailable: %s", sport, exc)
            events, meta = None, {"reason": str(exc)[:200]}
    meta = meta or {}
    line_rows, lstats = ([], {})
    if events:
        line_rows, lstats = build_line_rows(sport, events, logs, now)
    covered = {(r["player_norm"], r["stat"]) for r in line_rows}
    rot, rstats = [], {}
    try:
        g = games if games is not None else schedule_games(sport, now)
        rot, rstats = rotation_rows(sport, logs, g, now, covered)
    except Exception as exc:                                                           # noqa: BLE001
        log.warning("%s rotation fallback failed: %s", sport, exc)
    rows = (line_rows + rot)[:MAX_ROWS]
    base.update(rows=rows, count=len(rows), lines_age_minutes=meta.get("age_min"),
                status=_status(bool(line_rows), bool(rot), True),
                summary={**{k: v for k, v in meta.items() if k != "age_min"}, **lstats, **rstats,
                         "line_rows": len(line_rows), "confluence": sum(1 for r in line_rows if r["confluence"])})
    return _finish(base, sport, write)


def _finish(payload: dict, sport: str, write: bool) -> dict:
    if write and payload["status"] != "unavailable":
        try:
            from mlb_core import storage
            storage.write_bytes(json.dumps(payload, default=str).encode(), OUT_KEY.format(sport=sport))
        except Exception as exc:                                                       # noqa: BLE001
            log.warning("projections write failed for %s: %s", sport, exc)
    log.info("PROJECTIONS %s", json.dumps({k: payload[k] for k in ("sport", "status", "count", "summary")}, default=str))
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sport", choices=[*SPORTS, "all"], default="all")
    ap.add_argument("--no-write", action="store_true", help="print the summary, do not write to GCS/local storage")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for sp in (SPORTS if a.sport == "all" else (a.sport,)):
        p = run_sport(sp, write=not a.no_write)
        print(f"{sp}: status={p['status']} rows={p['count']} lines_age={p['lines_age_minutes']} "
              f"gamelog_latest={p['gamelog_latest']} summary={json.dumps(p['summary'], default=str)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
