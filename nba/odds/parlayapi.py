"""ParlayAPI client (parlay-api.com) -- sync, sport-agnostic.

Chosen live-odds provider for the NBA expansion (and usable for live MLB). Same
response shape as The Odds API EXCEPT prop outcome `name` is "Over <player>" /
"Under <player>" with `description` = the player name. We request
oddsFormat=american so the existing american best-book logic applies.

Auth: header `X-API-Key`; key from env PARLAY_API_KEY (Secret Manager
parlay-api-key). Reachable from Cloud Run only (office LAN blocks gambling sites).

Billing (free tier 1000 credits/mo, $5 = 20k):
  - /sports/{sport}/odds            game lines -- 1 credit PER MARKET (whole slate)
  - /sports/{sport}/events/{id}/odds props      -- 1 credit per (event x market)
Credit usage is read from x-requests-last / x-requests-remaining headers and logged.
"""
import logging
import os
import time

import requests

from nba.config import PARLAY_API_BASE, PARLAY_REGION

logger = logging.getLogger(__name__)

_TIMEOUT = 30
_MAX_RETRIES = 3


class ParlayApiClient:
    def __init__(self, api_key: str = None, base: str = PARLAY_API_BASE,
                 session=None, delay: float = 0.0):
        self.api_key = api_key or os.environ.get("PARLAY_API_KEY")
        if not self.api_key:
            raise ValueError("ParlayAPI key required: set PARLAY_API_KEY "
                             "(Secret Manager parlay-api-key) or pass api_key=.")
        self.base = base.rstrip("/")
        self.session = session or requests.Session()
        self.delay = delay
        self.credits_remaining = None
        self.credits_last = None

    def _get(self, path: str, params: dict = None):
        url = f"{self.base}/{path.lstrip('/')}"
        headers = {"X-API-Key": self.api_key}
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self.session.get(url, params=params or {}, headers=headers, timeout=_TIMEOUT)
                self.credits_remaining = resp.headers.get("x-requests-remaining")
                self.credits_last = resp.headers.get("x-requests-last")
                if resp.status_code in (401, 403):
                    logger.error("parlayapi auth error %s -- check PARLAY_API_KEY", resp.status_code)
                    return None
                if resp.status_code == 404:
                    return None
                if resp.status_code == 429:
                    logger.error("parlayapi 429 rate limited; remaining=%s", self.credits_remaining)
                    return None
                resp.raise_for_status()
                data = resp.json()
                if self.delay:
                    time.sleep(self.delay)
                return data
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                backoff = 2 ** attempt
                logger.warning("parlayapi GET failed (%s) attempt %d/%d; retry %ds",
                               exc, attempt + 1, _MAX_RETRIES, backoff)
                time.sleep(backoff)
        logger.error("parlayapi GET gave up: %s (%s)", url, last_exc)
        return None

    def _log_credits(self, what):
        logger.info("parlayapi %s -- credits last=%s remaining=%s",
                    what, self.credits_last, self.credits_remaining)

    def get_slate(self, sport: str, markets=None) -> list:
        """Game lines for the whole slate; also the event-discovery call.
        1 credit per market. Returns the raw event list (each has `id`)."""
        from nba.config import PARLAY_GAME_MARKETS
        markets = markets or ["h2h"]
        if markets == "game":
            markets = PARLAY_GAME_MARKETS
        params = {"regions": PARLAY_REGION, "markets": ",".join(markets),
                  "oddsFormat": "american"}
        data = self._get(f"sports/{sport}/odds", params)
        self._log_credits(f"slate {sport} [{','.join(markets)}]")
        return data or []

    def get_event_props(self, sport: str, event_id: str, markets) -> dict:
        """Player props for ONE event. 1 credit per (event x market).
        Returns the raw single-event odds object, or None."""
        params = {"regions": PARLAY_REGION, "markets": ",".join(markets),
                  "oddsFormat": "american"}
        data = self._get(f"sports/{sport}/events/{event_id}/odds", params)
        self._log_credits(f"props {sport} event={event_id} [{len(markets)} mkts]")
        return data
