"""
mlb_core.odds.dk_scraper — DraftKings team-name resolution.

LEGACY: this module used to be a full DK odds-scraping client (Selenium,
then direct HTTP). ParlayAPI became the primary live-odds source
(2026-06-29 migration) and the scraping code was never called again.
Trimmed 2026-09-04 to just resolve_team()/TEAM_NAME_TO_ABBREV, the one
piece still imported elsewhere (parlay_adapter.py, weather.py, lineups.py,
game_result.py, run_hr.py, run_f5.py, run_nrfi.py, run_1i.py,
parlayapi_to_history.py, capture_closing_lines.py) -- confirmed via grep
that fetch_dk_payloads/fetch_mlb_events/_fetch_json/_session/dk_to_int had
zero callers anywhere in the repo.
"""
from typing import Optional

# ---------------------------------------------------------------------------
# Team name maps
# ---------------------------------------------------------------------------

DK_NAME_TO_ABBR: dict = {
    "ARI": "ARI", "ATL": "ATL", "BAL": "BAL", "BOS": "BOS",
    "CHC": "CHC", "CWS": "CWS", "CIN": "CIN", "CLE": "CLE",
    "COL": "COL", "DET": "DET", "HOU": "HOU", "KC":  "KC",
    "LAA": "LAA", "LAD": "LAD", "MIA": "MIA", "MIL": "MIL",
    "MIN": "MIN", "NYM": "NYM", "NYY": "NYY", "OAK": "OAK",
    "PHI": "PHI", "PIT": "PIT", "SD":  "SD",  "SF":  "SF",
    "SEA": "SEA", "STL": "STL", "TB":  "TB",  "TEX": "TEX",
    "TOR": "TOR", "WSH": "WSH",
    "A's": "OAK", "AZ": "ARI", "ATH": "OAK",
    "KAN": "KC",  "ROY": "KC", "WAS": "WSH", "MIN Twins": "MIN",
    "Athletics":    "OAK", "Diamondbacks": "ARI", "Braves":    "ATL",
    "Orioles":      "BAL", "Red Sox":      "BOS", "Cubs":      "CHC",
    "White Sox":    "CWS", "Reds":         "CIN", "Guardians": "CLE",
    "Rockies":      "COL", "Tigers":       "DET", "Astros":    "HOU",
    "Royals":       "KC",  "Angels":       "LAA", "Dodgers":   "LAD",
    "Marlins":      "MIA", "Brewers":      "MIL", "Twins":     "MIN",
    "Mets":         "NYM", "Yankees":      "NYY", "Phillies":  "PHI",
    "Pirates":      "PIT", "Padres":       "SD",  "Giants":    "SF",
    "Mariners":     "SEA", "Cardinals":    "STL", "Rays":      "TB",
    "Rangers":      "TEX", "Blue Jays":    "TOR", "Nationals": "WSH",
}

TEAM_NAME_TO_ABBREV: dict = {
    "Arizona Diamondbacks":  "ARI", "Atlanta Braves":       "ATL",
    "Baltimore Orioles":     "BAL", "Boston Red Sox":       "BOS",
    "Chicago Cubs":          "CHC", "Chicago White Sox":    "CWS",
    "Cincinnati Reds":       "CIN", "Cleveland Guardians":  "CLE",
    "Cleveland Indians":     "CLE", "Colorado Rockies":     "COL",
    "Detroit Tigers":        "DET", "Houston Astros":       "HOU",
    "Kansas City Royals":    "KC",  "Los Angeles Angels":   "LAA",
    "Los Angeles Dodgers":   "LAD", "Miami Marlins":        "MIA",
    "Milwaukee Brewers":     "MIL", "Minnesota Twins":      "MIN",
    "New York Mets":         "NYM", "New York Yankees":     "NYY",
    "Oakland Athletics":     "OAK", "Athletics":            "OAK",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates":   "PIT",
    "San Diego Padres":      "SD",  "San Francisco Giants": "SF",
    "Seattle Mariners":      "SEA", "St. Louis Cardinals":  "STL",
    "Tampa Bay Rays":        "TB",  "Texas Rangers":        "TEX",
    "Toronto Blue Jays":     "TOR", "Washington Nationals": "WSH",
}

# ---------------------------------------------------------------------------
# Team name resolution
# ---------------------------------------------------------------------------

def resolve_team(name: str) -> Optional[str]:
    """Resolve any DK team name/fragment to standard 3-letter abbreviation."""
    if not name:
        return None
    if name in DK_NAME_TO_ABBR:
        return DK_NAME_TO_ABBR[name]
    for fragment, abbr in DK_NAME_TO_ABBR.items():
        if fragment and fragment in name:
            return abbr
    return None
