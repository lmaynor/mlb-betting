"""The Odds API v4 client for NBA -- sync, GCS-friendly.

Ported and refit from the nba-parlay-generator reference (async/httpx/Redis ->
sync/requests, Secret Manager key, our logging conventions).

IMPORTANT: api.the-odds-api.com is blocked on the office LAN (gambling category),
so live calls only succeed from Cloud Run. Key from env THE_ODDS_API_KEY (or
ODDS_API_KEY), injected from Secret Manager `odds-api-key` in production.

Credit budget (free tier 500/month) is tracked from the x-requests-remaining /
x-requests-used response headers and logged on every call:
  - /events                     -> 1 credit (whole slate)
  - /odds  (game lines)         -> 1 credit (whole slate)
  - /events/{id}/odds (props)   -> 1 credit PER EVENT
Prefer game lines when credits are tight.
"""
import logging
import os
import time

import requests

from nba.config import ODDS_API_BASE, ODDS_REGION, ODDS_SPORT_KEY

logger = logging.getLogger(__name__)

_TIMEOUT = 30
_MAX_RETRIES = 3


class OddsApiClient:
    def __init__(self, api_key: str = None, base: str = ODDS_API_BASE,
                 session=None, delay: float = 0.0):
        self.api_key = (api_key or os.environ.get("THE_ODDS_API_KEY")
                        or os.environ.get("ODDS_API_KEY"))
        if not self.api_key:
            raise ValueError("The Odds API key required: set THE_ODDS_API_KEY "
                             "(Secret Manager odds-api-key) or pass api_key=.")
        self.base = base.rstrip("/")
        self.session = session or requests.Session()
        self.delay = delay
        self.credits_remaining = None
        self.credits_used = None

    # -- low-level ---------------------------------------------------------
    def _get(self, path: str, params: dict = None):
        url = f"{self.base}/{path.lstrip('/')}"
        params = dict(params or {})
        params["apiKey"] = self.api_key
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self.session.get(url, params=params, timeout=_TIMEOUT)
                self.credits_remaining = resp.headers.get("x-requests-remaining")
                self.credits_used = resp.headers.get("x-requests-used")
                if resp.status_code == 401:
                    logger.error("odds api 401 unauthorized -- check THE_ODDS_API_KEY")
                    return None
                if resp.status_code == 422:
                    logger.error("odds api 422 invalid params: %s", resp.text[:300])
                    return None
                if resp.status_code == 429:
                    logger.error("odds api 429 rate limited; credits_remaining=%s",
                                 self.credits_remaining)
                    return None
                resp.raise_for_status()
                data = resp.json()
                if self.delay:
                    time.sleep(self.delay)
                return data
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                backoff = 2 ** attempt
                logger.warning("odds api GET failed (%s) attempt %d/%d; retry in %ds",
                               exc, attempt + 1, _MAX_RETRIES, backoff)
                time.sleep(backoff)
        logger.error("odds api GET gave up: %s (%s)", url, last_exc)
        return None

    def _log_credits(self, what: str):
        logger.info("odds api %s -- credits remaining=%s used=%s",
                    what, self.credits_remaining, self.credits_used)

    # -- public ------------------------------------------------------------
    def get_events(self) -> list:
        """Upcoming NBA events (1 credit). Each: id, home_team, away_team,
        commence_time, sport_key."""
        data = self._get(f"sports/{ODDS_SPORT_KEY}/events", {"dateFormat": "iso"})
        self._log_credits("events")
        return data or []

    def get_game_lines(self, markets=None, books=None) -> list:
        """Game lines for the whole slate (1 credit). markets default
        h2h/spreads/totals. Returns the raw per-event list from /odds."""
        from nba.config import ODDS_GAME_MARKETS
        markets = markets or ODDS_GAME_MARKETS
        params = {
            "regions": ODDS_REGION,
            "markets": ",".join(markets),
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        if books:
            params["bookmakers"] = ",".join(books)
        data = self._get(f"sports/{ODDS_SPORT_KEY}/odds", params)
        self._log_credits("game_lines")
        return data or []

    def get_event_player_props(self, event_id: str, markets=None, books=None) -> dict:
        """Player props for ONE event (1 credit PER EVENT). Returns the raw
        single-event odds object ({id, home_team, away_team, commence_time,
        bookmakers:[...]}), or None."""
        from nba.config import ODDS_DEFAULT_BOOKS, ODDS_PROP_MARKETS
        markets = markets or list(ODDS_PROP_MARKETS.keys())
        books = books or ODDS_DEFAULT_BOOKS
        params = {
            "regions": ODDS_REGION,
            "markets": ",".join(sorted(markets)),
            "oddsFormat": "american",
            "dateFormat": "iso",
            "bookmakers": ",".join(books),
        }
        data = self._get(f"sports/{ODDS_SPORT_KEY}/events/{event_id}/odds", params)
        self._log_credits(f"props event={event_id}")
        return data
