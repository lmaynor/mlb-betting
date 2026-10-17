"""Historical SOG quote loader for the validation study (the 63-day `nhl_data.db` from the sog-predictor bundle).

Everything read from that DB is DATA ONLY (opened read-only, `mode=ro`); none of the bundle's code or pickles is used.

Verified encodings (2026-10-14, see the validation report):
  * `price` is an American price, `over_under` is 'Over'/'Under', `line` is a half-point SOG line.
  * the table PK is (event_id, bookmaker, player_name, over_under): ONE line per player per book (alt ladders overwritten).
  * `game_date` is the SNAPSHOT date, NOT the game date -- the game date is the ET date of `commence_time`.
  * 'Utah Mammoth' occasionally appears where a team abbreviation belongs.
  * one snapshot per quote, ~184 min pre-start: NO closing line exists, so no CLV.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from mlb_core.data.player_names import PlayerResolver, norm_name
from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob, devig_two_way

TEAM_FIX = {"Utah Mammoth": "UTA", "Utah Hockey Club": "UTA", "ARI": "UTA"}      # legacy map; see canon_team for the dated one
UTAH_FIRST_GAME = "2024-07-01"        # the game log spells the Coyotes ARI through 2024-04 and the Utah club UTA from 2024-09


def canon_team(code, game_date=None):
    """Any feed spelling -> the NHL-API tricode the game log uses (nhl.config.TEAMS). BettingPros emits Kalshi-style
    LA/NJ/SJ/TB (verified on 2023-25 partitions: 4 of 32 teams, the bulk of the `no_game` drops) where the log has
    LAK/NJD/SJS/TBL. ARI/UTA are one franchise under two codes: dated, so a pre-2024-07 ARI game stays ARI and a later
    one is UTA (the old blanket ARI->UTA mislabelled 411 real Coyotes games). Unknown codes pass through unchanged."""
    from nhl import config as NC
    c = str(code or "").strip()
    if not c:
        return c
    if c in TEAM_FIX and c != "ARI":
        return TEAM_FIX[c]
    if c.upper() in ("ARI", "UTA"):
        late = str(game_date or "9999")[:10] >= UTAH_FIRST_GAME
        return "UTA" if late else "ARI"
    return NC.team_abbrev(c) or c


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def load_quotes(db_path: str) -> pd.DataFrame:
    """Raw SOG quote rows with the game's ET date and minutes-to-start attached."""
    con = _ro(db_path)
    try:
        q = pd.read_sql("select * from nhl_player_props where market = 'player_shots_on_goal'", con)
    finally:
        con.close()
    for c in ("home_abbrev", "away_abbrev"):
        q[c] = q[c].replace(TEAM_FIX)
    start = pd.to_datetime(q["commence_time"], format="ISO8601", utc=True)
    snap = pd.to_datetime(q["snapshot_time"], format="ISO8601", utc=True)
    q["game_date"] = start.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")   # overrides the snapshot-date column
    q["mins_to_start"] = (start - snap).dt.total_seconds() / 60.0
    return q


def pair_quotes(q: pd.DataFrame, min_mins_to_start: float = 0.0) -> pd.DataFrame:
    """One row per (event, book, player, line) with BOTH sides priced; drops post-start snapshots and one-sided rows.
    Adds per-book de-vigged fair P(over) (proportional) and the book's overround."""
    q = q[q["mins_to_start"] >= min_mins_to_start]
    key = ["event_id", "game_date", "home_abbrev", "away_abbrev", "commence_time", "bookmaker", "player_name", "line"]
    o = q[q["over_under"] == "Over"].rename(columns={"price": "over"})[key + ["over", "mins_to_start"]]
    u = q[q["over_under"] == "Under"].rename(columns={"price": "under"})[key + ["under"]]
    p = o.merge(u, on=key, how="inner")
    po = p["over"].map(american_to_implied_prob)
    pu = p["under"].map(american_to_implied_prob)
    p["overround"] = po + pu
    fo = [devig_two_way(a, b, "proportional")[0] for a, b in zip(po, pu)]
    p["fair_over"] = np.asarray(fo, dtype=float)
    p["player_norm"] = p["player_name"].map(norm_name)
    return p.reset_index(drop=True)


def name_table(db_path: str, extra: pd.DataFrame | None = None) -> pd.DataFrame:
    """player_id / full_name / team rows used to resolve quote names. DB source: MoneyPuck-derived `mp_player_game`
    (full names + ids + team per game). `extra` (e.g. NHL/players_master.csv) is appended."""
    con = _ro(db_path)
    try:
        t = pd.read_sql("select distinct player_id, player_name as full_name, team from mp_player_game "
                        "where situation = 'all'", con)
    finally:
        con.close()
    if extra is not None and len(extra):
        t = pd.concat([t, extra[["player_id", "full_name", "team"]]], ignore_index=True)
    return t.drop_duplicates()


def resolve_players(pairs: pd.DataFrame, names: pd.DataFrame) -> pd.DataFrame:
    """Add `player_id` (Int64, NA if unresolved/ambiguous) -- resolved WITHIN the event's two teams. Adds `resolve_how`."""
    r = PlayerResolver(names.to_dict("records"))
    cache: dict = {}
    ids, how = [], []
    for nm, h, a in zip(pairs["player_name"], pairs["home_abbrev"], pairs["away_abbrev"]):
        k = (nm, h, a)
        if k not in cache:
            cache[k] = r.resolve(nm, None, within=[h, a])
        pid, w = cache[k]
        ids.append(pid)
        how.append(w)
    out = pairs.copy()
    out["player_id"] = pd.array(ids, dtype="Int64")
    out["resolve_how"] = how
    return out


def attach_outcomes(pairs: pd.DataFrame, games: pd.DataFrame, gamelog: pd.DataFrame) -> pd.DataFrame:
    """Join resolved quotes to realized shots. `games`: columns game_id, date, home_team, away_team (final games only).
    `gamelog`: canonical game log (appearances with TOI > 0). Adds `game_id`, `sog_actual`, `status`:
        'ok'      player appeared (TOI > 0): graded
        'void'    resolved but did not appear in the game: stake refunded by books, EXCLUDED here (not a loss)
        'no_game' no final game found for the event (e.g. data ends before it)
        'unresolved' name not resolved / ambiguous
    """
    g = games.rename(columns={"date": "game_date", "home_team": "home_abbrev", "away_team": "away_abbrev"})
    g = g[["game_id", "game_date", "home_abbrev", "away_abbrev"]].assign(game_date=lambda x: x["game_date"].astype(str).str[:10])
    pairs = pairs.assign(
        home_abbrev=[canon_team(t, d) for t, d in zip(pairs["home_abbrev"], pairs["game_date"])],
        away_abbrev=[canon_team(t, d) for t, d in zip(pairs["away_abbrev"], pairs["game_date"])])
    out = pairs.merge(g, on=["game_date", "home_abbrev", "away_abbrev"], how="left")
    # +-1 day fallback (UTC-vs-ET stamping): the SAME ordered (home, away) pair a day either side. A team pair meets at most
    # once in any 3-day window, so a unique hit is the game; ambiguous or absent stays no_game.
    miss = out["game_id"].isna()
    if miss.any():
        shifted = []
        for off in (-1, 1):
            t = out.loc[miss, ["home_abbrev", "away_abbrev", "game_date"]].copy()
            t["game_date"] = (pd.to_datetime(t["game_date"]) + pd.Timedelta(days=off)).dt.strftime("%Y-%m-%d")
            t = t.reset_index().merge(g, on=["game_date", "home_abbrev", "away_abbrev"], how="inner")
            shifted.append(t[["index", "game_id", "game_date"]])
        alt = pd.concat(shifted, ignore_index=True)
        alt = alt[~alt["index"].duplicated(keep=False)]                  # two candidate days -> ambiguous, leave unmatched
        out.loc[alt["index"].to_numpy(), "game_id"] = alt["game_id"].to_numpy()
        out.loc[alt["index"].to_numpy(), "game_date"] = alt["game_date"].to_numpy()
    gl = gamelog[["game_id", "player_id", "sog"]].rename(columns={"sog": "sog_actual"})
    gl = gl.assign(player_id=gl["player_id"].astype("Int64"))
    out = out.merge(gl, on=["game_id", "player_id"], how="left")
    status = np.where(out["game_id"].isna(), "no_game",
             np.where(out["player_id"].isna(), "unresolved",
             np.where(out["sog_actual"].isna(), "void", "ok")))
    out["status"] = status
    out["won_over"] = np.where(out["status"] == "ok", (out["sog_actual"] > out["line"]).astype(float), np.nan)
    out["push"] = (out["status"] == "ok") & (out["sog_actual"] == out["line"])
    return out
