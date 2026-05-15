"""
mlb_core.data.game_result -- Fetch game results from MLB Stats API.

Single entry point for all settlement logic. Makes two API calls per
game_pk (linescore + boxscore) and returns a structured dict covering
every market we currently settle or may settle in future.

Usage:
    from mlb_core.data.game_result import fetch_game_result

    result = fetch_game_result(824196)
    if result is None:
        # game not Final yet -- skip
        pass
    else:
        # NRFI/F5: result["innings"][0]["away_runs"], ["home_runs"]
        # K:       result["pitchers"]["luis castillo"]["strikeouts"]
        # OUTS:    result["pitchers"]["luis castillo"]["outs"]
        # HR:      result["batters"]["aaron judge"]["home_runs"]

Returns None if the game is not yet Final or if the API call fails.
Caller should skip (retry tomorrow) on None.

All player names are normalized: NFD + ASCII fold + lower + strip.
Pitchers list includes all pitchers who appeared (not just starter).
is_starter=True means battingOrder % 100 == 0 (position 1-9 in lineup).
"""
from __future__ import annotations
import logging
import unicodedata

import requests

logger = logging.getLogger(__name__)

_MLB_API = "https://statsapi.mlb.com/api/v1"
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "mlb-betting/1.0"})


def _norm(s: str) -> str:
    """NFD + ASCII fold + lower + strip."""
    if not isinstance(s, str):
        return ""
    n = unicodedata.normalize("NFD", s)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.encode("ascii", "ignore").decode().lower().strip()


def _get(path: str, params: dict = None) -> dict | None:
    try:
        r = _SESSION.get(f"{_MLB_API}{path}", params=params or {}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"game_result: API call failed {path}: {e}")
        return None


def fetch_game_result(game_pk: int) -> dict | None:
    """
    Fetch full game result from MLB Stats API.

    Returns None if game is not Final or API call fails.

    Return structure:
    {
        "game_pk": int,
        "final": True,
        "innings": [
            {"num": 1, "away_runs": 0, "home_runs": 0,
             "away_hits": 0, "home_hits": 0},
            ...
        ],
        "pitchers": {
            "luis castillo": {
                "starter": True,
                "strikeouts": 6,
                "outs": 17,
                "innings_pitched": "5.2",
                "earned_runs": 3,
                "hits_allowed": 4,
                "walks": 3,
                "home_runs_allowed": 1,
                "pitches_thrown": 108,
                "wins": 1,
                "losses": 0,
                "saves": 0,
            }
        },
        "batters": {
            "aaron judge": {
                "starter": True,
                "batting_order": 100,
                "at_bats": 4,
                "plate_appearances": 5,
                "hits": 1,
                "home_runs": 0,
                "rbi": 1,
                "runs": 1,
                "doubles": 0,
                "triples": 0,
                "walks": 1,
                "strikeouts": 1,
                "stolen_bases": 0,
                "total_bases": 1,
            }
        }
    }
    """
    # Check game status
    sched = _get(f"/schedule", {"gamePk": game_pk})
    if not sched:
        return None
    try:
        game = sched["dates"][0]["games"][0]
        state = game["status"]["abstractGameState"]
        if state != "Final":
            return None
    except (KeyError, IndexError):
        return None

    # Fetch linescore
    linescore = _get(f"/game/{game_pk}/linescore")
    if not linescore:
        return None

    innings = []
    for inn in linescore.get("innings", []):
        innings.append({
            "num":        inn["num"],
            "away_runs":  inn.get("away", {}).get("runs", 0),
            "home_runs":  inn.get("home", {}).get("runs", 0),
            "away_hits":  inn.get("away", {}).get("hits", 0),
            "home_hits":  inn.get("home", {}).get("hits", 0),
        })

    # Fetch boxscore
    boxscore = _get(f"/game/{game_pk}/boxscore")
    if not boxscore:
        return None

    pitchers: dict = {}
    batters: dict  = {}

    for side in ("away", "home"):
        team = boxscore.get("teams", {}).get(side, {})
        players = team.get("players", {})
        starter_pitcher_id = team.get("pitchers", [None])[0]

        for pid, p in players.items():
            name = _norm(p.get("person", {}).get("fullName", ""))
            if not name:
                continue

            mlbam_id = p.get("person", {}).get("id")
            batting_order = p.get("battingOrder")
            try:
                is_starter_batter = int(str(batting_order)) % 100 == 0
            except (ValueError, TypeError):
                is_starter_batter = False

            # Pitching stats
            pit = p.get("stats", {}).get("pitching", {})
            if pit and pit.get("gamesPlayed", 0) > 0:
                is_starter_pitcher = (
                    str(mlbam_id) == str(starter_pitcher_id)
                    if starter_pitcher_id else False
                )
                pitchers[name] = {
                    "starter":           is_starter_pitcher,
                    "mlbam_id":          mlbam_id,
                    "strikeouts":        int(pit.get("strikeOuts", 0)),
                    "outs":              int(pit.get("outs", 0)),
                    "innings_pitched":   pit.get("inningsPitched", "0.0"),
                    "earned_runs":       int(pit.get("earnedRuns", 0)),
                    "hits_allowed":      int(pit.get("hits", 0)),
                    "walks":             int(pit.get("baseOnBalls", 0)),
                    "home_runs_allowed": int(pit.get("homeRuns", 0)),
                    "pitches_thrown":    int(pit.get("numberOfPitches", 0)),
                    "wins":              int(pit.get("wins", 0)),
                    "losses":            int(pit.get("losses", 0)),
                    "saves":             int(pit.get("saves", 0)),
                }

            # Batting stats
            bat = p.get("stats", {}).get("batting", {})
            if bat and bat.get("gamesPlayed", 0) > 0:
                total_bases = int(bat.get("totalBases", 0))
                if total_bases == 0:
                    # Compute from hits if totalBases missing
                    total_bases = (
                        int(bat.get("hits", 0))
                        + int(bat.get("doubles", 0))
                        + int(bat.get("triples", 0)) * 2
                        + int(bat.get("homeRuns", 0)) * 3
                    )
                batters[name] = {
                    "starter":          is_starter_batter,
                    "batting_order":    batting_order,
                    "mlbam_id":         mlbam_id,
                    "at_bats":          int(bat.get("atBats", 0)),
                    "plate_appearances":int(bat.get("plateAppearances", 0)),
                    "hits":             int(bat.get("hits", 0)),
                    "home_runs":        int(bat.get("homeRuns", 0)),
                    "rbi":              int(bat.get("rbi", 0)),
                    "runs":             int(bat.get("runs", 0)),
                    "doubles":          int(bat.get("doubles", 0)),
                    "triples":          int(bat.get("triples", 0)),
                    "walks":            int(bat.get("baseOnBalls", 0)),
                    "strikeouts":       int(bat.get("strikeOuts", 0)),
                    "stolen_bases":     int(bat.get("stolenBases", 0)),
                    "total_bases":      total_bases,
                }

    return {
        "game_pk":  game_pk,
        "final":    True,
        "innings":  innings,
        "pitchers": pitchers,
        "batters":  batters,
    }
