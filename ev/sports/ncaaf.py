"""NCAAF adapter. Kalshi prefix KXNCAAF (series KXNCAAFGAME / KXNCAAFSPREAD / KXNCAFTOTAL, verified in Kalshi's
/series listing 2026-11-10; note KXNCAFTOTAL drops an A). Codes are the 2026-11-10 census of open KXNCAAFGAME
markets (ev.sports.ncaa_teams): every code Kalshi itself printed a yes_sub_title for. A code outside the census is
not priced (fail closed). PARSING uses the census minus the parse-dropped codes (ME Maine, ND Notre Dame -- see
ev.sports.ncaa_teams): Kalshi's separator-free code concatenation is not split-unique over the full census, so
blobs needing a dropped code fail closed as ambiguous instead of being guessed; the banked-code fair-book path
validates codes against the full census, not this parse set. Rules text was NOT re-read for this league: the strict market mapper (ev.kalshi_map)
asserts the league_phrase against live rules before mapping any market, so an unverified phrase fails closed there
rather than guessing; the RFQ fair-book path (ticker build + parse_event round-trip) does not consult rules text.
Tie policy stays the base 'none' (college games cannot end tied under overtime rules), so map_market's $0.50
tie assertion is never armed for this league."""
from ev.sports.base import SportAdapter
from ev.sports.ncaa_teams import NCAAF_PARSE_CODES as CODES

NCAAF = SportAdapter(
    sport="ncaaf", kalshi_prefix="KXNCAAF", league_phrase="College Football", unit="points",
    codes=CODES,
    series_tickers={"total": "KXNCAFTOTAL"},          # verified in the /series listing 2026-11-10 (one A)
)
