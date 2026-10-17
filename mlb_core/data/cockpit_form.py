"""Recent player form (last-N game log vs a prop line) for the /edge multi-sport cockpit.

Pure read path: pulls the per-sport player game-log masters that already live in the data lake, reduces each to a
small per-player index, and caches it in memory for 10 minutes so the cockpit endpoint stays fast. NOTHING here
writes anywhere. Every failure degrades to `None` (the caller renders "no game log"), never an exception.

Sources (real keys; the spec's aliases are tried last):
    nfl  NFL/nflverse/player_stats/player_stats_{season}.parquet   (weekly lines, no calendar date -> "W5" labels)
    nhl  NHL/skater_boxscores_master.csv, NHL/goalie_boxscores_master.csv (+ team box master for the opponent)
    nba  NBA/player_boxscores_master.parquet|csv

`recent_form()` returns the cockpit shape the frontend charts:
    {line, stat, n_games, games: [{date, opp, val, usage, hit}], l5_rate, l10_rate, szn_rate, szn_avg, szn_games,
     usage_label, avg_val, avg_usage}
`hit` means the player CLEARED the line (val > line), regardless of which side the flagged quote is on; a push on an
integer line is therefore not a hit. Rates are 0-1 fractions over the games actually available (None when empty).
"""
from __future__ import annotations

import io
import logging
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone

import pandas as pd

from mlb_core import storage as _st

logger = logging.getLogger(__name__)

TTL_SEC = 600            # 10 min: the cockpit endpoint is polled, the masters change at most nightly
FAIL_TTL_SEC = 60        # a missing/unreadable master is retried sooner than a good one is refreshed
N_GAMES = 10             # games shown in the chart
KEEP_SEASONS = 2         # current + previous season: an early-season L10 still has ten games

_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm_name(name) -> str:
    """'Ja'Marr Chase Jr.' -> 'jamarr chase' (same folding as ev.props.norm_name, kept local so this module has no
    dependency on the +EV sweep)."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`]", "", s)
    s = re.sub(r"[-_]", " ", s)
    return " ".join(t for t in re.sub(r"[^a-z0-9 ]", " ", s).split() if t not in _SUFFIX_TOKENS)


# stat -> (value column(s) summed, usage column(s) summed, usage label)
NFL_STATS = {
    "pass_yds":   (("passing_yards",), ("attempts",), "Pass attempts"),
    "rush_yds":   (("rushing_yards",), ("carries",), "Carries"),
    "rec_yds":    (("receiving_yards",), ("targets",), "Targets"),
    "receptions": (("receptions",), ("targets",), "Targets"),
    "pass_tds":   (("passing_tds",), ("attempts",), "Pass attempts"),
    # volume + turnover markets (2026-10-16). nflverse names: completions, attempts, carries, passing_interceptions.
    # No dropback / snap-share column exists in player_stats, so a volume stat's own count is its usage proxy.
    "pass_completions": (("completions",), ("attempts",), "Pass attempts"),
    "pass_attempts":    (("attempts",), ("attempts",), "Pass attempts"),
    "rush_attempts":    (("carries",), ("carries",), "Carries"),
    "interceptions":    (("passing_interceptions",), ("attempts",), "Pass attempts"),
    # rush + receiving TDs only (return / defensive TDs are not in these columns); the books' own scope is UNVERIFIED
    "anytime_td": (("rushing_tds", "receiving_tds"), ("carries", "targets"), "Touches"),
}
NHL_SKATER_STATS = {
    "sog":           (("sog",), ("toi_min",), "TOI (min)"),
    "goals":         (("goals",), ("toi_min",), "TOI (min)"),
    "points":        (("points",), ("toi_min",), "TOI (min)"),
    "assists":       (("assists",), ("toi_min",), "TOI (min)"),
    "hits":          (("hits",), ("toi_min",), "TOI (min)"),
    "blocked_shots": (("blocked_shots",), ("toi_min",), "TOI (min)"),
    "pim":           (("pim",), ("toi_min",), "TOI (min)"),
}
NHL_GOALIE_STATS = {"saves": (("saves",), ("shots_against",), "Shots faced")}
NBA_STATS = {
    "points":    (("points",), ("minutes",), "Minutes"),
    "rebounds":  (("rebounds",), ("minutes",), "Minutes"),
    "assists":   (("assists",), ("minutes",), "Minutes"),
    "threes":    (("three_points_made",), ("minutes",), "Minutes"),
    "pra":       (("points", "rebounds", "assists"), ("minutes",), "Minutes"),
    "pr":        (("points", "rebounds"), ("minutes",), "Minutes"),
    "pa":        (("points", "assists"), ("minutes",), "Minutes"),
    "ra":        (("rebounds", "assists"), ("minutes",), "Minutes"),
    "steals":    (("steals",), ("minutes",), "Minutes"),
    "blocks":    (("blocks",), ("minutes",), "Minutes"),
    "turnovers": (("turnovers",), ("minutes",), "Minutes"),
}

NFL_KEY = "NFL/nflverse/player_stats/player_stats_{season}.parquet"
NHL_SKATER_KEYS = ("NHL/skater_boxscores_master.csv", "NHL/skaters_master.csv")
NHL_GOALIE_KEYS = ("NHL/goalie_boxscores_master.csv", "NHL/goalies_master.csv")
NHL_TEAM_KEY = "NHL/team_boxscores_master.csv"
NBA_KEYS = ("NBA/player_boxscores_master.parquet", "NBA/player_boxscores_master.csv", "NBA/player_box_master.parquet")

_CACHE: dict = {}
_LOCK = threading.Lock()


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _cached(key: str, loader):
    """TTL cache of a loader's result. A failed load caches `None` for FAIL_TTL_SEC so a missing file does not turn
    every request into a GCS round trip."""
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        value = loader()
        ttl = TTL_SEC if value else FAIL_TTL_SEC
    except Exception as exc:  # noqa: BLE001 -- file absent / unreadable: degrade to "no game log"
        logger.info("cockpit game log %s unavailable: %s", key, exc)
        value, ttl = None, FAIL_TTL_SEC
    with _LOCK:
        _CACHE[key] = (now + ttl, value)
    return value


def _read(keys, kind: str, usecols=None) -> pd.DataFrame:
    last: Exception | None = None
    for key in keys:
        try:
            raw = _st.read_bytes(key)
        except Exception as exc:  # noqa: BLE001
            last = exc
            continue
        buf = io.BytesIO(raw)
        if key.endswith(".parquet"):
            df = pd.read_parquet(buf)
        else:
            df = pd.read_csv(buf)
        if usecols is not None:
            df = df[[c for c in usecols if c in df.columns]]
        return df
    raise FileNotFoundError(f"{kind}: none of {list(keys)} readable ({last})")


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _minutes(v) -> float | None:
    """NBA minutes arrive as a float or 'MM:SS'."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, str) and ":" in v:
        try:
            m, s = v.split(":")[:2]
            return int(m) + int(s) / 60.0
        except ValueError:
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _index(df: pd.DataFrame, name_col: str, id_col: str | None) -> dict:
    """Frame (already sorted chronologically) -> {norm name | 'id:<id>': [row dict, ...]}. Keeping per-player lists of
    plain dicts (not DataFrames) keeps the cache small and the per-request work trivial."""
    out: dict = {}
    names = df[name_col].map(norm_name).tolist()
    ids = df[id_col].tolist() if id_col and id_col in df.columns else [None] * len(df)
    for nm, pid, row in zip(names, ids, df.to_dict("records")):
        if nm:
            out.setdefault(nm, []).append(row)
        if pid is not None and pid == pid:
            try:
                out.setdefault(f"id:{int(pid)}", []).append(row)
            except (TypeError, ValueError):
                pass
    return out


def _season_year_now() -> int:
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 3 else now.year - 1


# -- loaders --------------------------------------------------------------------------------------------------------
def _load_nfl() -> dict | None:
    cols = ["player_id", "player_display_name", "player_name", "team", "recent_team", "season", "week", "season_type",
            "opponent_team", "passing_yards", "passing_tds", "attempts", "rushing_yards", "carries",
            "receiving_yards", "receptions", "targets", "rushing_tds", "receiving_tds", "position",
            "completions", "passing_interceptions"]
    frames = []
    y = _season_year_now()
    for season in (y + 1, y, y - 1):       # newest first; stop after KEEP_SEASONS found
        try:
            frames.append(_read([NFL_KEY.format(season=season)], "nfl", cols))
        except FileNotFoundError:
            continue
        if len(frames) >= KEEP_SEASONS:
            break
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    name_col = "player_display_name" if "player_display_name" in df.columns else "player_name"
    for c in ("season", "week", "passing_yards", "passing_tds", "attempts", "rushing_yards", "carries",
              "receiving_yards", "receptions", "targets", "rushing_tds", "receiving_tds", "completions",
              "passing_interceptions"):
        if c in df.columns:
            df[c] = _num(df[c])
    df = df.dropna(subset=["season", "week"]).sort_values(["season", "week"], kind="stable")
    df["_date"] = "W" + df["week"].astype(int).astype(str)
    df["_opp"] = df["opponent_team"] if "opponent_team" in df.columns else None
    df["_sort"] = df["season"] * 100 + df["week"]
    return {"all": _index(df, name_col, None)}


def _load_nhl() -> dict | None:
    sk = _read(NHL_SKATER_KEYS, "nhl skaters")
    sk["toi_min"] = _num(sk.get("toi_sec")) / 60.0
    try:
        gl = _read(NHL_GOALIE_KEYS, "nhl goalies")
    except FileNotFoundError:
        gl = None
    opp = {}
    try:
        tb = _read([NHL_TEAM_KEY], "nhl teams", ["game_id", "team_id", "opp_abbr"])
        opp = {(int(g), int(t)): o for g, t, o in zip(tb["game_id"], tb["team_id"], tb["opp_abbr"]) if g == g and t == t}
    except Exception:  # noqa: BLE001 -- opponent is decoration only
        pass

    def prep(df: pd.DataFrame) -> pd.DataFrame:
        if "played" in df.columns:
            df = df[df["played"].astype(str).str.lower().isin(["true", "1", "1.0"])]
        if "game_type" in df.columns:
            df = df[_num(df["game_type"]).isin([2, 3])]
        if "season_year" in df.columns:
            keep = sorted(_num(df["season_year"]).dropna().unique())[-KEEP_SEASONS:]
            df = df[_num(df["season_year"]).isin(keep)]
        df = df.copy()
        df["_date"] = df["date"].astype(str).str[:10]
        df["_sort"] = df["_date"]
        df["_opp"] = [opp.get((int(g), int(t))) if g == g and t == t else None
                      for g, t in zip(df.get("game_id", []), df.get("team_id", []))]
        return df.sort_values("_date", kind="stable")

    out = {"skater": _index(prep(sk), "name", "player_id")}
    out["goalie"] = _index(prep(gl), "name", "player_id") if gl is not None else {}
    return out


def _load_nba() -> dict | None:
    df = _read(NBA_KEYS, "nba players")
    if "played" in df.columns:
        df = df[df["played"].astype(str).str.lower().isin(["true", "1", "1.0"])]
    if "season_type" in df.columns:
        df = df[~df["season_type"].astype(str).str.lower().eq("preseason")]
    if "season_year" in df.columns:
        keep = sorted(_num(df["season_year"]).dropna().unique())[-KEEP_SEASONS:]
        df = df[_num(df["season_year"]).isin(keep)]
    df = df.copy()
    df["minutes"] = df["minutes"].map(_minutes)
    df["_date"] = df["date"].astype(str).str[:10]
    df["_sort"] = df["_date"]
    df["_opp"] = df["opp_abbr"] if "opp_abbr" in df.columns else None
    df = df.sort_values("_date", kind="stable")
    return {"all": _index(df, "name", None)}


_LOADERS = {"nfl": _load_nfl, "nhl": _load_nhl, "nba": _load_nba}


def supported_stats(sport: str) -> set:
    if sport == "nfl":
        return set(NFL_STATS)
    if sport == "nhl":
        return set(NHL_SKATER_STATS) | set(NHL_GOALIE_STATS)
    if sport == "nba":
        return set(NBA_STATS)
    return set()


# -- shaping --------------------------------------------------------------------------------------------------------
def _sum_cols(row: dict, cols) -> float | None:
    if not cols:
        return None
    vals = []
    for c in cols:
        v = row.get(c)
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if f != f:
            return None
        vals.append(f)
    return sum(vals)


def _clean(x: float | None):
    if x is None:
        return None
    return int(x) if float(x).is_integer() else round(float(x), 1)


def _rate(flags: list) -> float | None:
    return round(sum(flags) / len(flags), 3) if flags else None


def shape_form(rows: list, stat_def: tuple, line, n: int = N_GAMES) -> dict | None:
    """Per-player chronological game rows -> the cockpit `recentForm` dict (None when no usable game)."""
    val_cols, use_cols, use_label = stat_def
    games = []
    for r in rows:
        v = _sum_cols(r, val_cols)
        if v is None:
            continue
        games.append({"row": r, "val": v, "usage": _sum_cols(r, use_cols)})
    if not games:
        return None
    last = games[-n:]
    try:
        ln = None if line is None else float(line)
    except (TypeError, ValueError):
        ln = None
    hit = (lambda v: bool(v > ln)) if ln is not None else (lambda v: None)
    chart = [{"date": g["row"].get("_date"), "opp": g["row"].get("_opp") if g["row"].get("_opp") == g["row"].get("_opp") else None,
              "val": _clean(g["val"]), "usage": _clean(g["usage"]), "hit": hit(g["val"])} for g in last]
    # season = the season of the player's most recent game: season sort keys are 'YYYYMMDD' strings (NHL/NBA) or ints (NFL)
    def season_of(r):
        s = r.get("season") if r.get("season") is not None else r.get("season_year")
        return s if s == s else None
    cur = season_of(games[-1]["row"])
    szn = [g for g in games if season_of(g["row"]) == cur] if cur is not None else games
    flags = lambda gs: [bool(g["val"] > ln) for g in gs] if ln is not None else []   # noqa: E731
    usages = [g["usage"] for g in last if g["usage"] is not None]
    return {
        "line": ln, "n_games": len(last), "games": chart,
        "l5_rate": _rate(flags(last[-5:])), "l10_rate": _rate(flags(last)), "szn_rate": _rate(flags(szn)),
        "szn_avg": round(sum(g["val"] for g in szn) / len(szn), 2), "szn_games": len(szn),
        "usage_label": use_label,
        "avg_val": round(sum(g["val"] for g in last) / len(last), 2),
        "avg_usage": round(sum(usages) / len(usages), 2) if usages else None,
    }


def form_from_pairs(pairs: list, line, usage_label: str | None = None, n: int = N_GAMES) -> dict | None:
    """Already-computed (date, value) pairs (e.g. the MLB Statcast enrichment's last-12 form) -> the same cockpit shape.
    No opponent / usage / season split is known, so those come back None and the season rate equals the shown window."""
    rows = [{"_date": d, "_opp": None, "v": v} for d, v in pairs if v is not None]
    return shape_form(rows, (("v",), (), usage_label), line, n)


def recent_form(sport: str, stat: str, line, player: str | None, player_id=None, n: int = N_GAMES) -> dict | None:
    """Last-`n` games of `stat` for one player against `line`, or None when the sport/stat is unsupported, the game-log
    master is absent, or the player has no games in it."""
    sport = (sport or "").lower()
    loader = _LOADERS.get(sport)
    if loader is None or stat not in supported_stats(sport):
        return None
    idx = _cached(f"form:{sport}", loader)
    if not idx:
        return None
    if sport == "nfl":
        defs, table = NFL_STATS, idx["all"]
    elif sport == "nba":
        defs, table = NBA_STATS, idx["all"]
    else:
        goalie = stat in NHL_GOALIE_STATS
        defs, table = (NHL_GOALIE_STATS, idx["goalie"]) if goalie else (NHL_SKATER_STATS, idx["skater"])
    rows = None
    if player_id is not None and sport == "nhl":
        try:
            rows = table.get(f"id:{int(player_id)}")
        except (TypeError, ValueError):
            rows = None
    if not rows:
        rows = table.get(norm_name(player))
    if not rows:
        return None
    return shape_form(rows, defs[stat], line, n)
