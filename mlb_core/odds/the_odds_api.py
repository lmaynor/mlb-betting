"""
mlb_core.odds.the_odds_api — The Odds API fetchers for game-level markets.

Why this exists:
  DraftKings' direct API is blocked at the Akamai edge from datacenter IPs
  (Cloud Run, AWS, etc.) so dk_scraper.py cannot run in production. The
  Odds API is a paid aggregator that resells the same DK lines and works
  fine from any IP. HR Pro already uses it for batter_home_runs; this
  module adds the game-level markets the other systems need.

Markets exposed here:
  fetch_nrfi_odds()   — totals_1st_1_innings @ point 0.5 (NRFI = Under, YRFI = Over)
  fetch_f5_odds()     — h2h_1st_5_innings (deferred to F5 port)
  fetch_k_odds()      — pitcher_strikeouts + alternates (deferred to K port)
"""
import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Constants match run_hr.py — same key, same base.
ODDS_API_KEY  = "1ad27b4115b12e9893ffed40a7e2cd27"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Per-event request pacing to avoid 429s. The free tier appears to
# throttle bursts of >~5 calls/sec, so sleep between per-event fetches.
ODDS_API_SLEEP_SEC = 1.5


def fetch_nrfi_odds(bookmaker: str = "draftkings") -> dict:
    """
    Fetch NRFI/YRFI odds for today's MLB events from The Odds API.

    Market: totals_1st_1_innings at the 0.5 line. Under = NRFI, Over = YRFI.
    Only the 0.5 line is returned — alt lines (1.5+) are ignored because the
    model is calibrated to 0.5.

    Returns:
        Dict keyed by event_id:
            {
              event_id: {
                "away_team":   "Arizona Diamondbacks",   # Odds API full name
                "home_team":   "Miami Marlins",
                "commence_time": "2026-05-12T22:41:00Z",
                "nrfi_odds":   -135,    # American odds for Under 0.5
                "yrfi_odds":   +115,    # American odds for Over  0.5
                "point":       0.5,
                "bookmaker":   "draftkings",
              },
              ...
            }
        Games with no 0.5-line NRFI market on the requested book are skipped
        with a warning. Empty dict if API fails entirely.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": "mlb-betting/1.0"})

    # Step 1: list today's events.
    events_url = f"{ODDS_API_BASE}/sports/baseball_mlb/events"
    try:
        r = session.get(events_url, params={"apiKey": ODDS_API_KEY}, timeout=30)
        r.raise_for_status()
        events = r.json()
    except Exception as e:
        logger.error(f"NRFI odds: events list fetch failed: {e}")
        return {}

    if not events:
        logger.warning("NRFI odds: no MLB events found")
        return {}

    odds_by_event = {}
    requests_used = 0
    skipped       = 0
    last_remaining = None

    # Step 2: per-event fetch of the NRFI market.
    for idx, event in enumerate(events):
        # Pace requests after the first one to avoid 429 rate-limiting.
        if idx > 0:
            time.sleep(ODDS_API_SLEEP_SEC)

        event_id   = event["id"]
        away_team  = event["away_team"]
        home_team  = event["home_team"]
        commence   = event.get("commence_time", "")

        url = f"{ODDS_API_BASE}/sports/baseball_mlb/events/{event_id}/odds"
        params = {
            "apiKey":     ODDS_API_KEY,
            "regions":    "us",
            "markets":    "totals_1st_1_innings",
            "oddsFormat": "american",
            "bookmakers": bookmaker,
        }
        try:
            r = session.get(url, params=params, timeout=30)
            r.raise_for_status()
            requests_used += 1
            last_remaining = r.headers.get("x-requests-remaining", last_remaining)
        except Exception as e:
            logger.warning(f"NRFI odds fetch failed for {away_team}@{home_team}: {e}")
            skipped += 1
            continue

        data = r.json()
        parsed = _parse_nrfi_event(data, bookmaker)
        if parsed is None:
            skipped += 1
            continue

        parsed.update({
            "away_team":     away_team,
            "home_team":     home_team,
            "commence_time": commence,
            "bookmaker":     bookmaker,
        })
        odds_by_event[event_id] = parsed

    logger.info(
        f"NRFI odds: {len(odds_by_event)} games | "
        f"{requests_used} API calls used | {skipped} skipped | "
        f"quota remaining: {last_remaining}"
    )
    return odds_by_event


def _parse_nrfi_event(event_data: dict, bookmaker: str) -> Optional[dict]:
    """
    Extract the 0.5-line Over/Under from one /events/{id}/odds response.

    Returns dict with nrfi_odds, yrfi_odds, point — or None if the 0.5 line
    isn't offered by the requested bookmaker on this event.
    """
    for bm in event_data.get("bookmakers", []):
        if bm.get("key") != bookmaker:
            continue
        for market in bm.get("markets", []):
            if market.get("key") != "totals_1st_1_innings":
                continue
            nrfi_odds = None
            yrfi_odds = None
            for outcome in market.get("outcomes", []):
                if outcome.get("point") != 0.5:
                    continue
                name = outcome.get("name", "")
                if name == "Under":
                    nrfi_odds = outcome.get("price")
                elif name == "Over":
                    yrfi_odds = outcome.get("price")
            if nrfi_odds is not None and yrfi_odds is not None:
                return {
                    "nrfi_odds": nrfi_odds,
                    "yrfi_odds": yrfi_odds,
                    "point":     0.5,
                }
    return None
