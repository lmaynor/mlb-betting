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

import pandas as pd

from mlb_core import storage as _st
from mlb_core.data.player_names import norm_name
from nba.shots.build import league_key, shots_key
from nba.shots.court import HEX_R, SHOT_GROUPS
from nba.teams import team_code

logger = logging.getLogger(__name__)

TTL_SEC = 1800
FAIL_TTL_SEC = 60
MIN_FGA = 50               # a season thinner than this is not charted as "the" season for a player
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


def _rate(a: int, m: int):
    return round(m / a, 4) if a else None


def _hex_cells(g: pd.DataFrame, league: dict) -> list:
    lg = {(q, r): (a, m) for q, r, a, m, _ in league["hexes"]}
    out = []
    for (q, r), c in g.groupby(["hex_q", "hex_r"]):
        if (q, r) not in lg:
            continue
        la, lm = lg[(q, r)]
        out.append([int(q), int(r), int(len(c)), int(c["made"].sum()), la, lm])
    return out


def _zone_rows(g: pd.DataFrame, league: dict) -> list:
    rows = []
    for i, z in enumerate(league["zones"]):
        c = g[g["zone"] == i]
        rows.append({"key": z["key"], "label": z["label"], "cx": z["cx"], "cy": z["cy"],
                     "fga": int(len(c)), "fgm": int(c["made"].sum()), "lfga": z["fga"], "lfgm": z["fgm"]})
    return rows


def _group_rows(g: pd.DataFrame, league: dict) -> list:
    rows = []
    for k in SHOT_GROUPS:
        c = g[g["group"] == k]
        la, lm = league["groups"].get(k, [0, 0])
        if len(c) or la:
            rows.append({"group": k, "fga": int(len(c)), "fgm": int(c["made"].sum()), "lfga": la, "lfgm": lm})
    return rows


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


def shot_chart(player: str | None, season: int | None = None, opp: str | None = None) -> dict:
    """-> {"status": "ok"|"no_data"|"no_player", ...}. `season` is a start year (2024 = 2024-25); default: the player's newest
    built season with >= MIN_FGA attempts."""
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
        g = _pick_player(df, nn)
        if len(g) < MIN_FGA and season is None:
            continue
        if not len(g):
            continue
        g = g.sort_values(["date", "game_id"])
        shots = [[int(round(x * 10)), int(round(yy * 10)), int(m) | (int(t) << 1)]
                 for x, yy, m, t in zip(g["x"], g["y"], g["made"], g["three"])]
        return {
            "status": "ok", "player": str(g["name"].iloc[-1]), "team": str(g["team"].iloc[-1]),
            "season": y, "label": league["label"], "seasons": seasons, "source": league["source"],
            "last_game": league["last_game"], "hoop": league["hoop"], "hex_r": HEX_R,
            "fga": int(len(g)), "fgm": int(g["made"].sum()), "fg3a": int(g["three"].sum()),
            "fg3m": int((g["three"] & g["made"]).sum()), "n_games": int(g["game_id"].nunique()),
            "league": {"fga": league["fga"], "fgm": league["fgm"], "fg3a": league["fg3a"], "fg3m": league["fg3m"]},
            "shots": shots, "hexes": _hex_cells(g, league), "grid": [[q, r, z] for q, r, _a, _m, z in league["hexes"]], "zones": _zone_rows(g, league),
            "groups": _group_rows(g, league), "games": _games(g), "venue": _venue(g),
            "opponent": _opponent(league, opp),
        }
    return {"status": "no_player", "seasons": seasons}
