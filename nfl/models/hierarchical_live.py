"""Glue between the serving engine's data (ev.daily_projections: player game-log rows, schedule env, mirrored nflverse tables)
and the pure hierarchical pricers in nfl.models.hierarchical_props.

    price_row(...)  -> HierResult | None     one (player, stat) at one game; None = not enough data (caller falls back to the
                                             legacy engine, never guesses)

Everything optional degrades: no pbp / participation / injuries / snap_counts mirror, no air-yards columns, no schedule
lines -> the missing layer is neutral (league average), never an exception. `LiveContext.load()` is the only place that
reads the mirror; it never raises.

Share definitions here come from the game-log columns the board already loads (player_stats): target share = player
targets / team pass attempts; rush share = player carries / team carries. The pbp-derived receiver profile in
nfl.features.context_gaps (share of team TARGETS, with aDOT / wopr / racr) is a richer source used for aDOT when loaded.

ASCII only.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from nfl.features import context_gaps as cg
from nfl.models import hierarchical_props as hp
from nfl.props import env_adjust as ea

log = logging.getLogger("nfl.models.hierarchical_live")

ENGINE = "hierarchical_v1"
RATE_HALFLIFE = 6.0
VOL_HALFLIFE = 3.0
TD_HALFLIFE = 7.0
MIN_ROWS = 4
ACTIVE_WINDOW_WEEKS = 6
# default depth of target / yards-after-catch share when the game logs carry no air-yards columns
POS_ADOT = {"WR": 11.5, "TE": 7.5, "RB": 2.0, "FB": 1.5, "QB": 8.0}

PBP_COLS = ["game_id", "play_id", "season", "week", "season_type", "posteam", "defteam", "play_type", "qb_dropback",
            "qb_kneel", "qb_spike", "sack", "qb_hit", "air_yards", "yards_after_catch", "complete_pass", "yards_gained",
            "rushing_yards", "receiver_player_id", "passer_player_id", "cpoe", "xpass", "down", "wp",
            "half_seconds_remaining"]
PART_COLS = ["nflverse_game_id", "play_id", "defense_coverage_type", "defense_man_zone_type", "was_pressure"]


def enabled() -> bool:
    """The hierarchical engine serves the board only when NFL_ENGINE=hierarchical (default: the legacy EWM engine)."""
    return os.environ.get("NFL_ENGINE", "ewm").strip().lower() == "hierarchical"


# ----------------------------------------------------------------------------------------------------------- helpers
def _wmean(vals: np.ndarray, hl: float) -> float:
    n = len(vals)
    if n == 0:
        return float("nan")
    w = 0.5 ** ((n - 1 - np.arange(n)) / max(hl, 1e-6))
    ok = np.isfinite(vals)
    return float(np.sum(w[ok] * vals[ok]) / np.sum(w[ok])) if ok.any() else float("nan")


def _col(rows: list, col: str, last: int = 60) -> np.ndarray:
    out = []
    for r in rows[-last:]:
        try:
            v = float(r.get(col))
        except (TypeError, ValueError):
            v = float("nan")
        out.append(v)
    return np.array(out, dtype=float)


def _ewm(rows: list, col: str, hl: float) -> float:
    return _wmean(_col(rows, col), hl)


def _ratio(rows: list, num: str, den: str, hl: float) -> float:
    """sum(w num) / sum(w den) over rows where both are present."""
    n, d = _col(rows, num), _col(rows, den)
    ok = np.isfinite(n) & np.isfinite(d)
    if not ok.any():
        return float("nan")
    n, d = n[ok], d[ok]
    w = 0.5 ** ((len(n) - 1 - np.arange(len(n))) / max(hl, 1e-6))
    return float(np.sum(w * n) / np.sum(w * d)) if np.sum(w * d) > 0 else float("nan")


def _fin(x, default):
    return float(x) if x is not None and math.isfinite(x) else default


def _opt(x):
    return float(x) if x is not None and isinstance(x, (int, float, np.floating)) and math.isfinite(float(x)) else None


# ------------------------------------------------------------------------------------------------------ team context
def build_team_ctx(table: dict) -> dict:
    """Game-log index ({name: [row dicts]}) -> {"teams": {team: {"pass_att","carries","targets", "players": [...]}}}.

    Per team: trailing team pass attempts / carries / targets per game, and per active player the trailing target share
    (targets / team pass attempts), rush share (carries / team carries), air-yards share, aDOT, rush+rec TD rate."""
    recs = []
    for rows in table.values():
        for r in rows[-40:]:
            team = ea.canon(r.get("recent_team") or r.get("team"))
            try:
                season, week = int(r["season"]), int(r["week"])
            except (KeyError, TypeError, ValueError):
                continue
            if team is None:
                continue
            recs.append({"pid": r.get("player_id"), "name": r.get("player_display_name") or r.get("player_name"),
                         "team": team, "season": season, "week": week, "pos": str(r.get("position") or ""),
                         "att": _f(r.get("attempts")), "car": _f(r.get("carries")), "tgt": _f(r.get("targets")),
                         "td": _f(r.get("rushing_tds"), 0.0) + _f(r.get("receiving_tds"), 0.0),
                         "ay": _f(r.get("receiving_air_yards"))})
    if not recs:
        return {"teams": {}, "newest": 0}
    df = pd.DataFrame(recs)
    df["key"] = df["season"] * 100 + df["week"]
    tg = df.groupby(["team", "season", "week"], sort=False)[["att", "car", "tgt", "ay"]].sum(min_count=1).reset_index()
    tg["key"] = tg["season"] * 100 + tg["week"]
    out: dict = {}
    for team, g in tg.sort_values("key", kind="mergesort").groupby("team", sort=False):
        t = {"pass_att": _wmean(g["att"].to_numpy(float), VOL_HALFLIFE), "carries": _wmean(g["car"].to_numpy(float), VOL_HALFLIFE),
             "targets": _wmean(g["tgt"].to_numpy(float), VOL_HALFLIFE), "last_key": int(g["key"].iloc[-1]), "players": [],
             "recent_keys": [int(k) for k in g["key"].tail(MISS_WINDOW_GAMES)]}
        out[team] = t
    tot = tg.set_index(["team", "season", "week"])
    newest = int(df["key"].max())
    for (team, pid, name), g in df.sort_values("key", kind="mergesort").groupby(["team", "pid", "name"], sort=False, dropna=False):
        t = out.get(team)
        if t is None:
            continue
        all_keys = g["key"].to_numpy()
        g = g.tail(12)
        tot_rows = [tot.loc[(team, s, w)] for s, w in zip(g["season"], g["week"])]
        att = np.array([float(x["att"]) for x in tot_rows])
        car = np.array([float(x["car"]) for x in tot_rows])
        ay_t = np.array([float(x["ay"]) for x in tot_rows])
        with np.errstate(invalid="ignore", divide="ignore"):
            tshare = g["tgt"].to_numpy(float) / np.where(att > 0, att, np.nan)
            rshare = g["car"].to_numpy(float) / np.where(car > 0, car, np.nan)
            ashare = g["ay"].to_numpy(float) / np.where(ay_t > 0, ay_t, np.nan)
        tgt_sum, ay_sum = np.nansum(g["tgt"].to_numpy(float)), np.nansum(g["ay"].to_numpy(float))
        t["players"].append({
            "pid": pid, "name": name, "pos": g["pos"].iloc[-1], "last_key": int(g["key"].iloc[-1]),
            "miss": _missed_fraction(t["recent_keys"], set(int(k) for k in all_keys)),
            "tgt": _wmean(tshare, 4.0), "rush": _wmean(rshare, 4.0), "air": _wmean(ashare, 4.0),
            "adot": float(ay_sum / tgt_sum) if tgt_sum > 0 and ay_sum > 0 else None,
            "td": _wmean(g["td"].to_numpy(float), TD_HALFLIFE)})
    return {"teams": out, "newest": newest}


MISS_WINDOW_GAMES = 12


def _missed_fraction(team_keys: list, played_keys: set) -> float:
    """Recency-weighted share (same half-life as the volume EWMs) of the team's last games a player did NOT appear in.

    A receiver's trailing targets / carries already contain the games in which this teammate was absent, so only the
    NOT-yet-embedded part of his absence may be redistributed again: new weight = weight * (1 - missed_fraction)."""
    n = len(team_keys)
    if n == 0:
        return 0.0
    w = 0.5 ** ((n - 1 - np.arange(n)) / VOL_HALFLIFE)
    miss = np.array([0.0 if k in played_keys else 1.0 for k in team_keys])
    return float(np.sum(w * miss) / np.sum(w))


def _f(x, default=float("nan")) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


# ----------------------------------------------------------------------------------------------------- live context
@dataclass
class LiveContext:
    """Optional pbp / participation / injury / snap layers for ONE slate. All members may be empty (-> neutral)."""
    positions: dict = field(default_factory=dict)
    injuries: Optional[pd.DataFrame] = None
    absent_by_team: dict = field(default_factory=dict)       # {(team, season, week): [{"pid", "weight"}]}
    pbp: Optional[pd.DataFrame] = None
    participation: Optional[pd.DataFrame] = None
    starters: dict = field(default_factory=dict)
    _def_cache: dict = field(default_factory=dict)
    _off_cache: dict = field(default_factory=dict)
    _ol_cache: dict = field(default_factory=dict)

    @classmethod
    def load(cls, season: int, with_pbp: bool = True, seasons: Optional[list] = None) -> "LiveContext":
        """Read what the mirror has for `season` and the one before (or the explicit `seasons`, for a multi-season replay).
        NEVER raises: each layer is independent."""
        from nfl.data.read import load
        lc = cls()
        seasons = list(seasons) if seasons else [season - 1, season]
        try:
            pl = load("players", columns=["gsis_id", "pfr_id", "position"])
            lc.positions = {g: p for g, p in zip(pl["gsis_id"], pl["position"]) if isinstance(g, str)}
            players = pl
        except Exception as exc:                                                   # noqa: BLE001
            log.info("players crosswalk unavailable: %s", exc)
            players = None
        try:
            inj = load("injuries", seasons=seasons, strict=False)
            lc.injuries = inj
            lc.absent_by_team = _absences(inj)
        except Exception as exc:                                                   # noqa: BLE001
            log.info("injuries unavailable: %s", exc)
        if with_pbp:
            try:
                lc.pbp = load("pbp", seasons=seasons, columns=PBP_COLS, strict=False)
            except Exception as exc:                                               # noqa: BLE001
                log.info("pbp unavailable: %s", exc)
            try:
                lc.participation = load("participation", seasons=seasons, columns=PART_COLS, strict=False)
            except Exception as exc:                                               # noqa: BLE001
                log.info("participation unavailable: %s", exc)
            try:
                sc = load("snap_counts", seasons=seasons, columns=["season", "week", "team", "position", "pfr_player_id",
                                                                    "offense_pct"], strict=False)
                lc._snaps, lc._players = sc, players
            except Exception as exc:                                               # noqa: BLE001
                log.info("snap_counts unavailable: %s", exc)
        return lc

    def defense(self, season: int, week: int) -> Optional[pd.DataFrame]:
        if self.pbp is None or len(self.pbp) == 0:
            return None
        k = (season, week)
        if k not in self._def_cache:
            try:
                self._def_cache[k] = cg.defense_context_before(self.pbp, season, week, self.positions or None,
                                                               self.participation)
            except Exception as exc:                                               # noqa: BLE001
                log.warning("defense context failed: %s", exc)
                self._def_cache[k] = None
        return self._def_cache[k]

    def offense(self, season: int, week: int) -> Optional[pd.DataFrame]:
        if self.pbp is None or len(self.pbp) == 0:
            return None
        k = (season, week)
        if k not in self._off_cache:
            try:
                self._off_cache[k] = cg.offense_context_before(self.pbp, season, week)
            except Exception as exc:                                               # noqa: BLE001
                log.warning("offense context failed: %s", exc)
                self._off_cache[k] = None
        return self._off_cache[k]

    def ol_out(self, season: int, week: int) -> dict:
        k = (season, week)
        if k not in self._ol_cache:
            out: dict = {}
            sc, pl = getattr(self, "_snaps", None), getattr(self, "_players", None)
            if sc is not None and pl is not None and self.injuries is not None:
                try:
                    starters = cg.ol_starters_before(sc, pl, season, week)
                    out = cg.n_ol_out(self.injuries, starters, season, week)
                except Exception as exc:                                           # noqa: BLE001
                    log.warning("ol context failed: %s", exc)
            self._ol_cache[k] = out
        return self._ol_cache[k]

    def game_fields(self, team: str, opp: str, season: Optional[int], week: Optional[int]) -> dict:
        """GameContext kwargs for `team` (offence) vs `opp` (defence) in (season, week); {} when nothing is loaded."""
        out: dict = {}
        if season is None or week is None:
            return out
        d = self.defense(season, week)
        if d is not None and opp in d.index:
            row, lg = d.loc[opp], d.attrs.get("league", {})
            for src, dst in (("def_two_high_rate", "def_two_high_rate"), ("def_man_rate", "def_man_rate"),
                             ("def_middle_closed_rate", "def_single_high_rate")):
                v = _opt(row.get(src))
                if v is not None:
                    out[dst] = v
            lg_yac, lg_stuff = lg.get("def_yac_per_rec_allowed"), lg.get("def_stuff_rate")
            y, s = _opt(row.get("def_yac_per_rec_allowed")), _opt(row.get("def_stuff_rate"))
            if y is not None and lg_yac and math.isfinite(lg_yac) and lg_yac > 0:
                out["def_yac_factor"] = y / lg_yac
            if s is not None and lg_stuff and math.isfinite(lg_stuff) and lg_stuff > 0:
                out["def_stuff_factor"] = s / lg_stuff
        n_out = float(self.ol_out(season, week).get(team, 0))
        out["n_ol_out"] = n_out
        o = self.offense(season, week)
        if d is not None and o is not None and team in o.index and opp in d.index:
            m = cg.net_pressure_mismatch(_fin(o.loc[team].get("off_sack_hit_rate"), float("nan")), n_out,
                                         _fin(d.loc[opp].get("def_pass_rush_pressure_rate"), float("nan")),
                                         _fin(o.attrs.get("league", {}).get("off_sack_hit_rate"), float("nan")),
                                         _fin(d.attrs.get("league", {}).get("def_pass_rush_pressure_rate"), float("nan")))
            if math.isfinite(m):
                out["net_pressure_mismatch"] = m
        return out


def _absences(inj: pd.DataFrame) -> dict:
    """{(team, season, week): [{"pid": gsis_id, "weight": absence_weight}]} (weight > 0 only)."""
    out: dict = {}
    if inj is None or len(inj) == 0 or "report_status" not in inj.columns:
        return out
    prac = inj["practice_status"] if "practice_status" in inj.columns else [None] * len(inj)
    for gid, tm, s, w, st, pr in zip(inj["gsis_id"], inj["team"], inj["season"], inj["week"], inj["report_status"], prac):
        wt = cg.absence_weight(st, pr)
        if wt > 0 and isinstance(gid, str):
            out.setdefault((ea.canon(tm), int(s), int(w)), []).append({"pid": gid, "weight": wt})
    return out


# -------------------------------------------------------------------------------------------------------- the adapter
class AlphaLookup:
    """Callable stat -> that stat's league NegBin alpha; `.cv(stat)` -> the normal-family within-player cv.
    `ctx_for` is GameLogs.context (stat -> StatContext)."""

    def __init__(self, ctx_for):
        self._ctx_for = ctx_for

    def __call__(self, stat: str) -> Optional[float]:
        try:
            return float(self._ctx_for(stat).alpha)
        except Exception:                                                          # noqa: BLE001
            return None

    def cv(self, stat: str) -> Optional[float]:
        try:
            return float(self._ctx_for(stat).cv)
        except Exception:                                                          # noqa: BLE001
            return None


@dataclass
class HierResult:
    dist: hp.Dist
    vol_mult: float
    inputs: hp.PlayerInputs
    game: hp.GameContext
    notes: dict

    @property
    def mean(self) -> float:
        return self.dist.mean

    def p_over(self, line: float) -> float:
        return self.dist.sf(line)


def _volume_calibration(coef: Optional[dict], stat: str, implied_total, team_spread_fav_pos) -> float:
    """The shipped calibration as a multiplier on the primary VOLUME input. TD stats take the scale only: the implied total
    already enters them through the team-TD anchor, so the env term would count it twice."""
    if not coef:
        return 1.0
    if stat in ("anytime_td", "pass_tds") and "b0" in coef:
        return float(math.exp(coef["b0"]))
    return float(ea.multiplier(coef, implied_total=implied_total, team_spread=team_spread_fav_pos, stat=""))


def price_row(stat: str, rows: list, team: str, team_ctx: dict, env, ctx_alpha, coef: Optional[dict] = None,
              live: Optional[LiveContext] = None, mu_engine: Optional[float] = None, weather: Optional[dict] = None,
              pid: Optional[str] = None, opp: Optional[str] = None) -> Optional[HierResult]:
    """Price one player-stat. `env` = EnvTuple-like (implied_total, team_spread positive = favoured; optional wind / roof /
    temp / season / week) or None. `ctx_alpha(stat)` -> that stat's league NegBin alpha (or normal-family cv for
    pass_attempts via `ctx_alpha.cv`). `mu_engine` = the legacy engine's validated anytime_td mean (used as the TD prior)."""
    if stat not in hp.STATS or len(rows) < MIN_ROWS:
        return None
    last = rows[-1]
    pos = str(last.get("position") or "").upper()
    t = team_ctx.get("teams", {}).get(team) or {}
    it = _opt(getattr(env, "implied_total", None)) if env is not None else None
    sp_fav = _opt(getattr(env, "team_spread", None)) if env is not None else None
    wx = weather or {}
    dome = bool(wx.get("dome")) or (env is not None and str(getattr(env, "roof", "") or "").lower() in ("dome", "closed"))
    gkw = dict(team_spread=None if sp_fav is None else hp.spread_from_favoured_positive(sp_fav), implied_team_total=it,
               wind_mph=wx.get("wind_mph", _opt(getattr(env, "wind", None)) if env is not None else None), dome=dome,
               temp_f=wx.get("temp_f", _opt(getattr(env, "temp", None)) if env is not None else None))
    if live is not None and env is not None and opp:
        gkw.update(live.game_fields(team, opp, getattr(env, "season", None), getattr(env, "week", None)))
    game = hp.GameContext(**gkw)
    vol = _volume_calibration(coef, stat, it, sp_fav)
    pi = hp.PlayerInputs(pos=pos, vol_mult=vol)
    notes: dict = {}

    wk = (getattr(env, "season", None), getattr(env, "week", None)) if env is not None else (None, None)
    absent_ids = {}
    if live is not None and wk[0] is not None:
        for a in live.absent_by_team.get((team, int(wk[0]), int(wk[1])), []):
            absent_ids[a["pid"]] = a["weight"]

    # ---- passing
    att = _ewm(rows, "attempts", VOL_HALFLIFE)
    if stat in ("pass_attempts", "pass_completions", "pass_yds", "pass_tds", "interceptions"):
        if not math.isfinite(att) or att < 5.0:
            return None
        pi.pass_att_ewm = att
        pi.comp_rate = _fin(_ratio(rows, "completions", "attempts", RATE_HALFLIFE), 0.63)
        pi.ypc_ewm = _fin(_ratio(rows, "passing_yards", "completions", RATE_HALFLIFE), 11.0)
        pi.int_rate = _fin(_ratio(rows, "passing_interceptions", "attempts", 12.0), 0.025)
        pi.pass_td_ewm = _fin(_ewm(rows, "passing_tds", TD_HALFLIFE), 1.3)
        pi.qb_adot = _fin(_ratio(rows, "passing_air_yards", "attempts", RATE_HALFLIFE), 8.0)
        cp = _col(rows, "passing_cpoe")
        if np.isfinite(cp).sum() >= 4:
            pi.cpoe_dev = float(_wmean(cp, 3.0) - np.nanmean(cp))
        cvs = ctx_alpha.cv("pass_attempts") if hasattr(ctx_alpha, "cv") else None
        if cvs:
            between = float(np.var([hp.PRIORS["att_shift_trail"], 0.0, hp.PRIORS["att_shift_lead"]]))
            pi.alpha_att = hp.nb_alpha_from_var(att, (cvs * att) ** 2 * hp.PRIORS["att_var_mult"] - between)
        pi.alpha_pass_td = _fin(ctx_alpha("pass_tds"), pi.alpha_pass_td)
        pi.alpha_int = _fin(ctx_alpha("interceptions"), pi.alpha_int)

    # ---- rushing
    team_car = _fin(t.get("carries"), 26.0)
    car = _ewm(rows, "carries", VOL_HALFLIFE)
    me = _find_player(t, pid, last)
    if stat in ("rush_attempts", "rush_yds"):
        if not math.isfinite(car):
            return None
        pi.carries_ewm = car
        pi.team_carries = team_car
        pi.rush_share = _fin(me.get("rush") if me else None, car / team_car if team_car > 0 else 0.0)
        pi.ypc_rush_ewm = _fin(_ratio(rows, "rushing_yards", "carries", RATE_HALFLIFE), 4.3)
        pi.alpha_car = _fin(ctx_alpha("rush_attempts"), pi.alpha_car)

    # ---- receiving
    if stat in ("receptions", "rec_yds"):
        tgt = _ewm(rows, "targets", VOL_HALFLIFE)
        team_att = _fin(t.get("pass_att"), 35.0)
        if not math.isfinite(tgt) or team_att <= 0:
            return None
        pi.team_pass_att = team_att
        pi.target_share = tgt / team_att
        pi.catch_rate = _fin(_ratio(rows, "receptions", "targets", RATE_HALFLIFE), 0.65)
        pi.ypr_ewm = _fin(_ratio(rows, "receiving_yards", "receptions", RATE_HALFLIFE), 11.5)
        adot = _ratio(rows, "receiving_air_yards", "targets", RATE_HALFLIFE)
        pi.adot = adot if math.isfinite(adot) and adot > -5 else POS_ADOT.get(pos, 8.5)
        yac = _ratio(rows, "receiving_yards_after_catch", "receptions", RATE_HALFLIFE)
        pi.yac_per_rec_ewm = yac if math.isfinite(yac) and yac >= 0 else 0.40 * pi.ypr_ewm
        pi.alpha_rec = _fin(ctx_alpha("receptions"), pi.alpha_rec)
        notes["adot_source"] = "air_yards" if math.isfinite(adot) else "position_default"

    # ---- touchdowns
    if stat == "anytime_td":
        own = _fin(mu_engine, _fin(_ewm(rows, "rushing_tds", TD_HALFLIFE), 0.0) + _fin(_ewm(rows, "receiving_tds", TD_HALFLIFE), 0.0))
        pi.td_ewm = own
        mates = [p for p in t.get("players", []) if _is_active(p, t) and not _same(p, pid, last)]
        pi.team_td_prior_sum = own + sum(_fin(p.get("td"), 0.0) * (1.0 - absent_ids.get(p.get("pid"), 0.0)) for p in mates)

    # ---- teammate absences -> vacated opportunity redistributed by role
    if absent_ids and t:
        absent, active = [], []
        for p in t.get("players", []):
            if _same(p, pid, last):
                continue
            if p.get("pid") in absent_ids:
                absent.append({"weight": absent_ids[p["pid"]] * (1.0 - _fin(p.get("miss"), 0.0)), "pos": p["pos"], "adot": p.get("adot") if p.get("adot") is not None else POS_ADOT.get(str(p["pos"]).upper()),
                               "tgt": p.get("tgt"), "rush": p.get("rush"), "air": p.get("air"), "rz": None})
            elif _is_active(p, t):
                active.append(p)
        pools = cg.vacated_pools(absent)
        my_role = _role(pos, pi.adot if stat in ("receptions", "rec_yds") else None)
        me_tgt = _fin(me.get("tgt") if me else None, pi.target_share)
        add_t = cg.redistribute(pools["tgt"], [("me", my_role, me_tgt)] + [(p["pid"], _role(p["pos"], p.get("adot")),
                                                                             _fin(p.get("tgt"), 0.0)) for p in active])
        pi.vacated_target_share = add_t.get("me", 0.0)
        my_rush_role = "rb" if pos in ("RB", "FB") else ("qb" if pos == "QB" else "other")
        rpool = {("rb" if _role(a["pos"], a.get("adot")) == "rb" else ("qb" if a["pos"] == "QB" else "other")): 0.0
                 for a in absent}
        for a in absent:
            r = "rb" if _role(a["pos"], a.get("adot")) == "rb" else ("qb" if a["pos"] == "QB" else "other")
            if a.get("rush") and math.isfinite(a["rush"]):
                rpool[r] += a["weight"] * a["rush"]
        add_r = cg.redistribute(rpool, [("me", my_rush_role, pi.rush_share)] + [
            (p["pid"], "rb" if p["pos"] in ("RB", "FB") else ("qb" if p["pos"] == "QB" else "other"), _fin(p.get("rush"), 0.0))
            for p in active], spill=0.15)
        pi.vacated_rush_share = add_r.get("me", 0.0)
        # no red-zone split exists in the game logs: vacated touches (half targets, half carries) stand in for it
        pi.vacated_rz_share = 0.5 * pi.vacated_target_share + 0.5 * pi.vacated_rush_share
        notes["absent"] = len(absent)
    try:
        dist = hp.price(stat, pi, game)
    except Exception as exc:                                                        # noqa: BLE001 -- fall back, never break the board
        log.warning("hierarchical pricer failed for %s: %s", stat, exc)
        return None
    if dist.mean is None or not math.isfinite(dist.mean):
        return None
    return HierResult(dist, vol, pi, game, notes)


def _role(pos, adot) -> str:
    """Receiver role with ONE aDOT fallback (the position default) for everyone: an unknown aDOT must put the absent player and
    the player being priced in the same role, or the role-based redistribution silently never matches."""
    a = adot if adot is not None and isinstance(adot, (int, float, np.floating)) and math.isfinite(adot) else POS_ADOT.get(str(pos).upper())
    return cg.role_of(pos, a)


def _is_active(p: dict, t: dict) -> bool:
    newest = t.get("last_key", 0)
    gap = (newest // 100 - p["last_key"] // 100) * 18 + (newest % 100 - p["last_key"] % 100)
    return gap <= ACTIVE_WINDOW_WEEKS


def _same(p: dict, pid, last_row: dict) -> bool:
    if pid is not None and p.get("pid") == pid:
        return True
    lp = last_row.get("player_id")
    return lp is not None and p.get("pid") == lp


def _find_player(t: dict, pid, last_row: dict) -> Optional[dict]:
    for p in t.get("players", []):
        if _same(p, pid, last_row):
            return p
    return None
