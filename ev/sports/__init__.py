from ev.sports.base import SportAdapter
from ev.sports.nba import NBA
from ev.sports.ncaab import NCAAB
from ev.sports.ncaaf import NCAAF
from ev.sports.nfl import NFL
from ev.sports.nhl import NHL
from ev.sports.soccer import SOCCER

ADAPTERS = {"nfl": NFL, "nba": NBA, "nhl": NHL, "soccer": SOCCER, "ncaaf": NCAAF, "ncaab": NCAAB}


def get_adapter(sport: str) -> SportAdapter:
    try:
        return ADAPTERS[sport.lower()]
    except KeyError:
        raise ValueError(f"unknown sport {sport!r}; expected one of {sorted(ADAPTERS)}") from None


# Repo-canonical team code (ev.daily_projections.canon_team) -> Kalshi's code, where they differ. NHL only: the repo uses LAK/NJD/SJS/TBL,
# Kalshi LA/NJ/SJ/TB (read from live KXNHLGAME markets). NFL (JAC/LAR/WAS) and NBA codes already agree.
_KALSHI_CROSSWALK = {"nhl": {"LAK": "LA", "NJD": "NJ", "SJS": "SJ", "TBL": "TB"}}


def kalshi_code(sport: str, code):
    """Canonical team code -> the code Kalshi uses for that league, or None when it is not in the league's complete Kalshi code
    table (fail closed: a game with an unresolvable team is not priced)."""
    if not code:
        return None
    c = _KALSHI_CROSSWALK.get(sport, {}).get(str(code).upper(), str(code).upper())
    return c if c in ADAPTERS[sport.lower()].codes else None
