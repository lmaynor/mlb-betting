"""Read path for the /edge shot chart: one player's season of shots against the league.

`shot_chart(player, ...)` never raises for a missing file or an unknown player: it returns {"status": ...} so the page can
say so. Artifacts are the ones `nba.shots.build` writes; they are cached in memory (TTL) because the season files are static.

Everything numeric in the payload is a count (attempts / makes) taken from real shots; percentages and differences are
computed by the page from those counts, so a sample size always travels with every rate.
"""
from __future__ import annotations

import io
import json
import logging
import threading
import time

import numpy as np
import pandas as pd

from mlb_core import storage as _st
from mlb_core.data.player_names import norm_name
from nba.shots.build import league_key, shots_key
from nba.shots.court import DIST_BINS, HEX_R, SHOT_GROUPS, ZONE_KEYS, dist_bin
from nba.teams import team_code

logger = logging.getLogger(__name__)

TTL_SEC = 1800
FAIL_TTL_SEC = 60
DEFAULT_MIN_FGA = 200      # the season charted when none is asked for: the newest with at least this many attempts (an explicit season has no floor)
RECENT_GAMES = 15
FIRST_YEAR = 2015          # how far back `seasons` offers

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
        ttl = TTL_SEC
    except Exception as exc:  # noqa: BLE001 -- absent artifact: "no shot data", retried soon
        logger.info("shot artifact %s unavailable: %s", key, exc)
        value, ttl = None, FAIL_TTL_SEC
    with _LOCK:
        _CACHE[key] = (now + ttl, value)
    return value


def _league(year: int):
    return _cached(f"league:{year}", lambda: json.loads(_st.read_bytes(league_key(year))))


def _shots(year: int):
    def load():
        df = pd.read_parquet(io.BytesIO(_st.read_bytes(shots_key(year))))
        df["game_id"] = df["game_id"].astype(str)
        return df
    return _cached(f"shots:{year}", load)


def available_seasons() -> list[int]:
    """Start years with a built league artifact, newest first (probes a bounded range; each miss is cached 60 s)."""
    this = pd.Timestamp.now().year
    return [y for y in range(this, FIRST_YEAR - 1, -1) if _league(y)]


def _pick_player(df: pd.DataFrame, name_norm: str):
    # by NAME only: the shot file's ids are NBA ids, the cockpit's are ESPN ids, and they do not join
    g = df[df["name_norm"] == name_norm]
    if g["player_id"].nunique() > 1:           # namesakes: the one who shot the most
        g = g[g["player_id"] == g["player_id"].value_counts().idxmax()]
    return g


def _hex_cells(g: pd.DataFrame, league: dict) -> list:
    """[q, r, fga, fgm, league fga, league fgm, pts, league pts, modal zone] for every cell the player shot from that the league did too."""
    lg = {(q, r): (a, m, z, p) for q, r, a, m, z, p in league["hexes"]}
    pts = g["made"].to_numpy() * np.where(g["three"].to_numpy(), 3, 2)
    out = []
    for (q, r), idx in g.groupby(["hex_q", "hex_r"]).indices.items():
        if (q, r) not in lg:
            continue
        la, lm, z, lp = lg[(q, r)]
        out.append([int(q), int(r), int(len(idx)), int(g["made"].to_numpy()[idx].sum()), la, lm, int(pts[idx].sum()), lp, z])
    return out


def _zone_rows(g: pd.DataFrame, league: dict) -> list:
    la = league.get("assist")
    rows = []
    for i, z in enumerate(league["zones"]):
        c = g[g["zone"] == i]
        row = {"key": z["key"], "label": z["label"], "cx": z["cx"], "cy": z["cy"],
               "fga": int(len(c)), "fgm": int(c["made"].sum()), "lfga": z["fga"], "lfgm": z["fgm"]}
        if la:
            row["ast"] = int((c["made"] & c["assisted"]).sum())          # his makes that a teammate set up
            row["last"] = la[i][1]                                        # league makes that were assisted ...
            row["lmade"] = la[i][0]                                       # ... out of this many league makes
        rows.append(row)
    return rows


def _group_rows(g: pd.DataFrame, league: dict) -> list:
    rows = []
    for k in SHOT_GROUPS:
        c = g[g["group"] == k]
        la, lm = league["groups"].get(k, [0, 0])
        if len(c) or la:
            rows.append({"group": k, "fga": int(len(c)), "fgm": int(c["made"].sum()), "lfga": la, "lfgm": lm})
    return rows


def _dist(g: pd.DataFrame, league: dict) -> dict:
    """FGA / FGM by feet from the hoop (1-ft bins) for the player and the league: the shooting-by-distance curve."""
    db = dist_bin(g["x"].to_numpy(), g["y"].to_numpy())
    made = g["made"].to_numpy()
    player = [[int((db == i).sum()), int(made[db == i].sum())] for i in range(DIST_BINS)]
    return {"player": player, "league": league.get("dist") or [[0, 0]] * DIST_BINS}


def _quarters(g: pd.DataFrame, league: dict) -> list:
    """Shooting by period (1-4, 5 = overtime): attempts, makes, threes, points per game played, and the league's FG% for context."""
    out = []
    games = max(int(g["game_id"].nunique()), 1)
    lq = league.get("quarters") or [[0, 0]] * 5
    for p in range(1, 6):
        c = g[g["period"] == p]
        pts = int((c["made"].to_numpy() * np.where(c["three"].to_numpy(), 3, 2)).sum())
        out.append({"period": p, "fga": int(len(c)), "fgm": int(c["made"].sum()), "fg3a": int(c["three"].sum()),
                    "fg3m": int((c["three"] & c["made"]).sum()), "pts": pts, "pts_pg": round(pts / games, 2),
                    "lfga": lq[p - 1][0], "lfgm": lq[p - 1][1]})
    return out


def _games(g: pd.DataFrame) -> list:
    """Per-game shooting for the last RECENT_GAMES games: the volume trend behind points / threes props."""
    out = []
    for (date, gid), c in g.groupby(["date", "game_id"]):
        out.append({"date": date, "opp": str(c["opp"].iloc[0]), "home": bool(c["home"].iloc[0]),
                    "fga": int(len(c)), "fgm": int(c["made"].sum()),
                    "fg3a": int(c["three"].sum()), "fg3m": int((c["three"] & c["made"]).sum())})
    out.sort(key=lambda r: r["date"])
    return out[-RECENT_GAMES:]


def _venue(g: pd.DataFrame) -> dict:
    res = {}
    for key, c in (("home", g[g["home"]]), ("away", g[~g["home"]])):
        res[key] = {"games": int(c["game_id"].nunique()), "fga": int(len(c)), "fgm": int(c["made"].sum()),
                    "fg3a": int(c["three"].sum()), "fg3m": int((c["three"] & c["made"]).sum())}
    return res


def _opponent(league: dict, opp: str | None):
    """The defending team's zone profile vs the league's, as counts."""
    opp = team_code(opp or "") or (opp or "").upper()       # ESPN spells GS / NY / NO / SA / UTAH / WSH; the artifacts use tricodes
    if not opp:
        return None
    d = league["defense"].get(opp)
    if not d:
        return None
    zones = []
    for i, z in enumerate(league["zones"]):
        a, m = d["zones"][i]
        zones.append({"key": z["key"], "fga": a, "fgm": m})
    return {"team": opp, "fga": d["fga"], "fg3a": d["fg3a"], "zones": zones,
            "lfga": league["fga"], "lfg3a": league["fg3a"], "n_teams": len(league["defense"])}


def _apply_filters(g: pd.DataFrame, last, venue, vs):
    """Game-level filters in order: the player's last N games, home/away, games against one opponent."""
    if vs:
        g = g[g["opp"] == (team_code(vs) or vs.upper())]
    if venue in ("home", "away"):
        g = g[g["home"] == (venue == "home")]
    if last:
        ids = g.sort_values(["date", "game_id"]).drop_duplicates("game_id")["game_id"].tail(int(last))
        g = g[g["game_id"].isin(set(ids))]
    return g


def _assists(df: pd.DataFrame, nn: str, game_ids: set, period) -> dict | None:
    """Baskets this player set up (ESPN play-by-play marks the passer): where they landed, who scored them. None when the season
    has no passer data. These are ASSISTED BASKETS, not potential assists: passes that did not become makes are tracking data."""
    a = df[(df["ast_norm"] == nn) & df["game_id"].isin(game_ids)]
    if period:
        a = a[a["period"] == int(period)]
    if not len(a):
        return {"n": 0, "games": len(game_ids), "shots": [], "recipients": [], "zones": []}
    rec = a.groupby("name").size().sort_values(ascending=False).head(6)
    zones = a[a["zone"] >= 0].groupby("zone").agg(n=("made", "size"), three=("three", "sum"))
    return {
        "n": int(len(a)), "games": len(game_ids), "threes": int(a["three"].sum()),
        "shots": [[int(round(x * 10)), int(round(y * 10)), 1 | (int(t) << 1)] for x, y, t in zip(a["x"], a["y"], a["three"])],
        "recipients": [{"name": str(k), "n": int(v)} for k, v in rec.items()],
        "zones": [{"key": ZONE_KEYS[int(i)], "n": int(r.n), "three": int(r.three)} for i, r in zones.iterrows()],
    }


def shot_chart(player: str | None, season: int | None = None, opp: str | None = None, last=None, venue=None, vs=None, period=None) -> dict:
    """-> {"status": "ok"|"no_data"|"no_player", ...}. `season` is a start year (2024 = 2024-25); default: the player's newest
    built season with >= DEFAULT_MIN_FGA attempts (early in a season that is last season, and the season picker offers the partial one).
    Filters (all optional): `last` = his last N games, `venue` = home|away, `vs` = games against one opponent tricode, `period` = 1-5."""
    seasons = available_seasons()
    if not seasons:
        return {"status": "no_data", "reason": "no shot artifacts built", "seasons": []}
    nn = norm_name(player or "")
    if not nn:
        return {"status": "no_player", "seasons": seasons}
    try_years = [season] if season in seasons else seasons
    for y in try_years:
        df = _shots(y)
        league = _league(y)
        if df is None or league is None:
            continue
        full = _pick_player(df, nn)
        if len(full) < DEFAULT_MIN_FGA and season is None:
            continue
        if not len(full):
            continue
        full = full.sort_values(["date", "game_id"])
        base = _apply_filters(full, last, venue, vs)
        g = base[base["period"] == int(period)] if period else base
        shots = [[int(round(x * 10)), int(round(yy * 10)), int(m) | (int(t) << 1) | (int(a) << 2)]
                 for x, yy, m, t, a in zip(g["x"], g["y"], g["made"], g["three"], g["assisted"])]
        has_assist = bool(league.get("has_assist"))
        return {
            "status": "ok", "player": str(full["name"].iloc[-1]), "team": str(full["team"].iloc[-1]),
            "season": y, "label": league["label"], "seasons": seasons, "source": league["source"],
            "last_game": league["last_game"], "hoop": league["hoop"], "hex_r": HEX_R,
            "filters": {"last": int(last) if last else None, "venue": venue if venue in ("home", "away") else None,
                        "vs": (team_code(vs) or vs.upper()) if vs else None, "period": int(period) if period else None,
                        "games": int(base["game_id"].nunique()), "games_season": int(full["game_id"].nunique())},
            "fga": int(len(g)), "fgm": int(g["made"].sum()), "fg3a": int(g["three"].sum()),
            "fg3m": int((g["three"] & g["made"]).sum()), "n_games": int(g["game_id"].nunique()),
            "has_assist": has_assist,
            "league": {"fga": league["fga"], "fgm": league["fgm"], "fg3a": league["fg3a"], "fg3m": league["fg3m"]},
            "shots": shots, "hexes": _hex_cells(g, league),
            "grid": [[q, r, z] for q, r, _a, _m, z, _p in league["hexes"]],
            "zones": _zone_rows(g, league), "groups": _group_rows(g, league), "dist": _dist(g, league),
            "quarters": _quarters(base, league), "games": _games(full), "venue": _venue(full),
            "opponent": _opponent(league, opp),
            "assists": _assists(df, nn, set(base["game_id"]), period) if has_assist else None,
        }
    return {"status": "no_player", "seasons": seasons}
