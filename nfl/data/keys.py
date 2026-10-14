"""Join keys between nflverse data and the rest of this repo.

nflverse's canonical team codes differ from the Kalshi/ParlayAPI codes used by ev/ in exactly two places
(JAX vs JAC, LA vs LAR; verified against nflreadr's team_abbr_mapping, 2026-10-14), and its history spans
franchise moves (OAK -> LV, SD -> LAC, STL -> LA). `ev_code` maps every nflverse spelling to the ev/ code so a
schedule row can be joined to a Kalshi event or a ParlayAPI slate event by `game_key`.

game_key format is owned by ev.kalshi_map.game_key: "{sport}:{YYYY-MM-DD ET}:{AWAY}@{HOME}" (a test pins this
module to it). nflverse's `gameday` is the game's calendar date (its `gametime` is given in ET); ev/ keys on the
ET date. Spot-check a late-night/international game against Kalshi before trusting the join at the date boundary.
"""
from typing import Optional

import pandas as pd

# Every nflverse team spelling -> ev/ (Kalshi) code. Only differences + relocations need listing.
_TO_EV = {"JAX": "JAC", "LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR", "SL": "LAR", "LVR": "LV",
          "ARZ": "ARI", "WSH": "WAS"}
_EV_CODES = frozenset("""ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAC KC LAC LAR LV MIA MIN NE NO
NYG NYJ PHI PIT SEA SF TB TEN WAS""".split())


def ev_code(abbr) -> Optional[str]:
    """nflverse team abbreviation -> ev/ Kalshi code, or None if it is not an NFL franchise (fail closed)."""
    if not isinstance(abbr, str):
        return None
    code = _TO_EV.get(abbr.strip().upper(), abbr.strip().upper())
    return code if code in _EV_CODES else None


def make_game_key(game_date: str, away: str, home: str, sport: str = "nfl") -> Optional[str]:
    a, h = ev_code(away), ev_code(home)
    if not a or not h or not game_date:
        return None
    return f"{sport}:{game_date}:{a}@{h}"


def add_game_keys(schedules: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of an nflverse `schedules` frame with `game_key` (None where a team is unrecognised)."""
    out = schedules.copy()
    out["game_key"] = [make_game_key(str(d)[:10], a, h) for d, a, h in
                       zip(out["gameday"], out["away_team"], out["home_team"])]
    return out
