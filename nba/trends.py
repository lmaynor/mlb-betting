"""NBA prop trends for the /edge inspector: one player's game log against a line, with the numbers behind the variance.

Pure read path over the box-score sources the cockpit already uses (`NBA/player_boxscores_master.*` from SportsBlaze, topped up
with the hoopR / ESPN player box for games after the master's newest date). Nothing is written. `trends(...)` never raises for a
missing source or an unknown player: it returns {"status": "no_data" | "no_player"}.

What it can and cannot say: the sources are BOX scores. Minutes, shot attempts, free throws, turnovers and the teammates' / the
opponent's totals are real; potential assists, touches, drives and passes are NBA tracking data and are NOT here (see `proxies`).
Every rate in the payload travels with its sample size `n`; nothing is estimated beyond arithmetic on the games shown.
"""
from __future__ import annotations

import io
import logging
import math
import threading
import time

import numpy as np
import pandas as pd

from mlb_core import storage as _st
from mlb_core.data.cockpit_form import NBA_HOOPR_KEY, NBA_KEYS, NBA_STATS, _HOOPR_SEASON_TYPE, _minutes, _num, _read
from mlb_core.data.player_names import norm_name
from nba.teams import team_code

logger = logging.getLogger(__name__)

TTL_SEC = 600
FAIL_TTL_SEC = 60
MAX_GAMES = 120
MIN_SEASON_GAMES = 20          # fewer games than this in the current season: splits / drivers also use the previous season
MIN_DRIVER_N = 15
TEAMMATE_WINDOW = 20           # team games used to rank a teammate's minutes
MIN_TEAMMATE_ABSENCES = 2

STAT_LABELS = {"points": "Points", "rebounds": "Rebounds", "assists": "Assists", "threes": "3-pointers made",
               "pra": "Pts + reb + ast", "pr": "Pts + reb", "pa": "Pts + ast", "ra": "Reb + ast", "steals": "Steals",
               "blocks": "Blocks", "turnovers": "Turnovers"}
# canonical game column(s) summed for each market
STAT_COLS = {"points": ("pts",), "rebounds": ("reb",), "assists": ("ast",), "threes": ("fg3m",),
             "pra": ("pts", "reb", "ast"), "pr": ("pts", "reb"), "pa": ("pts", "ast"), "ra": ("reb", "ast"),
             "steals": ("stl",), "blocks": ("blk",), "turnovers": ("tov",)}
assert set(STAT_COLS) == set(NBA_STATS)

DRIVER_LABELS = {"min": "Minutes", "fga": "Own field-goal attempts", "fg3a": "Own three-point attempts", "fta": "Own free-throw attempts",
                 "tov": "Own turnovers", "tm_fga": "Teammates' field-goal attempts", "tm_fgm": "Teammates' field goals made",
                 "tm_fg3m": "Teammates' threes made", "opp_fga": "Opponent field-goal attempts", "opp_miss": "Opponent missed shots"}
_PTS = ("min", "fga", "fg3a", "fta", "tm_fga")
_AST = ("min", "tov", "fga", "tm_fgm", "tm_fg3m")
_REB = ("min", "opp_miss", "fga")
_THR = ("fg3a", "min", "fga")
_MISC = ("min", "opp_fga", "fga")
DRIVERS = {"points": _PTS, "assists": _AST, "rebounds": _REB, "threes": _THR,
           "pra": _PTS + _AST + _REB, "pr": _PTS + _REB, "pa": _PTS + _AST, "ra": _REB + _AST,
           "steals": _MISC, "blocks": _MISC, "turnovers": _MISC}

PROXIES = {
    "available": ["minutes", "fga", "fg3a", "fta", "tov", "team makes", "opponent shots"],
    "missing": ["potential assists", "touches", "drives", "passes", "time of possession", "defender distance"],
    "note": "NBA tracking data (potential assists, touches, drives) is not in our sources; assisted-basket maps and teammate makes are the proxies",
}

_CACHE: dict = {}
_LOCK = threading.Lock()


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _cached(key: str, loader):
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        value = loader()
        ttl = TTL_SEC if value else FAIL_TTL_SEC
    except Exception as exc:  # noqa: BLE001 -- a missing / unreadable master degrades to "no data"
        logger.info("nba trends source %s unavailable: %s", key, exc)
        value, ttl = None, FAIL_TTL_SEC
    with _LOCK:
        _CACHE[key] = (now + ttl, value)
    return value


# -- sources -> one canonical per-player-game frame -----------------------------------------------------------------------
CANON = ["game_id", "season", "stype", "date", "team", "name", "pid", "min", "pts", "reb", "ast", "stl", "blk", "tov", "pf", "fgm", "fga",
         "fg3m", "fg3a", "fta", "starter", "home"]
_STYPE = {"regular season": "RS", "playoffs": "PO", "play-in tournament": "PI", "play-in": "PI", "in-season tournament": "IST"}
_MASTER_COLS = ["game_id", "season_year", "season_type", "date", "team_abbr", "is_home", "player_id", "name", "starter", "played", "points",
                "minutes", "field_goals_made", "field_goals_attempted", "three_points_made", "three_points_attempted",
                "free_throws_attempted", "rebounds", "assists", "steals", "blocks", "turnovers", "fouls"]


def _bool(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().isin(["true", "1", "1.0"])


def _from_master(df: pd.DataFrame) -> pd.DataFrame:
    from nba.props.data import sb_team
    df = df.copy()
    df = df[df["season_type"].astype(str).str.lower().isin(_STYPE)]            # drops Preseason
    df["min"] = df["minutes"].map(_minutes)
    out = pd.DataFrame({
        "game_id": df["game_id"].astype(str).values,
        "season": _num(df["season_year"]).astype("Int64").values,
        "stype": df["season_type"].astype(str).str.lower().map(_STYPE).values,
        "date": df["date"].astype(str).str[:10].values,
        "team": [sb_team(a) or team_code(str(a)) or a for a in df["team_abbr"]],
        "name": df["name"].astype(str).str.strip().values,
        "pid": ("s" + df["player_id"].astype(str)).values,
        "min": df["min"].values,
        "pts": _num(df["points"]).values, "reb": _num(df["rebounds"]).values, "ast": _num(df["assists"]).values,
        "stl": _num(df["steals"]).values, "blk": _num(df["blocks"]).values, "tov": _num(df["turnovers"]).values,
        "pf": _num(df["fouls"]).values, "fgm": _num(df["field_goals_made"]).values, "fga": _num(df["field_goals_attempted"]).values,
        "fg3m": _num(df["three_points_made"]).values, "fg3a": _num(df["three_points_attempted"]).values,
        "fta": _num(df["free_throws_attempted"]).values,
        "starter": _bool(df["starter"]).values if "starter" in df else False,
        "home": _bool(df["is_home"]).values if "is_home" in df else None,
    })
    return out


def _from_hoopr(e: pd.DataFrame) -> pd.DataFrame:
    e = e[e["season_type"].map(_HOOPR_SEASON_TYPE).notna() & e["athlete_id"].notna()].copy()
    e = e[e["season_type"] != 1]                                               # preseason
    e = e.drop_duplicates(["game_id", "athlete_id"])
    played = ~e["did_not_play"].fillna(False).astype(bool) & e["minutes"].notna()
    e["minutes"] = np.where(played, _num(e["minutes"]), 0.0)
    ha = e["home_away"].astype(str).str.lower() if "home_away" in e else pd.Series([""] * len(e), index=e.index)
    return pd.DataFrame({
        "game_id": "e" + e["game_id"].astype(str).values,
        "season": (_num(e["season"]) - 1).astype("Int64").values,
        "stype": e["season_type"].map(_HOOPR_SEASON_TYPE).str.lower().map(_STYPE).values,
        "date": e["game_date"].astype(str).str[:10].values,
        "team": [team_code(a) or a for a in e["team_abbreviation"]],
        "name": e["athlete_display_name"].astype(str).str.strip().values,
        "pid": ("e" + e["athlete_id"].astype("Int64").astype(str)).values,
        "min": e["minutes"].values,
        "pts": _num(e["points"]).fillna(0).values, "reb": _num(e["rebounds"]).fillna(0).values, "ast": _num(e["assists"]).fillna(0).values,
        "stl": _num(e["steals"]).fillna(0).values, "blk": _num(e["blocks"]).fillna(0).values, "tov": _num(e["turnovers"]).fillna(0).values,
        "pf": _num(e["fouls"]).fillna(0).values, "fgm": _num(e["field_goals_made"]).fillna(0).values,
        "fga": _num(e["field_goals_attempted"]).fillna(0).values,
        "fg3m": _num(e["three_point_field_goals_made"]).fillna(0).values, "fg3a": _num(e["three_point_field_goals_attempted"]).fillna(0).values,
        "fta": _num(e["free_throws_attempted"]).fillna(0).values,
        "starter": e["starter"].fillna(False).astype(bool).values if "starter" in e else False,
        "home": (ha == "home").values if len(ha) else None,
    })


_ET_DAY: dict = {}          # game_id -> ET game day; a tip never changes, so this survives cache refreshes


def to_et_days(base: pd.DataFrame, tips: pd.Series) -> pd.DataFrame:
    """The SportsBlaze master's `date` is the UTC date of the tip (76% differ from the ET game day, see
    docs/solutions/integration-issues/sportsblaze-master-date-is-utc.md): rest / back-to-back / "yesterday" need the ET day.
    `tips` = game_id -> UTC tip. Games without a known tip keep the master date (counted in the log)."""
    from nba.props.data import et_day
    new = {g: et_day(t).strftime("%Y-%m-%d") for g, t in tips.items() if g not in _ET_DAY}
    _ET_DAY.update(new)
    out = base.copy()
    mapped = out["game_id"].map(_ET_DAY)
    unknown = int(mapped.isna().sum())
    if unknown:
        logger.info("trends: %d player rows have no schedule tip yet (hoopR / raw-boxscore fallbacks follow)", unknown)
    out["date"] = mapped.fillna(out["date"]).values
    return out


def et_from_hoopr(base: pd.DataFrame, ref: pd.DataFrame) -> int:
    """Games the schedule dumps do not cover (the new season: SportsBlaze has no schedule file for it) get their ET day from the
    hoopR / ESPN box, which carries the ET `game_date`: same two teams, ET day within one day of the master's UTC date. Fills
    `_ET_DAY` for the unambiguous matches and returns how many games it mapped."""
    if ref is None or ref.empty:
        return 0
    pairs: dict = {}
    for gid, g in ref.groupby("game_id"):
        ts = frozenset(g["team"])
        if len(ts) == 2:
            pairs.setdefault(ts, set()).add(str(g["date"].iloc[0]))
    n = 0
    for gid, g in base[~base["game_id"].isin(_ET_DAY)].groupby("game_id"):
        ts = frozenset(g["team"])
        utc = pd.Timestamp(str(g["date"].iloc[0]))
        cand = [d for d in pairs.get(ts, ()) if 0 <= (utc - pd.Timestamp(d)).days <= 1]
        if len(cand) == 1:
            _ET_DAY[gid] = cand[0]
            n += 1
    return n


def _tips_for(base: pd.DataFrame, seasons: list) -> pd.Series:
    """Tip times for the master games in `base` from the schedule dumps (the raw per-day boxscore cache is the slow last resort,
    bounded to a few days: ~3 s a day against GCS)."""
    from nba.props.data import load_tips, tips_from_raw_boxscores
    need = base[~base["game_id"].isin(_ET_DAY)]
    if need.empty:
        return pd.Series(dtype="datetime64[ns, UTC]")
    return load_tips(seasons=seasons)


def _fallback_tips(base: pd.DataFrame, max_days: int = 6) -> pd.Series:
    from nba.props.data import tips_from_raw_boxscores
    miss = base[~base["game_id"].isin(_ET_DAY)]
    if miss.empty:
        return pd.Series(dtype="datetime64[ns, UTC]")
    return tips_from_raw_boxscores(sorted(set(miss["date"].astype(str))), max_days=max_days)


def _read_sources() -> pd.DataFrame | None:
    """Master + hoopR top-up (games dated AFTER the master's newest) in the canonical frame; None when no master is readable."""
    master = _read(NBA_KEYS, "nba players", usecols=_MASTER_COLS)
    base = _from_master(master)
    if base.empty:
        return None
    first = int(base["season"].max())
    base = base[base["season"] >= first - 1]                                    # only the two seasons the payload uses
    extra = []
    for season in (first + 1, first + 2):                                       # hoopR season = START year + 1
        try:
            extra.append(pd.read_parquet(io.BytesIO(_st.read_bytes(NBA_HOOPR_KEY.format(season=season)))))
        except Exception as exc:  # noqa: BLE001 -- the new season's file appears after its first games
            logger.info("trends: hoopR season %s unavailable: %s", season, exc)
    add = None
    if extra:
        e = pd.concat(extra, ignore_index=True)
        need = {"game_id", "athlete_id", "athlete_display_name", "team_abbreviation", "game_date", "season", "season_type",
                "did_not_play", "minutes", "points", "rebounds", "assists", "steals", "blocks", "turnovers", "fouls",
                "field_goals_made", "field_goals_attempted", "three_point_field_goals_made", "three_point_field_goals_attempted",
                "free_throws_attempted"}
        if need <= set(e.columns):
            add = _from_hoopr(e)
        else:
            logger.warning("trends: hoopR player box lacks %s", sorted(need - set(e.columns)))
    base = to_et_days(base, _tips_for(base, [first - 1, first]))
    if et_from_hoopr(base, add if add is not None else pd.DataFrame()):
        base = to_et_days(base, pd.Series(dtype="datetime64[ns, UTC]"))
    if (~base["game_id"].isin(_ET_DAY)).any():
        base = to_et_days(base, _fallback_tips(base))
    if add is not None:
        add = add[add["date"] > str(base["date"].max())]
        if len(add):
            base = pd.concat([base, add], ignore_index=True)
    return base


def prepare(raw: pd.DataFrame) -> dict | None:
    """Canonical frame -> {df, ...} with the opponent, team-schedule rest days and teammate / opponent aggregates per row.

    Keeps the newest season and the one before it. A row is a PLAYED game when minutes > 0; zero-minute rows stay (they mark an
    absence for the teammate-out table) but are never part of a player's log."""
    if raw is None or raw.empty:
        return None
    df = raw.copy()
    df = df[df["season"].notna() & df["date"].notna() & df["team"].notna()]
    cur = int(df["season"].max())
    df = df[df["season"] >= cur - 1].copy()
    df["season"] = df["season"].astype(int)
    df["min"] = pd.to_numeric(df["min"], errors="coerce").fillna(0.0)
    for c in ("pts", "reb", "ast", "stl", "blk", "tov", "pf", "fgm", "fga", "fg3m", "fg3a", "fta"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df = df.drop_duplicates(["game_id", "team", "name"], keep="first")
    played = df["min"] > 0
    # team totals of the players who played
    tot = df[played].groupby(["game_id", "team"])[["fgm", "fga", "fg3m", "fg3a", "fta", "tov", "ast", "reb", "pts"]].sum()
    tot.columns = [f"t_{c}" for c in tot.columns]
    df = df.join(tot, on=["game_id", "team"])
    for c in tot.columns:
        df[c] = df[c].fillna(0.0)
    # opponent = the other team of the game (None when the game does not have exactly two teams)
    teams = df.groupby("game_id")["team"].agg(lambda t: sorted(set(t)))
    opp = {}
    for gid, ts in teams.items():
        if len(ts) == 2:
            opp[(gid, ts[0])], opp[(gid, ts[1])] = ts[1], ts[0]
    df["opp"] = [opp.get((g, t)) for g, t in zip(df["game_id"], df["team"])]
    otot = tot.reset_index().rename(columns={"team": "opp"})
    df = df.merge(otot.rename(columns={c: f"o{c}" for c in tot.columns}), on=["game_id", "opp"], how="left")
    for c in tot.columns:
        df[f"o{c}"] = df[f"o{c}"].fillna(0.0)
    # teammates' totals exclude the player's own line
    played = df["min"].to_numpy() > 0
    df["tm_fgm"] = df["t_fgm"] - np.where(played, df["fgm"], 0.0)
    df["tm_fga"] = df["t_fga"] - np.where(played, df["fga"], 0.0)
    df["tm_fg3m"] = df["t_fg3m"] - np.where(played, df["fg3m"], 0.0)
    df["tm_ast"] = df["t_ast"] - np.where(played, df["ast"], 0.0)
    df["opp_fga"] = df["ot_fga"]
    df["opp_fgm"] = df["ot_fgm"]
    df["opp_reb"] = df["ot_reb"]
    df["opp_miss"] = df["ot_fga"] - df["ot_fgm"]
    # rest = days since the TEAM's previous game this season (a player returning from injury is not "rested"); first game: None
    tg = df.drop_duplicates(["game_id", "team"])[["game_id", "team", "season", "date"]].sort_values(["team", "season", "date"])
    prev = tg.groupby(["team", "season"])["date"].shift(1)
    gap = (pd.to_datetime(tg["date"]) - pd.to_datetime(prev)).dt.days
    rest = {(g, t): (None if pd.isna(x) else int(x)) for g, t, x in zip(tg["game_id"], tg["team"], gap)}
    df["rest"] = [rest.get((g, t)) for g, t in zip(df["game_id"], df["team"])]
    df["name_norm"] = df["name"].map(norm_name)
    df = df.sort_values(["date", "game_id"], kind="stable").reset_index(drop=True)
    return {"df": df, "current": cur}


def _load():
    return _cached("trends", lambda: prepare(_read_sources()))


# -- statistics ---------------------------------------------------------------------------------------------------------
def _r(x, nd=2):
    if x is None:
        return None
    try:
        if x != x or math.isinf(x):
            return None
    except TypeError:
        return None
    return round(float(x), nd)


def _desc(vals: list, line) -> dict:
    """Distribution summary of a list of per-game values against `line` (over = strictly greater)."""
    n = len(vals)
    base = {"n": n, "mean": None, "median": None, "sd": None, "cv": None, "over": None, "under": None, "push": None,
            "over_rate": None, "p10": None, "p25": None, "p75": None, "p90": None}
    if not n:
        return base
    a = np.asarray(vals, dtype=float)
    base["mean"] = _r(a.mean())
    base["median"] = _r(np.median(a))
    if n >= 2:
        sd = float(a.std(ddof=1))
        base["sd"] = _r(sd)
        base["cv"] = _r(sd / a.mean()) if a.mean() > 0 else None
    if n >= 5:
        base["p10"], base["p25"], base["p75"], base["p90"] = (_r(np.percentile(a, q)) for q in (10, 25, 75, 90))
    if line is not None:
        over, under = int((a > line).sum()), int((a < line).sum())
        base.update(over=over, under=under, push=n - over - under, over_rate=_r(over / n, 4))
    return base


def _hist(vals: list):
    if not vals:
        return None
    a = np.asarray(vals, dtype=float)
    lo_v, hi_v = float(a.min()), float(a.max())
    width = max(1, math.ceil((hi_v - lo_v) / 14)) if hi_v > lo_v else 1
    lo = math.floor(lo_v / width) * width
    nb = int(math.floor((hi_v - lo) / width)) + 1
    counts = [0] * nb
    for v in a:
        counts[min(int((v - lo) // width), nb - 1)] += 1
    return {"lo": lo, "hi": lo + nb * width, "width": width, "counts": counts}


def _rolling(vals: list, k: int) -> list:
    out = []
    for i in range(len(vals)):
        out.append(_r(float(np.mean(vals[i - k + 1:i + 1]))) if i + 1 >= k else None)
    return out


def _corr(x, y):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if len(x) < 3 or x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def _split(label: str, key: str, g: pd.DataFrame, line) -> dict:
    v = g["val"].tolist()
    d = _desc(v, line)
    return {"key": key, "label": label, "n": d["n"], "mean": d["mean"], "over_rate": d["over_rate"],
            "min_mean": _r(g["min"].mean()) if len(g) else None}


def _pick_player(df: pd.DataFrame, nn: str):
    """Played rows of the player named `nn`. Two source ids that play on the SAME date are two people (namesakes): the one with the
    most minutes wins. Two ids that never share a date are one person seen through two sources (SportsBlaze uuid / ESPN id)."""
    g = df[(df["name_norm"] == nn) & (df["min"] > 0)]
    if g.empty:
        return None
    if "pid" in g and g["pid"].nunique() > 1:
        ids = list(g.groupby("pid")["min"].sum().sort_values(ascending=False).index)
        dates = {i: set(g[g["pid"] == i]["date"]) for i in ids}
        keep = []
        for i in ids:                                   # heaviest first: a later id that overlaps any kept one is a namesake
            if all(not (dates[i] & dates[k]) for k in keep):
                keep.append(i)
        g = g[g["pid"].isin(keep)]
    return g


def _teammate_out(df: pd.DataFrame, pg: pd.DataFrame, team: str, seasons: list, line) -> list:
    """Own line with and without each rotation teammate, over the player's games with this team in `seasons`.

    Candidates = the 6 teammates with the most minutes over the team's last TEAMMATE_WINDOW games of the NEWEST season. An absence
    counts only between his first and last game for the team that season (before / after he was not on the roster), and only when
    he has no row or 0 minutes in a game the player played."""
    latest = max(seasons)
    tdf0 = df[(df["team"] == team) & (df["season"] == latest)]
    if tdf0.empty or pg[(pg["team"] == team) & (pg["season"] == latest)].empty:
        return []
    order = tdf0.drop_duplicates("game_id").sort_values(["date", "game_id"])["game_id"].tolist()
    me = pg["name"].iloc[-1]
    recent = tdf0[tdf0["game_id"].isin(set(order[-TEAMMATE_WINDOW:])) & (tdf0["min"] > 0) & (tdf0["name"] != me)]
    cand = recent.groupby("name")["min"].mean().sort_values(ascending=False).head(6)
    acc = {n: ([], []) for n in cand.index}
    for season in seasons:
        mine = pg[(pg["team"] == team) & (pg["season"] == season)]
        tdf = df[(df["team"] == team) & (df["season"] == season)]
        if mine.empty or tdf.empty:
            continue
        dates = tdf.drop_duplicates("game_id").set_index("game_id")["date"]
        played_by = tdf[tdf["min"] > 0].groupby("name")["game_id"].agg(set)
        for name in cand.index:
            had = played_by.get(name, set())
            if not had:
                continue
            d_had = sorted(dates[g] for g in had)
            win = mine[(mine["date"] >= d_had[0]) & (mine["date"] <= d_had[-1])]
            acc[name][0].append(win[win["game_id"].isin(had)])
            acc[name][1].append(win[~win["game_id"].isin(had)])
    out = []
    for name, (ws, wos) in acc.items():
        if not ws:
            continue
        w, wo = pd.concat(ws), pd.concat(wos)
        if len(wo) < MIN_TEAMMATE_ABSENCES:
            continue
        dw, dwo = _desc(w["val"].tolist(), line), _desc(wo["val"].tolist(), line)
        out.append({"player": name, "min_avg": _r(cand[name], 1), "n_with": len(w), "n_without": len(wo),
                    "mean_with": dw["mean"], "mean_without": dwo["mean"], "over_with": dw["over_rate"], "over_without": dwo["over_rate"],
                    "min_with": _r(w["min"].mean(), 1) if len(w) else None, "min_without": _r(wo["min"].mean(), 1),
                    "fga_with": _r(w["fga"].mean(), 1) if len(w) else None, "fga_without": _r(wo["fga"].mean(), 1)})
    out.sort(key=lambda r: -r["n_without"])
    return out


def _drivers(scope: pd.DataFrame, stat: str) -> list:
    if len(scope) < MIN_DRIVER_N:
        return []
    keys = list(dict.fromkeys(DRIVERS[stat]))
    last5 = scope.tail(5)
    out = []
    for k in keys:
        col = scope[k]
        c = _corr(col, scope["val"])
        if c is None:
            continue
        sd = float(col.std(ddof=1))
        z = (float(last5[k].mean()) - float(col.mean())) / (sd / math.sqrt(5)) if sd > 0 else None
        out.append({"key": k, "label": DRIVER_LABELS[k], "corr": _r(c, 3), "n": len(scope), "recent_mean": _r(last5[k].mean()),
                    "season_mean": _r(col.mean()), "z": _r(z)})
    out.sort(key=lambda r: -abs(r["corr"]))
    return out


def trends(player: str | None, stat: str, line=None, opp: str | None = None) -> dict:
    """-> {"status": "ok" | "no_player" | "no_data", ...}; see the module docstring and the payload keys below."""
    if stat not in STAT_COLS:
        raise ValueError(f"unsupported stat {stat!r}")
    data = _load()
    if not data:
        return {"status": "no_data", "reason": "no NBA box-score source readable"}
    nn = norm_name(player or "")
    if not nn:
        return {"status": "no_player"}
    df = data["df"]
    pg = _pick_player(df, nn)
    if pg is None or pg.empty:
        return {"status": "no_player"}
    try:
        line = None if line is None or line == "" else float(line)
    except (TypeError, ValueError):
        line = None
    pg = pg.copy()
    pg["val"] = pg[list(STAT_COLS[stat])].sum(axis=1)
    pg = pg.sort_values(["date", "game_id"], kind="stable")
    # props are regular-season markets: playoff / play-in games (higher minutes and usage) are left out unless he is in them now
    post = pg["stype"].isin(["PO", "PI"])
    in_post = bool(post.iloc[-1])
    skipped = 0 if in_post else int(post.sum())
    if skipped and not (~post).all():
        pg = pg[~post]
    else:
        skipped = 0
    team = str(pg["team"].iloc[-1])
    season = int(pg["season"].max())
    seasons = sorted(int(s) for s in pg["season"].unique())
    cur = pg[pg["season"] == season]
    prev = pg[pg["season"] == season - 1]
    scope_two = len(cur) < MIN_SEASON_GAMES
    scope = pg[pg["season"] >= season - 1] if scope_two else cur
    opp_code = team_code(opp or "") or ((opp or "").upper() or None)

    full_vals = pg["val"].tolist()
    r5, r10 = _rolling(full_vals, 5), _rolling(full_vals, 10)
    tail = pg.tail(MAX_GAMES)
    cut = len(pg) - len(tail)
    games = []
    for (_, r), a5, a10 in zip(tail.iterrows(), r5[cut:], r10[cut:]):
        games.append({"date": r["date"], "opp": r["opp"], "home": None if pd.isna(r["home"]) else bool(r["home"]),
                      "rest": None if pd.isna(r["rest"]) else int(r["rest"]), "stype": r["stype"], "season": int(r["season"]),
                      "starter": bool(r["starter"]), "min": _r(r["min"], 1), "val": _r(r["val"], 1), "pts": _r(r["pts"], 0),
                      "reb": _r(r["reb"], 0), "ast": _r(r["ast"], 0), "fg3m": _r(r["fg3m"], 0), "fg3a": _r(r["fg3a"], 0),
                      "fga": _r(r["fga"], 0), "fta": _r(r["fta"], 0), "tov": _r(r["tov"], 0), "pf": _r(r["pf"], 0),
                      "stl": _r(r["stl"], 0), "blk": _r(r["blk"], 0), "tm_fgm": _r(r["tm_fgm"], 0), "tm_fga": _r(r["tm_fga"], 0),
                      "tm_fg3m": _r(r["tm_fg3m"], 0), "tm_ast": _r(r["tm_ast"], 0), "opp_fga": _r(r["opp_fga"], 0),
                      "opp_fgm": _r(r["opp_fgm"], 0), "opp_reb": _r(r["opp_reb"], 0), "r5": a5, "r10": a10})

    rest = scope["rest"]
    sp = [
        ("Home", "home", scope[scope["home"] == True]),            # noqa: E712 -- nullable bool column
        ("Away", "away", scope[scope["home"] == False]),           # noqa: E712
        ("Back-to-back", "rest0", scope[rest == 1]),
        ("1 day off", "rest1", scope[rest == 2]),
        ("2+ days off", "rest2p", scope[rest >= 3]),
        ("Starter", "starter", scope[scope["starter"]]),
        ("Bench", "bench", scope[~scope["starter"]]),
    ]
    splits = [_split(label, key, g, line) for label, key, g in sp]
    if opp_code:
        splits.append(_split(f"vs {opp_code}", "vs_opp", pg[pg["opp"] == opp_code], line))

    mn = scope["min"]
    cmin = _corr(mn, scope["val"])
    slope = float(np.polyfit(mn, scope["val"], 1)[0]) if len(scope) >= 3 and mn.std() > 0 else None
    minutes = {"n": len(scope), "mean": _r(mn.mean()), "sd": _r(mn.std(ddof=1)) if len(scope) >= 2 else None,
               "l5_mean": _r(pg.tail(5)["min"].mean()), "corr_val_min": _r(cmin, 3), "slope_per_min": _r(slope, 3),
               "per36": _r(scope["val"].mean() / mn.mean() * 36) if mn.mean() > 0 else None}
    d_scope = _desc(scope["val"].tolist(), line)
    share = None if cmin is None else _r(cmin * cmin, 3)
    note = None
    if share is not None:
        note = (f"Minutes explain {round(share * 100)}% of the game-to-game swing in {STAT_LABELS[stat].lower()}; the rest is shot volume "
                f"and efficiency, which is why the driver table matters.")
    variance = {"n": d_scope["n"], "sd": d_scope["sd"], "cv": d_scope["cv"],
                "min_cv": _r(mn.std(ddof=1) / mn.mean()) if len(scope) >= 2 and mn.mean() > 0 else None,
                "share_from_minutes": share, "note": note}

    return {
        "status": "ok", "player": str(pg["name"].iloc[-1]), "team": team, "stat": stat, "label": STAT_LABELS[stat], "line": line,
        "season": season, "seasons": seasons, "as_of": str(pg["date"].iloc[-1]), "n_loaded": len(pg),
        "scope": "two_seasons" if scope_two else "season", "postseason_games_excluded": skipped, "games": games,
        "summary": {"season": _desc(cur["val"].tolist(), line), "l10": _desc(pg.tail(10)["val"].tolist(), line),
                    "l5": _desc(pg.tail(5)["val"].tolist(), line), "prev": _desc(prev["val"].tolist(), line)},
        "hist": _hist(cur["val"].tolist()), "rolling": {"l5": r5[cut:], "l10": r10[cut:]},
        "splits": splits, "minutes": minutes, "teammate_out": _teammate_out(df, scope, team, sorted(int(x) for x in scope["season"].unique()), line),
        "drivers": _drivers(scope, stat), "variance": variance, "proxies": PROXIES,
    }
