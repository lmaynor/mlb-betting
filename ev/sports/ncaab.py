"""NCAAB (men's) adapter. Kalshi prefix KXNCAAMB (series KXNCAAMBGAME / KXNCAAMBSPREAD / KXNCAAMBTOTAL, verified in
Kalshi's /series listing 2026-11-10). The women's series is KXNCAAWB* -- a DIFFERENT prefix on purpose: nothing in
the stack carries women's college basketball lines, so those legs classify sport_not_covered and stay unpriced.
Codes are the 2026-11-10 census of open KXNCAAMBGAME markets (ev.sports.ncaa_teams); a code outside the census is
not priced (fail closed). PARSING uses the census minus the parse-dropped codes (AMER, CSB, EVAN, ILL, MASS, SF --
see ev.sports.ncaa_teams): Kalshi's separator-free code concatenation is not split-unique over the full census,
so blobs needing a dropped code fail closed as ambiguous instead of being guessed; the banked-code fair-book path
validates codes against the full census, not this parse set. Rules text was NOT re-read for this league: the strict market mapper (ev.kalshi_map)
asserts the league_phrase against live rules before mapping any market, so an unverified phrase fails closed there
rather than guessing; the RFQ fair-book path (ticker build + parse_event round-trip) does not consult rules text."""
from ev.sports.base import SportAdapter
from ev.sports.ncaa_teams import NCAAB_PARSE_CODES as CODES

NCAAB = SportAdapter(
    sport="ncaab", kalshi_prefix="KXNCAAMB", league_phrase="College Basketball", unit="points",
    codes=CODES,
)
