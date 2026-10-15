"""
Celebration GIF resolver for the nightly settlement post.

fetch_celebration_gif(bet) builds a targeted query from the slate's hero cash
(player / winning team / outcome), asks Tenor v2 (TENOR_API_KEY) or Giphy
(GIPHY_API_KEY) for the best match, and ALWAYS returns a dict:

    {"gif_url", "mp4_url", "preview_url", "query", "source"}

source is "tenor" | "giphy" | "fallback". With no key, or on any API error or
empty result, source="fallback": the curated query is kept and gif_url/mp4_url/
preview_url are "" (no media URL is invented) while "search_url" points at the
Tenor web search for that query so the operator can pick one by hand.
Callers treat an empty gif_url as "no GIF to attach".
"""
from __future__ import annotations

import os
import re
from urllib.parse import quote

import requests

TENOR_URL = "https://tenor.googleapis.com/v2/search"
GIPHY_URL = "https://api.giphy.com/v1/gifs/search"
TIMEOUT = 8

# Generic celebration queries by outcome; used when no player/team is known.
OUTCOME_QUERIES = {
    "NRFI_WIN": "baseball strikeout celebration",
    "HR_WIN": "home run celebration",
    "K_WIN": "pitcher strikeout celebration",
    "NFL_TD": "touchdown celebration",
    "NHL_GOAL": "hockey goal celebration",
    "NBA_THREE": "three pointer celebration",
    "DEFAULT": "winning celebration sports",
}

MLB_TEAMS = {
    "ARI": "Arizona Diamondbacks", "ATL": "Atlanta Braves", "BAL": "Baltimore Orioles", "BOS": "Boston Red Sox",
    "CHC": "Chicago Cubs", "CWS": "Chicago White Sox", "CIN": "Cincinnati Reds", "CLE": "Cleveland Guardians",
    "COL": "Colorado Rockies", "DET": "Detroit Tigers", "HOU": "Houston Astros", "KC": "Kansas City Royals",
    "LAA": "Los Angeles Angels", "LAD": "Los Angeles Dodgers", "MIA": "Miami Marlins", "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins", "NYM": "New York Mets", "NYY": "New York Yankees", "ATH": "Athletics",
    "PHI": "Philadelphia Phillies", "PIT": "Pittsburgh Pirates", "SD": "San Diego Padres", "SF": "San Francisco Giants",
    "SEA": "Seattle Mariners", "STL": "St. Louis Cardinals", "TB": "Tampa Bay Rays", "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays", "WSH": "Washington Nationals",
}
NFL_TEAMS = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers", "CHI": "Chicago Bears", "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys", "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars", "KC": "Kansas City Chiefs",
    "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams", "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings", "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers", "SF": "San Francisco 49ers",
    "SEA": "Seattle Seahawks", "TB": "Tampa Bay Buccaneers", "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}
NHL_TEAMS = {
    "ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres", "CGY": "Calgary Flames",
    "CAR": "Carolina Hurricanes", "CHI": "Chicago Blackhawks", "COL": "Colorado Avalanche", "CBJ": "Columbus Blue Jackets",
    "DAL": "Dallas Stars", "DET": "Detroit Red Wings", "EDM": "Edmonton Oilers", "FLA": "Florida Panthers",
    "LAK": "Los Angeles Kings", "MIN": "Minnesota Wild", "MTL": "Montreal Canadiens", "NSH": "Nashville Predators",
    "NJD": "New Jersey Devils", "NYI": "New York Islanders", "NYR": "New York Rangers", "OTT": "Ottawa Senators",
    "PHI": "Philadelphia Flyers", "PIT": "Pittsburgh Penguins", "SJS": "San Jose Sharks", "SEA": "Seattle Kraken",
    "STL": "St. Louis Blues", "TBL": "Tampa Bay Lightning", "TOR": "Toronto Maple Leafs", "UTA": "Utah Mammoth",
    "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights", "WSH": "Washington Capitals", "WPG": "Winnipeg Jets",
}
NBA_TEAMS = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets", "CHA": "Charlotte Hornets",
    "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers", "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons", "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "Los Angeles Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies", "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves", "NOP": "New Orleans Pelicans", "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder", "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs", "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz", "WAS": "Washington Wizards",
}
TEAMS = {"mlb": MLB_TEAMS, "nfl": NFL_TEAMS, "nhl": NHL_TEAMS, "nba": NBA_TEAMS}
OUTCOME_BY_SPORT = {"mlb": "HR_WIN", "nfl": "NFL_TD", "nhl": "NHL_GOAL", "nba": "NBA_THREE"}


def team_full_name(abbrev, sport="mlb"):
    """Full team name for an abbreviation (case-insensitive); None when unknown. Names pass through unchanged."""
    if not abbrev:
        return None
    a = str(abbrev).strip()
    table = TEAMS.get((sport or "mlb").lower(), MLB_TEAMS)
    if a.upper() in table:
        return table[a.upper()]
    return a if len(a) > 4 else None


def fallback_queries():
    """Every curated team celebration query, keyed '<sport>:<ABBR>' (30 MLB / 32 NFL / 32 NHL / 30 NBA)."""
    return {f"{s}:{ab}": f"{name} celebration" for s, t in TEAMS.items() for ab, name in t.items()}


def _winning_team(bet, sport):
    bt = str(bet.get("bet_type") or "").upper()
    side = "home_team" if bt in ("HOME", "GAME_HOME", "F5_HOME") else "away_team" if bt in ("AWAY", "GAME_AWAY", "F5_AWAY") else None
    return team_full_name(bet.get(side), sport) if side else None


def build_query(bet):
    """Targeted search query for a settled winning bet (player first, then winning team, then outcome)."""
    sport = str(bet.get("sport") or "mlb").lower()
    player = (bet.get("player") or "").strip()
    bt = str(bet.get("bet_type") or "").upper()
    if player:
        # the bet row carries no player->team link, so the player's own name is the query
        return f"{player} celebration"
    team = _winning_team(bet, sport)
    if team:
        return f"{team} celebration"
    if bt in ("NRFI", "YRFI"):
        return OUTCOME_QUERIES["NRFI_WIN"]
    if bt.startswith("K_") or bt.startswith("OUTS_"):
        return OUTCOME_QUERIES["K_WIN"]
    if bt == "HR":
        return OUTCOME_QUERIES["HR_WIN"]
    return OUTCOME_QUERIES.get(OUTCOME_BY_SPORT.get(sport, ""), OUTCOME_QUERIES["DEFAULT"])


def _fallback(query):
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    return {"gif_url": "", "mp4_url": "", "preview_url": "", "query": query, "source": "fallback",
            "search_url": f"https://tenor.com/search/{quote(slug)}-gifs"}


def _tenor(query, key):
    r = requests.get(TENOR_URL, params={"q": query, "key": key, "client_key": "beezy_fyi", "limit": 1,
                                        "media_filter": "gif,mp4,tinygif", "contentfilter": "medium"}, timeout=TIMEOUT)
    r.raise_for_status()
    res = (r.json().get("results") or [])
    if not res:
        return None
    m = res[0].get("media_formats") or {}
    gif = (m.get("gif") or {}).get("url", "")
    return {"gif_url": gif, "mp4_url": (m.get("mp4") or {}).get("url", ""),
            "preview_url": (m.get("tinygif") or {}).get("url", gif), "query": query, "source": "tenor"} if gif else None


def _giphy(query, key):
    r = requests.get(GIPHY_URL, params={"api_key": key, "q": query, "limit": 1, "rating": "pg-13"}, timeout=TIMEOUT)
    r.raise_for_status()
    res = (r.json().get("data") or [])
    if not res:
        return None
    im = res[0].get("images") or {}
    gif = (im.get("original") or {}).get("url", "")
    return {"gif_url": gif, "mp4_url": (im.get("original_mp4") or {}).get("mp4", ""),
            "preview_url": (im.get("fixed_width_still") or {}).get("url", gif), "query": query, "source": "giphy"} if gif else None


def fetch_celebration_gif(bet):
    """Resolve a celebration GIF for a settled winning bet. Never raises, never returns None."""
    query = build_query(bet or {})
    providers = []
    if os.environ.get("TENOR_API_KEY"):
        providers.append((_tenor, os.environ["TENOR_API_KEY"]))
    if os.environ.get("GIPHY_API_KEY"):
        providers.append((_giphy, os.environ["GIPHY_API_KEY"]))
    for fn, key in providers:
        try:
            got = fn(query, key)
        except Exception:
            got = None
        if got:
            return got
    return _fallback(query)
