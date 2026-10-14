"""NHL adapter. Codes read from live KXNHLGAME markets 2026-10-04 (note LA, NJ, SJ, TB, VGK, WSH)."""
from ev.sports.base import SportAdapter

CODES = frozenset("""ANA BOS BUF CAR CBJ CGY CHI COL DAL DET EDM FLA LA MIN MTL NJ NSH NYI NYR OTT PHI PIT SEA
SJ STL TB TOR UTA VAN VGK WPG WSH""".split())

NHL = SportAdapter(
    sport="nhl", kalshi_prefix="KXNHL", league_phrase="NHL", unit="goals",
    codes=CODES,
    # KXNHLTOTAL rules_secondary: "If the game is decided by a shootout, the shootout result will be
    # recorded as one goal awarded to the winning team." Sportsbook NHL totals typically count regulation
    # + OT goals only (UNVERIFIED per book), so an Over at a line a shootout game lands on resolves
    # DIFFERENTLY on Kalshi than at the book. Not the same proposition -> refuse until modelled/verified.
    disabled={"total": "Kalshi counts a shootout as one goal; sportsbook totals typically do not"},
)
