from ev.sports.base import SportAdapter
from ev.sports.nba import NBA
from ev.sports.nfl import NFL
from ev.sports.nhl import NHL

ADAPTERS = {"nfl": NFL, "nba": NBA, "nhl": NHL}


def get_adapter(sport: str) -> SportAdapter:
    try:
        return ADAPTERS[sport.lower()]
    except KeyError:
        raise ValueError(f"unknown sport {sport!r}; expected one of {sorted(ADAPTERS)}") from None
