"""soccer.data.reep_register -- the Reep Register identity bridge for clubs (fail closed).

Every soccer data source spells the same club differently: ParlayAPI says "Manchester United", football-data.co.uk
says "Man United", Understat says "Manchester United", ClubElo says "Man United", Kalshi titles say "Manchester
United" while its tickers use codes. A naive join on names silently drops or mis-joins matches, so ALL soccer code
resolves club identity through this module and NOTHING fuzzy-matches: resolve_club() returns the canonical code on
an exact hit against the register, or None (plus an UNRESOLVED_SOCCER_CLUB warning) and the caller skips the match.

The register ships with a built-in seed dictionary covering every EPL club and the UCL league-phase majors, mapping
each canonical code across the providers this vertical reads (parlayapi, understat, football_data, clubelo,
bestbook, kalshi). A downloaded Reep Register CSV (Soccer/reep_register_clubs.csv in storage, or a local path via
REEP_REGISTER_CSV) is merged on top best-effort when present: seed entries win on conflict, CSV rows only add.

Canonical codes are the Kalshi ticker codes where Kalshi lists the club (ARS, MCI, LIV, ...), so a resolved club is
directly usable in ev.kalshi_game_legs ticker construction.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re

log = logging.getLogger("soccer.reep_register")

PROVIDERS = ("parlayapi", "understat", "football_data", "clubelo", "bestbook", "kalshi")

# code, canonical name, leagues, {provider: that provider's spelling}
_SEED: list[tuple[str, str, tuple[str, ...], dict[str, str]]] = [
    # ---- Premier League (2026-27) ----
    ("ARS", "Arsenal", ("epl", "ucl"), {"parlayapi": "Arsenal", "understat": "Arsenal", "football_data": "Arsenal", "clubelo": "Arsenal", "bestbook": "Arsenal", "kalshi": "Arsenal"}),
    ("AVL", "Aston Villa", ("epl", "ucl"), {"parlayapi": "Aston Villa", "understat": "Aston Villa", "football_data": "Aston Villa", "clubelo": "Aston Villa", "bestbook": "Aston Villa", "kalshi": "Aston Villa"}),
    ("BOU", "Bournemouth", ("epl",), {"parlayapi": "Bournemouth", "understat": "Bournemouth", "football_data": "Bournemouth", "clubelo": "Bournemouth", "bestbook": "Bournemouth", "kalshi": "Bournemouth"}),
    ("BRE", "Brentford", ("epl",), {"parlayapi": "Brentford", "understat": "Brentford", "football_data": "Brentford", "clubelo": "Brentford", "bestbook": "Brentford", "kalshi": "Brentford"}),
    ("BHA", "Brighton", ("epl",), {"parlayapi": "Brighton and Hove Albion", "understat": "Brighton", "football_data": "Brighton", "clubelo": "Brighton", "bestbook": "Brighton", "kalshi": "Brighton and Hove Albion"}),
    ("BUR", "Burnley", ("epl",), {"parlayapi": "Burnley", "understat": "Burnley", "football_data": "Burnley", "clubelo": "Burnley", "bestbook": "Burnley", "kalshi": "Burnley"}),
    ("CHE", "Chelsea", ("epl", "ucl"), {"parlayapi": "Chelsea", "understat": "Chelsea", "football_data": "Chelsea", "clubelo": "Chelsea", "bestbook": "Chelsea", "kalshi": "Chelsea"}),
    ("CRY", "Crystal Palace", ("epl",), {"parlayapi": "Crystal Palace", "understat": "Crystal Palace", "football_data": "Crystal Palace", "clubelo": "Crystal Palace", "bestbook": "Crystal Palace", "kalshi": "Crystal Palace"}),
    ("EVE", "Everton", ("epl",), {"parlayapi": "Everton", "understat": "Everton", "football_data": "Everton", "clubelo": "Everton", "bestbook": "Everton", "kalshi": "Everton"}),
    ("FUL", "Fulham", ("epl",), {"parlayapi": "Fulham", "understat": "Fulham", "football_data": "Fulham", "clubelo": "Fulham", "bestbook": "Fulham", "kalshi": "Fulham"}),
    ("LEE", "Leeds United", ("epl",), {"parlayapi": "Leeds United", "understat": "Leeds", "football_data": "Leeds", "clubelo": "Leeds", "bestbook": "Leeds United", "kalshi": "Leeds United"}),
    ("LIV", "Liverpool", ("epl", "ucl"), {"parlayapi": "Liverpool", "understat": "Liverpool", "football_data": "Liverpool", "clubelo": "Liverpool", "bestbook": "Liverpool", "kalshi": "Liverpool"}),
    ("MCI", "Manchester City", ("epl", "ucl"), {"parlayapi": "Manchester City", "understat": "Manchester City", "football_data": "Man City", "clubelo": "Man City", "bestbook": "Manchester City", "kalshi": "Manchester City"}),
    ("MUN", "Manchester United", ("epl",), {"parlayapi": "Manchester United", "understat": "Manchester United", "football_data": "Man United", "clubelo": "Man United", "bestbook": "Manchester United", "kalshi": "Manchester United"}),
    ("NEW", "Newcastle United", ("epl", "ucl"), {"parlayapi": "Newcastle United", "understat": "Newcastle United", "football_data": "Newcastle", "clubelo": "Newcastle", "bestbook": "Newcastle United", "kalshi": "Newcastle United"}),
    ("NFO", "Nottingham Forest", ("epl",), {"parlayapi": "Nottingham Forest", "understat": "Nottingham Forest", "football_data": "Nott'm Forest", "clubelo": "Forest", "bestbook": "Nottingham Forest", "kalshi": "Nottingham Forest"}),
    ("SUN", "Sunderland", ("epl",), {"parlayapi": "Sunderland", "understat": "Sunderland", "football_data": "Sunderland", "clubelo": "Sunderland", "bestbook": "Sunderland", "kalshi": "Sunderland"}),
    ("TOT", "Tottenham Hotspur", ("epl", "ucl"), {"parlayapi": "Tottenham Hotspur", "understat": "Tottenham", "football_data": "Tottenham", "clubelo": "Tottenham", "bestbook": "Tottenham Hotspur", "kalshi": "Tottenham Hotspur"}),
    ("WHU", "West Ham United", ("epl",), {"parlayapi": "West Ham United", "understat": "West Ham", "football_data": "West Ham", "clubelo": "West Ham", "bestbook": "West Ham United", "kalshi": "West Ham United"}),
    ("WOL", "Wolverhampton Wanderers", ("epl",), {"parlayapi": "Wolverhampton Wanderers", "understat": "Wolverhampton Wanderers", "football_data": "Wolves", "clubelo": "Wolves", "bestbook": "Wolverhampton Wanderers", "kalshi": "Wolverhampton Wanderers"}),
    # ---- Champions League majors (league phase) ----
    ("RMA", "Real Madrid", ("ucl", "laliga"), {"parlayapi": "Real Madrid", "understat": "Real Madrid", "football_data": "Real Madrid", "clubelo": "Real Madrid", "bestbook": "Real Madrid", "kalshi": "Real Madrid"}),
    ("BAR", "Barcelona", ("ucl", "laliga"), {"parlayapi": "Barcelona", "understat": "Barcelona", "football_data": "Barcelona", "clubelo": "Barcelona", "bestbook": "Barcelona", "kalshi": "Barcelona"}),
    ("ATM", "Atletico Madrid", ("ucl", "laliga"), {"parlayapi": "Atletico Madrid", "understat": "Atletico Madrid", "football_data": "Ath Madrid", "clubelo": "Atletico", "bestbook": "Atletico Madrid", "kalshi": "Atletico Madrid"}),
    ("VIL", "Villarreal", ("ucl", "laliga"), {"parlayapi": "Villarreal", "understat": "Villarreal", "football_data": "Villarreal", "clubelo": "Villarreal", "bestbook": "Villarreal", "kalshi": "Villarreal"}),
    ("ATH", "Athletic Club", ("ucl", "laliga"), {"parlayapi": "Athletic Club", "understat": "Athletic Club", "football_data": "Ath Bilbao", "clubelo": "Athletic", "bestbook": "Athletic Club", "kalshi": "Athletic Club"}),
    ("BAY", "Bayern Munich", ("ucl", "bundesliga"), {"parlayapi": "Bayern Munich", "understat": "Bayern Munich", "football_data": "Bayern Munich", "clubelo": "Bayern", "bestbook": "Bayern Munich", "kalshi": "Bayern Munich"}),
    ("DOR", "Borussia Dortmund", ("ucl", "bundesliga"), {"parlayapi": "Borussia Dortmund", "understat": "Borussia Dortmund", "football_data": "Dortmund", "clubelo": "Dortmund", "bestbook": "Borussia Dortmund", "kalshi": "Borussia Dortmund"}),
    ("LEV", "Bayer Leverkusen", ("ucl", "bundesliga"), {"parlayapi": "Bayer Leverkusen", "understat": "Bayer Leverkusen", "football_data": "Leverkusen", "clubelo": "Leverkusen", "bestbook": "Bayer Leverkusen", "kalshi": "Bayer Leverkusen"}),
    ("FRA", "Eintracht Frankfurt", ("ucl", "bundesliga"), {"parlayapi": "Eintracht Frankfurt", "understat": "Eintracht Frankfurt", "football_data": "Ein Frankfurt", "clubelo": "Frankfurt", "bestbook": "Eintracht Frankfurt", "kalshi": "Eintracht Frankfurt"}),
    ("INT", "Inter", ("ucl", "seriea"), {"parlayapi": "Inter Milan", "understat": "Inter", "football_data": "Inter", "clubelo": "Inter", "bestbook": "Inter Milan", "kalshi": "Inter Milan"}),
    ("MIL", "AC Milan", ("ucl", "seriea"), {"parlayapi": "AC Milan", "understat": "AC Milan", "football_data": "Milan", "clubelo": "Milan", "bestbook": "AC Milan", "kalshi": "AC Milan"}),
    ("JUV", "Juventus", ("ucl", "seriea"), {"parlayapi": "Juventus", "understat": "Juventus", "football_data": "Juventus", "clubelo": "Juventus", "bestbook": "Juventus", "kalshi": "Juventus"}),
    ("NAP", "Napoli", ("ucl", "seriea"), {"parlayapi": "Napoli", "understat": "Napoli", "football_data": "Napoli", "clubelo": "Napoli", "bestbook": "Napoli", "kalshi": "Napoli"}),
    ("ATA", "Atalanta", ("ucl", "seriea"), {"parlayapi": "Atalanta", "understat": "Atalanta", "football_data": "Atalanta", "clubelo": "Atalanta", "bestbook": "Atalanta", "kalshi": "Atalanta"}),
    ("PSG", "Paris Saint-Germain", ("ucl", "ligue1"), {"parlayapi": "Paris Saint Germain", "understat": "Paris Saint Germain", "football_data": "Paris SG", "clubelo": "PSG", "bestbook": "Paris Saint-Germain", "kalshi": "Paris Saint-Germain"}),
    ("OM", "Marseille", ("ucl", "ligue1"), {"parlayapi": "Marseille", "understat": "Marseille", "football_data": "Marseille", "clubelo": "Marseille", "bestbook": "Marseille", "kalshi": "Marseille"}),
    ("MON", "Monaco", ("ucl", "ligue1"), {"parlayapi": "Monaco", "understat": "Monaco", "football_data": "Monaco", "clubelo": "Monaco", "bestbook": "Monaco", "kalshi": "Monaco"}),
    ("SCP", "Sporting CP", ("ucl",), {"parlayapi": "Sporting CP", "understat": "Sporting CP", "football_data": "Sp Lisbon", "clubelo": "Sporting", "bestbook": "Sporting CP", "kalshi": "Sporting CP"}),
    ("BEN", "Benfica", ("ucl",), {"parlayapi": "Benfica", "understat": "Benfica", "football_data": "Benfica", "clubelo": "Benfica", "bestbook": "Benfica", "kalshi": "Benfica"}),
    ("POR", "FC Porto", ("ucl",), {"parlayapi": "FC Porto", "understat": "FC Porto", "football_data": "Porto", "clubelo": "Porto", "bestbook": "FC Porto", "kalshi": "FC Porto"}),
    ("AJA", "Ajax", ("ucl",), {"parlayapi": "Ajax", "understat": "Ajax", "football_data": "Ajax", "clubelo": "Ajax", "bestbook": "Ajax", "kalshi": "Ajax"}),
    ("PSV", "PSV Eindhoven", ("ucl",), {"parlayapi": "PSV Eindhoven", "understat": "PSV", "football_data": "PSV Eindhoven", "clubelo": "PSV", "bestbook": "PSV Eindhoven", "kalshi": "PSV Eindhoven"}),
    ("CEL", "Celtic", ("ucl",), {"parlayapi": "Celtic", "understat": "Celtic", "football_data": "Celtic", "clubelo": "Celtic", "bestbook": "Celtic", "kalshi": "Celtic"}),
    ("GAL", "Galatasaray", ("ucl",), {"parlayapi": "Galatasaray", "understat": "Galatasaray", "football_data": "Galatasaray", "clubelo": "Galatasaray", "bestbook": "Galatasaray", "kalshi": "Galatasaray"}),
    ("BRU", "Club Brugge", ("ucl",), {"parlayapi": "Club Brugge", "understat": "Club Brugge", "football_data": "Club Brugge", "clubelo": "Brugge", "bestbook": "Club Brugge", "kalshi": "Club Brugge"}),
    ("OLY", "Olympiacos", ("ucl",), {"parlayapi": "Olympiacos", "understat": "Olympiacos", "football_data": "Olympiakos", "clubelo": "Olympiacos", "bestbook": "Olympiacos", "kalshi": "Olympiacos"}),
    ("SLP", "Slavia Prague", ("ucl",), {"parlayapi": "Slavia Prague", "understat": "Slavia Praha", "football_data": "Slavia Prague", "clubelo": "Slavia Praha", "bestbook": "Slavia Prague", "kalshi": "Slavia Prague"}),
    ("COP", "FC Copenhagen", ("ucl",), {"parlayapi": "FC Copenhagen", "understat": "FC Copenhagen", "football_data": "FC Copenhagen", "clubelo": "Copenhagen", "bestbook": "FC Copenhagen", "kalshi": "FC Copenhagen"}),
    ("BOD", "Bodo/Glimt", ("ucl",), {"parlayapi": "Bodo/Glimt", "understat": "Bodoe/Glimt", "football_data": "Bodo/Glimt", "clubelo": "Bodo Glimt", "bestbook": "Bodo/Glimt", "kalshi": "Bodo/Glimt"}),
    ("QAR", "Qarabag", ("ucl",), {"parlayapi": "Qarabag", "understat": "Qarabag FK", "football_data": "Qarabag", "clubelo": "Qarabag", "bestbook": "Qarabag", "kalshi": "Qarabag"}),
    ("USG", "Union Saint-Gilloise", ("ucl",), {"parlayapi": "Union Saint-Gilloise", "understat": "Union Saint-Gilloise", "football_data": "Union SG", "clubelo": "Union SG", "bestbook": "Union Saint-Gilloise", "kalshi": "Union Saint-Gilloise"}),
    ("PAF", "Pafos", ("ucl",), {"parlayapi": "Pafos FC", "understat": "Pafos", "football_data": "Pafos", "clubelo": "Pafos", "bestbook": "Pafos FC", "kalshi": "Pafos FC"}),
    ("KAI", "Kairat Almaty", ("ucl",), {"parlayapi": "Kairat Almaty", "understat": "Kairat", "football_data": "Kairat Almaty", "clubelo": "Kairat", "bestbook": "Kairat Almaty", "kalshi": "Kairat Almaty"}),
    # ---- clubs ParlayAPI listed 2026-11-04 that the seed lacked (parlayapi spelling only: ratings for a club with no other provider
    #      column fail closed in the pricer; the odds board needs only the identity) ----
    ("AEK", "AEK Athens", ('ucl',), {"parlayapi": "AEK Athens"}),
    ("COV", "Coventry City", ('epl',), {"parlayapi": "Coventry City"}),
    ("HUL", "Hull City", ('epl',), {"parlayapi": "Hull City"}),
    ("IPS", "Ipswich Town", ('epl',), {"parlayapi": "Ipswich Town"}),
    ("COM", "Como", ('ucl',), {"parlayapi": "Como"}),
    ("LNS", "Lens", ('ucl',), {"parlayapi": "Lens"}),
    ("RBL", "RB Leipzig", ('ucl', 'bundesliga'), {"parlayapi": "RB Leipzig"}),
    ("BET", "Real Betis", ('ucl', 'laliga'), {"parlayapi": "Real Betis"}),
    ("SAB", "Sabah", ('ucl',), {"parlayapi": "Sabah"}),
    ("VIK", "Viking", ('ucl',), {"parlayapi": "Viking"}),
    ("FEN", "Fenerbahce", ('ucl',), {"parlayapi": "Fenerbahce"}),
]

CLUBS: dict[str, dict] = {}
for _code, _name, _leagues, _providers in _SEED:
    CLUBS[_code] = {"code": _code, "name": _name, "leagues": _leagues, "providers": dict(_providers)}


# ParlayAPI's live spellings ("Arsenal FC", "AFC Bournemouth"...), observed 2026-11-04. EXACT extra spellings only: nothing here fuzzy-matches.
_PARLAY_ALIASES: dict[str, tuple[str, ...]] = {
    "BOU": ('AFC Bournemouth',),
    "ARS": ('Arsenal FC',),
    "AVL": ('Aston Villa FC',),
    "BRE": ('Brentford FC',),
    "BHA": ('Brighton & Hove Albion FC',),
    "CHE": ('Chelsea FC',),
    "CRY": ('Crystal Palace FC',),
    "EVE": ('Everton FC',),
    "FUL": ('Fulham FC',),
    "LEE": ('Leeds United FC',),
    "LIV": ('Liverpool FC',),
    "MCI": ('Manchester City FC',),
    "MUN": ('Manchester United FC',),
    "NEW": ('Newcastle United FC',),
    "NFO": ('Nottingham Forest FC',),
    "SUN": ('Sunderland AFC',),
    "TOT": ('Tottenham Hotspur FC',),
    "WHU": ('West Ham United FC',),
    "WOL": ('Wolverhampton Wanderers FC',),
    "BUR": ('Burnley FC',),
    "HUL": ('Hull City AFC',),
    "IPS": ('Ipswich Town FC',),
    "COV": ('Coventry City FC',),
    "DOR": ('BV Borussia Dortmund',),
    "NAP": ('SSC Napoli',),
    "RMA": ('Real Madrid CF',),
    "BRU": ('Club Brugge KV',),
    "SLP": ('SK Slavia Praha',),
    "COM": ('Como 1907',),
    "LNS": ('Racing Club de Lens',),
    "BET": ('Real Betis Balompié',),
    "FEN": ('Fenerbahçe SK',),
    "SAB": ('Sabah FK',),
    "VIK": ('Viking FK',),
}

# ParlayAPI also lists corners / bookings props as pseudo-events named "Arsenal (Corners)": not clubs, skipped without a warning.
_NOT_A_CLUB = re.compile(r"\((?:Corners|Bookings)\)\s*$")

def _norm(value: str) -> str:
    return " ".join(str(value).strip().split()).casefold()


def _build_index() -> dict[str, str]:
    """normalized spelling -> canonical code, across canonical names, codes and every provider column."""
    index: dict[str, str] = {}
    for code, club in CLUBS.items():
        index[_norm(code)] = code
        index[_norm(club["name"])] = code
        for spelling in club["providers"].values():
            index[_norm(spelling)] = code
    for code, spellings in _PARLAY_ALIASES.items():
        for spelling in spellings:
            index.setdefault(_norm(spelling), code)
    return index


_INDEX: dict[str, str] = _build_index()


def _merge_csv_text(text: str) -> int:
    """Merge Reep Register CSV rows (canonical_code, canonical_name, parlayapi, understat, football_data, clubelo,
    bestbook, kalshi) into the register. Seed entries win: a CSV row never moves an existing spelling or code."""
    added = 0
    for row in csv.DictReader(io.StringIO(text)):
        code = str(row.get("canonical_code") or "").strip().upper()
        if not code:
            continue
        if code not in CLUBS:
            CLUBS[code] = {"code": code, "name": str(row.get("canonical_name") or code).strip(),
                           "leagues": (), "providers": {}}
            _INDEX[_norm(code)] = code
            added += 1
        club = CLUBS[code]
        for provider in PROVIDERS:
            spelling = str(row.get(provider) or "").strip()
            if spelling and _norm(spelling) not in _INDEX:
                club["providers"][provider] = spelling
                _INDEX[_norm(spelling)] = code
                added += 1
    return added


def load_register_csv(path: str | None = None) -> int:
    """Best-effort merge of a downloaded Reep Register CSV (see the module docstring). Returns rows/spellings added;
    0 when no CSV is available. Never raises: the seed dictionary is always the floor."""
    candidates = [path, os.environ.get("REEP_REGISTER_CSV")]
    for cand in candidates:
        if cand and os.path.exists(cand):
            try:
                with open(cand, encoding="utf-8") as fh:
                    return _merge_csv_text(fh.read())
            except OSError:
                log.warning("reep register CSV at %s unreadable; seed dictionary only", cand)
                return 0
    try:
        from mlb_core import storage
        key = "Soccer/reep_register_clubs.csv"
        if storage.exists(key):
            return _merge_csv_text(storage.read_bytes(key).decode("utf-8"))
    except Exception:  # noqa: BLE001 -- storage is optional; the seed is the floor
        pass
    return 0


def resolve_club(name_or_code: str | None, league: str | None = None, provider: str | None = None) -> str | None:
    """A provider's club string (or a canonical code / name) -> the canonical club code, or None.

    Exact matches only, after whitespace/case normalization: the canonical code, the canonical name, or any
    registered provider spelling. When `provider` is given its column is checked first (the same string can in
    principle belong to two clubs in two providers' spellings; the provider column disambiguates). When `league`
    is given, a resolved club that does not play in that league is still returned -- league membership is the
    caller's fixture filter, not an identity fact -- but an unknown string is always None: fail closed, never a
    fuzzy guess. Unresolved names log UNRESOLVED_SOCCER_CLUB so coverage gaps are visible, not silent.
    """
    if not name_or_code or _NOT_A_CLUB.search(str(name_or_code)):
        return None
    key = _norm(name_or_code)
    if provider and provider in PROVIDERS:
        for code, club in CLUBS.items():
            spelling = club["providers"].get(provider)
            if spelling and _norm(spelling) == key:
                return code
    hit = _INDEX.get(key)
    if hit is None:
        log.warning("UNRESOLVED_SOCCER_CLUB name=%r league=%s provider=%s", name_or_code, league, provider)
    return hit


def club_name(code: str) -> str | None:
    club = CLUBS.get(str(code).upper())
    return club["name"] if club else None


def provider_name(code: str, provider: str) -> str | None:
    club = CLUBS.get(str(code).upper())
    if not club:
        return None
    return club["providers"].get(provider) or club["name"]


def league_codes(league: str) -> frozenset:
    return frozenset(code for code, club in CLUBS.items() if league in club["leagues"])


def all_codes() -> frozenset:
    return frozenset(CLUBS)
