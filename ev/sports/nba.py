"""NBA adapter. 29 codes read from live KXNBAGAME markets 2026-10-04; TOR added (not scheduled in the
open set that day, UNVERIFIED -- the mapper fails closed on any code it does not know)."""
from ev.sports.base import SportAdapter

CODES = frozenset("""ATL BKN BOS CHA CHI CLE DAL DEN DET GSW HOU IND LAC LAL MEM MIA MIL MIN NOP NYK OKC ORL
PHI PHX POR SAC SAS TOR UTA WAS""".split())

NBA = SportAdapter(
    sport="nba", kalshi_prefix="KXNBA", league_phrase="Pro Basketball", unit="points",
    codes=CODES,
)
