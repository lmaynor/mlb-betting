"""NFL adapter. Codes read from live KXNFLGAME markets 2026-10-04 (note JAC, LV)."""
from ev.sports.base import SportAdapter

CODES = frozenset("""ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAC KC LAC LAR LV MIA MIN NE NO
NYG NYJ PHI PIT SEA SF TB TEN WAS""".split())

NFL = SportAdapter(
    sport="nfl", kalshi_prefix="KXNFL", league_phrase="Pro Football", unit="points",
    codes=CODES,
    # rules_secondary: "If the game ends in a tie, the market will resolve to $0.50 for each team."
    tie_policy="half",
)

# ParlayAPI / book full team names -> Kalshi codes (used to join a book event to a Kalshi event).
TEAM_NAMES = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR", "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAC",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}
