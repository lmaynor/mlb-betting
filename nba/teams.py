"""NBA team vocabulary: ONE table bridging every id/name scheme we meet.

(code, full name, NBA team id, ESPN team id, ESPN abbrev)
  code          : the tricode used by Kalshi (ev/sports/nba.py CODES) and as the frontend logo key.
  NBA team id   : keys the official logo CDN (cdn.nba.com/logos/nba/{id}/global/L/logo.svg, verified 200 for LAL).
  ESPN id/abbr  : keys ESPN's roster API + headshots (ESPN spells some abbrevs differently: GS, NY, NO, SA, UTAH, WSH).
Names verified against ESPN /teams and a live ParlayAPI NBA slate on 2026-10-14. ParlayAPI writes "L.A. Clippers";
ESPN writes "LA Clippers" -- team_code() folds punctuation and carries aliases for both.
"""
import unicodedata

TEAMS = [
    ("ATL", "Atlanta Hawks", 1610612737, 1, "ATL"), ("BOS", "Boston Celtics", 1610612738, 2, "BOS"),
    ("BKN", "Brooklyn Nets", 1610612751, 17, "BKN"), ("CHA", "Charlotte Hornets", 1610612766, 30, "CHA"),
    ("CHI", "Chicago Bulls", 1610612741, 4, "CHI"), ("CLE", "Cleveland Cavaliers", 1610612739, 5, "CLE"),
    ("DAL", "Dallas Mavericks", 1610612742, 6, "DAL"), ("DEN", "Denver Nuggets", 1610612743, 7, "DEN"),
    ("DET", "Detroit Pistons", 1610612765, 8, "DET"), ("GSW", "Golden State Warriors", 1610612744, 9, "GS"),
    ("HOU", "Houston Rockets", 1610612745, 10, "HOU"), ("IND", "Indiana Pacers", 1610612754, 11, "IND"),
    ("LAC", "Los Angeles Clippers", 1610612746, 12, "LAC"), ("LAL", "Los Angeles Lakers", 1610612747, 13, "LAL"),
    ("MEM", "Memphis Grizzlies", 1610612763, 29, "MEM"), ("MIA", "Miami Heat", 1610612748, 14, "MIA"),
    ("MIL", "Milwaukee Bucks", 1610612749, 15, "MIL"), ("MIN", "Minnesota Timberwolves", 1610612750, 16, "MIN"),
    ("NOP", "New Orleans Pelicans", 1610612740, 3, "NO"), ("NYK", "New York Knicks", 1610612752, 18, "NY"),
    ("OKC", "Oklahoma City Thunder", 1610612760, 25, "OKC"), ("ORL", "Orlando Magic", 1610612753, 19, "ORL"),
    ("PHI", "Philadelphia 76ers", 1610612755, 20, "PHI"), ("PHX", "Phoenix Suns", 1610612756, 21, "PHX"),
    ("POR", "Portland Trail Blazers", 1610612757, 22, "POR"), ("SAC", "Sacramento Kings", 1610612758, 23, "SAC"),
    ("SAS", "San Antonio Spurs", 1610612759, 24, "SA"), ("TOR", "Toronto Raptors", 1610612761, 28, "TOR"),
    ("UTA", "Utah Jazz", 1610612762, 26, "UTAH"), ("WAS", "Washington Wizards", 1610612764, 27, "WSH"),
]

_EXTRA_ALIASES = {"la clippers": "LAC", "l a clippers": "LAC", "los angeles clippers": "LAC",
                  "la lakers": "LAL", "ny knicks": "NYK", "gs warriors": "GSW", "no pelicans": "NOP",
                  "sa spurs": "SAS", "philadelphia sixers": "PHI"}


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().replace(".", "")
    return " ".join(s.replace("-", " ").split())


_BY_NAME = {_fold(n): c for c, n, *_ in TEAMS}
_BY_NAME.update({_fold(k): v for k, v in _EXTRA_ALIASES.items()})
_BY_CODE = {c: c for c, *_ in TEAMS}
_BY_ESPN = {e: c for c, _, _, _, e in TEAMS}
NBA_ID = {c: i for c, _, i, _, _ in TEAMS}
ESPN_ID = {c: e for c, _, _, e, _ in TEAMS}
NAME_OF = {c: n for c, n, *_ in TEAMS}


def team_code(s: str):
    """Any of: tricode ('LAL'), ESPN abbrev ('GS'), full name ('L.A. Clippers', 'Golden State Warriors') -> tricode."""
    s = (s or "").strip()
    if not s:
        return None
    up = s.upper()
    if up in _BY_CODE:
        return up
    if up in _BY_ESPN:
        return _BY_ESPN[up]
    return _BY_NAME.get(_fold(s))


def logo_url(code: str) -> str:
    """Official NBA logo (SVG, transparent)."""
    return f"https://cdn.nba.com/logos/nba/{NBA_ID[code]}/global/L/logo.svg"
