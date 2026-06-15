"""SportsBlaze REST client.

Base: https://cache.sportsblaze.com  (no auth required; cache/CDN domain).
Endpoints used:
    GET /seasons/nba
    GET /teams/nba
    GET /players/nba                (current rosters only)
    GET /schedule/nba/{year}        (year = season start year)
    GET /boxscores/nba/{YYYY-MM-DD}

A 404 is treated as "no data" (returns None) rather than fatal; transient
network/JSON errors retry with exponential backoff.
"""
import logging
import time

import requests

from nba.config import LEAGUE, REQUEST_DELAY_SEC, SB_BASE

logger = logging.getLogger(__name__)

_TIMEOUT = 30
_MAX_RETRIES = 4


class SbClient:
    def __init__(self, base: str = SB_BASE, delay: float = REQUEST_DELAY_SEC, session=None):
        self.base = base.rstrip("/")
        self.delay = delay
        self.session = session or requests.Session()

    def _get(self, path: str):
        url = f"{self.base}/{path.lstrip('/')}"
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self.session.get(url, timeout=_TIMEOUT)
                if resp.status_code == 404:
                    logger.warning("SportsBlaze 404 (treated as empty): %s", url)
                    return None
                resp.raise_for_status()
                data = resp.json()
                if self.delay:
                    time.sleep(self.delay)
                return data
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                backoff = 2 ** attempt
                logger.warning("SportsBlaze GET failed (%s) attempt %d/%d; retry in %ds",
                               exc, attempt + 1, _MAX_RETRIES, backoff)
                time.sleep(backoff)
        raise RuntimeError(f"SportsBlaze GET failed after {_MAX_RETRIES} tries: {url}") from last_exc

    def get_seasons(self):
        return self._get(f"seasons/{LEAGUE}")

    def get_teams(self):
        return self._get(f"teams/{LEAGUE}")

    def get_players(self):
        return self._get(f"players/{LEAGUE}")

    def get_schedule(self, year: int):
        return self._get(f"schedule/{LEAGUE}/{year}")

    def get_boxscores(self, date: str):
        """date: 'YYYY-MM-DD'. Returns the response dict (events may be empty)
        or None on 404."""
        return self._get(f"boxscores/{LEAGUE}/{date}")
