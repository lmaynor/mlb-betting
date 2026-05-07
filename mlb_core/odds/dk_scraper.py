"""
mlb_core.odds.dk_scraper v2 — direct HTTP, no Selenium.

Changes from v1:
  - Replaced Selenium / CDP approach with direct HTTP requests to the
    DraftKings sportscontent API endpoints.
  - fetch_dk_payloads() accepts the same arguments as before but uses
    requests instead of a headless browser.
  - All team name maps are unchanged.

DK API notes:
  - The main events endpoint is /api/odds/v1/leagues/{league_id}/events
    but the more reliable endpoint for pre-game markets is the
    sportscontent CDN used internally by their SPA.
  - We hit the documented-ish GET endpoint and parse the JSON directly.
  - If DK changes their API structure, only _fetch_market_json() needs
    updating.
"""
import json
import time
import logging
import random
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Team name maps (unchanged from v1)
# ---------------------------------------------------------------------------

DK_NAME_TO_ABBR: dict = {
    "ARI": "ARI", "ATL": "ATL", "BAL": "BAL", "BOS": "BOS",
    "CHC": "CHC", "CWS": "CWS", "CIN": "CIN", "CLE": "CLE",
    "COL": "COL", "DET": "DET", "HOU": "HOU", "KC":  "KC",
    "LAA": "LAA", "LAD": "LAD", "MIA": "MIA", "MIL": "MIL",
    "MIN": "MIN", "NYM": "NYM", "NYY": "NYY", "OAK": "OAK",
    "PHI": "PHI", "PIT": "PIT", "SD":  "SD",  "SF":  "SF",
    "SEA": "SEA", "STL": "STL", "TB":  "TB",  "TEX": "TEX",
    "TOR": "TOR", "WSH": "WSH",
    "A's": "OAK", "AZ": "ARI", "ATH": "OAK",
    "KAN": "KC",  "ROY": "KC", "WAS": "WSH", "MIN Twins": "MIN",
    "Athletics":    "OAK", "Diamondbacks": "ARI", "Braves":    "ATL",
    "Orioles":      "BAL", "Red Sox":      "BOS", "Cubs":      "CHC",
    "White Sox":    "CWS", "Reds":         "CIN", "Guardians": "CLE",
    "Rockies":      "COL", "Tigers":       "DET", "Astros":    "HOU",
    "Royals":       "KC",  "Angels":       "LAA", "Dodgers":   "LAD",
    "Marlins":      "MIA", "Brewers":      "MIL", "Twins":     "MIN",
    "Mets":         "NYM", "Yankees":      "NYY", "Phillies":  "PHI",
    "Pirates":      "PIT", "Padres":       "SD",  "Giants":    "SF",
    "Mariners":     "SEA", "Cardinals":    "STL", "Rays":      "TB",
    "Rangers":      "TEX", "Blue Jays":    "TOR", "Nationals": "WSH",
}

TEAM_NAME_TO_ABBREV: dict = {
    "Arizona Diamondbacks":  "ARI", "Atlanta Braves":       "ATL",
    "Baltimore Orioles":     "BAL", "Boston Red Sox":       "BOS",
    "Chicago Cubs":          "CHC", "Chicago White Sox":    "CWS",
    "Cincinnati Reds":       "CIN", "Cleveland Guardians":  "CLE",
    "Cleveland Indians":     "CLE", "Colorado Rockies":     "COL",
    "Detroit Tigers":        "DET", "Houston Astros":       "HOU",
    "Kansas City Royals":    "KC",  "Los Angeles Angels":   "LAA",
    "Los Angeles Dodgers":   "LAD", "Miami Marlins":        "MIA",
    "Milwaukee Brewers":     "MIL", "Minnesota Twins":      "MIN",
    "New York Mets":         "NYM", "New York Yankees":     "NYY",
    "Oakland Athletics":     "OAK", "Athletics":            "OAK",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates":   "PIT",
    "San Diego Padres":      "SD",  "San Francisco Giants": "SF",
    "Seattle Mariners":      "SEA", "St. Louis Cardinals":  "STL",
    "Tampa Bay Rays":        "TB",  "Texas Rangers":        "TEX",
    "Toronto Blue Jays":     "TOR", "Washington Nationals": "WSH",
}

# ---------------------------------------------------------------------------
# Team name resolution (unchanged from v1)
# ---------------------------------------------------------------------------

def resolve_team(name: str) -> Optional[str]:
    """Resolve any DK team name/fragment to standard 3-letter abbreviation."""
    if not name:
        return None
    if name in DK_NAME_TO_ABBR:
        return DK_NAME_TO_ABBR[name]
    for fragment, abbr in DK_NAME_TO_ABBR.items():
        if fragment and fragment in name:
            return abbr
    return None


def dk_to_int(s) -> Optional[int]:
    """Parse DK American odds string to int. Handles Unicode minus variants."""
    if not s:
        return None
    s = (
        str(s)
        .replace("\u2212", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .strip()
    )
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

_session = requests.Session()
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://sportsbook.draftkings.com/",
    "Origin":          "https://sportsbook.draftkings.com",
})

# DK MLB league id
_MLB_LEAGUE_ID = 84240

# Base URL for the DK sportscontent API
_DK_API_BASE = (
    "https://sportsbook-nash.draftkings.com/sites/US-SB/api/v5/eventgroups"
    "/{event_group_id}/categories/{category_id}/subcategories/{subcategory_id}"
    "?format=json"
)

# Fallback: simpler league-level event list
_DK_EVENTS_URL = (
    "https://sportsbook-nash.draftkings.com/sites/US-SB/api/v5/eventgroups"
    f"/{_MLB_LEAGUE_ID}?format=json"
)


def _fetch_json(url: str, retries: int = 4) -> Optional[dict]:
    """GET a URL and return parsed JSON, with exponential backoff."""
    for attempt in range(retries):
        try:
            r = _session.get(url, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            if r.status_code == 429:
                wait = (2 ** attempt) * 5 + random.uniform(1, 3)
                logger.warning(f"Rate limited (429). Waiting {wait:.1f}s")
                time.sleep(wait)
            else:
                logger.error(f"HTTP {r.status_code} for {url}: {e}")
                return None
        except Exception as e:
            wait = (2 ** attempt) + random.uniform(0.5, 1.5)
            logger.warning(f"Request error (attempt {attempt+1}): {e}. Retrying in {wait:.1f}s")
            time.sleep(wait)
    logger.error(f"All {retries} attempts failed for {url}")
    return None


def fetch_dk_payloads(
    url: str,
    wait: float = 0.0,          # kept for API compatibility, unused in HTTP mode
    extra_wait: float = 0.0,    # kept for API compatibility, unused in HTTP mode
    tab_xpath: Optional[str] = None,  # kept for API compatibility, unused in HTTP mode
) -> list:
    """
    Fetch DK market payloads for a given market URL via direct HTTP.

    The url parameter accepts the same DK sportsbook URLs used in v1
    (e.g. https://sportsbook.draftkings.com/...) — the market category
    and subcategory IDs are extracted from the URL path and used to
    call the DK sportscontent API directly.

    Falls back to the top-level league events endpoint if IDs cannot
    be extracted from the URL.

    Returns:
        List of raw payload dicts (same structure as v1).
    """
    # Try to extract category/subcategory from the URL path
    # DK URLs look like: .../baseball/mlb/{category}/{subcategory}
    import re
    cat_match = re.search(r"/(\d{5,})/categories/(\d+)/subcategories/(\d+)", url)

    if cat_match:
        event_group_id, category_id, subcategory_id = cat_match.groups()
        api_url = _DK_API_BASE.format(
            event_group_id=event_group_id,
            category_id=category_id,
            subcategory_id=subcategory_id,
        )
        payload = _fetch_json(api_url)
        if payload:
            return [payload]

    # Fallback: fetch full MLB event group
    logger.info(f"Could not extract IDs from URL, falling back to league endpoint: {url}")
    payload = _fetch_json(_DK_EVENTS_URL)
    if payload:
        return [payload]

    return []


def fetch_mlb_events() -> list[dict]:
    """
    Fetch today's MLB events from DK with moneylines.

    Returns a list of dicts:
        game_pk, away_team, home_team, away_ml, home_ml
    """
    payload = _fetch_json(_DK_EVENTS_URL)
    if not payload:
        return []

    events = []
    for event in payload.get("eventGroup", {}).get("events", []):
        teams = event.get("teamNames", [])
        if len(teams) < 2:
            continue
        away_raw, home_raw = teams[0], teams[1]
        away = resolve_team(away_raw)
        home = resolve_team(home_raw)
        if not away or not home:
            continue

        away_ml = home_ml = None
        for market in event.get("displayGroups", []):
            for offer in market.get("offers", []):
                label = offer.get("label", "").lower()
                if "moneyline" in label or "game lines" in label:
                    outcomes = offer.get("outcomes", [])
                    if len(outcomes) >= 2:
                        away_ml = dk_to_int(outcomes[0].get("oddsAmerican"))
                        home_ml = dk_to_int(outcomes[1].get("oddsAmerican"))
                    break

        events.append({
            "game_pk":  event.get("id"),
            "away_team": away,
            "home_team": home,
            "away_ml":   away_ml,
            "home_ml":   home_ml,
        })

    return events
