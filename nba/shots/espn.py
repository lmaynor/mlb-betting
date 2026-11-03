"""ESPN play-by-play (hoopR / sportsdataverse `espn_nba_pbp` release) -> the canonical shot frame of `nba.shots.court`.

Why this adapter exists: the supplied shot file ends 2025-04-13, ESPN's play-by-play is the only source in reach for 2025-26 and
the live 2026-27 season. ESPN carries NO zone labels, so zones come from `zone_grid.json` (the majority zone of each 0.5 ft cell
of the supplied file, 98.9% agreement on a held-out season) with the restricted area applied exactly (<= 4.0 ft).

ESPN frame -> canonical frame (calibrated 2026-11-03 on the 2024-25 file, see docs/solutions/integration-issues/
nba-shot-chart-source-and-licensing.md):
  * x runs the length of the court (-46.75..46.75), y is lateral (-25..25), shots are recorded toward BOTH baskets: attack the
    right basket by negating both axes when x < 0 (a 180 degree turn keeps handedness).
  * the hoop is 5.25 ft from the baseline, ESPN's baseline sits at 47: canonical y = (41.75 + 5.25) - x, canonical x = y. Negative
    canonical x is the shooter's right: per-player right-corner-three counts correlate 0.91 with the supplied file this way (0.72
    mirrored).
  * three = the text says "three point"; free throws, preseason, playoffs and play-in are dropped so a season matches the file.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from mlb_core.data.player_names import norm_name
from nba.shots.court import HALF_Y, HOOP, hex_of, shot_group
from nba.teams import team_code

HOOP_X_ESPN = 41.75
REGULAR = 2
RA_RADIUS = 4.0
_GRID = Path(__file__).with_name("zone_grid.json")


@lru_cache(maxsize=1)
def zone_grid():
    g = json.loads(_GRID.read_text())
    arr = np.array([[-1 if ch == "." else int(ch, 16) for ch in r] for r in g["rows"]], dtype=np.int16)
    return arr, g["cell_ft"], g["x0"], g["zones"]


def zone_index(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Canonical (x, y) feet -> index into ZONE_KEYS (-1 = none), grid lookup with the restricted area applied exactly."""
    arr, cell, x0, keys = zone_grid()
    ix = np.floor((x - x0) / cell).astype(int).clip(0, arr.shape[1] - 1)
    iy = np.floor(y / cell).astype(int).clip(0, arr.shape[0] - 1)
    z = arr[iy, ix].astype(np.int16)
    ra = keys.index("Restricted Area|C")
    z[np.hypot(x - HOOP[0], y - HOOP[1]) <= RA_RADIUS] = ra
    return z


def canonical_xy(cx: np.ndarray, cy: np.ndarray):
    """ESPN (coordinate_x, coordinate_y) -> canonical (lateral x, feet from baseline y)."""
    f = np.where(cx >= 0, 1.0, -1.0)
    hx, hy = cx * f, cy * f
    return hy, (HOOP_X_ESPN + HOOP[1]) - hx


def normalize_espn_frame(pbp: pd.DataFrame, release_season: int) -> pd.DataFrame:
    """One hoopR play-by-play season (release_season = the year the season ENDS) -> canonical shot frame, regular season only."""
    s = pbp[(pbp["shooting_play"] == True) & (pbp["season_type"] == REGULAR)].copy()      # noqa: E712 -- nullable bool column
    s = s[~s["type_text"].astype(str).str.contains("Free Throw", na=False)]
    s = s.dropna(subset=["coordinate_x", "coordinate_y", "athlete_id_1", "team_id", "game_date"])
    home = s["team_id"].astype(str) == s["home_team_id"].astype(str)
    team = np.where(home, s["home_team_abbrev"], s["away_team_abbrev"])
    opp = np.where(home, s["away_team_abbrev"], s["home_team_abbrev"])
    s["team"] = [team_code(str(a)) for a in team]
    s["opp"] = [team_code(str(a)) for a in opp]
    s = s[s["team"].notna() & s["opp"].notna()].copy()                                    # All-Star / exhibition teams do not resolve
    s["home"] = (s["team_id"].astype(str) == s["home_team_id"].astype(str)).to_numpy()
    x, y = canonical_xy(s["coordinate_x"].to_numpy(float), s["coordinate_y"].to_numpy(float))
    s["x"], s["y"] = np.round(x, 2), np.round(y, 2)
    s = s[(s["y"] >= 0) & (s["y"] <= HALF_Y) & (s["x"].abs() <= 25)].copy()
    s["made"] = s["scoring_play"].astype(bool)
    s["three"] = s["text"].astype(str).str.contains("three point", case=False, na=False)
    s["group"] = s["type_text"].map(shot_group)
    s["zone"] = zone_index(s["x"].to_numpy(), s["y"].to_numpy())
    s["season"] = np.int16(release_season - 1)
    s["player_id"] = pd.to_numeric(s["athlete_id_1"], errors="coerce").astype("int64")
    s = s[s["athlete_name_1"].notna()].copy()                                              # a few plays carry an id but no name
    s["name"] = s["athlete_name_1"].astype(str)
    s["name_norm"] = [norm_name(n) for n in s["name"]]
    s["game_id"] = s["game_id"].astype(str)
    s["date"] = pd.to_datetime(s["game_date"]).dt.strftime("%Y-%m-%d")
    hq = [hex_of(a, b) for a, b in zip(s["x"].to_numpy(), s["y"].to_numpy())]
    s["hex_q"] = [h[0] for h in hq]
    s["hex_r"] = [h[1] for h in hq]
    cols = ["season", "player_id", "name", "name_norm", "team", "opp", "home", "game_id", "date", "x", "y", "made", "three",
            "group", "zone", "hex_q", "hex_r"]
    return s[cols].drop_duplicates(["game_id", "player_id", "x", "y", "made"]).reset_index(drop=True)
