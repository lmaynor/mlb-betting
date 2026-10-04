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
