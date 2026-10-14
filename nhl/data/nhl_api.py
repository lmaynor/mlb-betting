"""Client for the official NHL web API (https://api-web.nhle.com/v1, no auth).

Endpoints used:
    GET schedule/{YYYY-MM-DD}          one WEEK of games starting at that date (+ nextStartDate)
    GET gamecenter/{id}/boxscore       per-player box stats + goalie splits
    GET gamecenter/{id}/play-by-play   every event (shots with x/y + strength) + rosterSpots (full names)

A 404 is treated as "no data" (returns None); 429/5xx and transient network errors retry
with exponential backoff.
"""
import logging
import time

import requests

from nhl.data.settings import NHL_API_BASE, REQUEST_DELAY_SEC, USER_AGENT

logger = logging.getLogger(__name__)

_TIMEOUT = 30
_MAX_RETRIES = 4


class NhlClient:
    def __init__(self, base: str = NHL_API_BASE, delay: float = REQUEST_DELAY_SEC, session=None):
        self.base = base.rstrip("/")
        self.delay = delay
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)

    def _get(self, path: str):
        url = f"{self.base}/{path.lstrip('/')}"
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self.session.get(url, timeout=_TIMEOUT)
                if resp.status_code == 404:
                    logger.warning("NHL API 404 (treated as empty): %s", url)
                    return None
                resp.raise_for_status()
                data = resp.json()
                if self.delay:
                    time.sleep(self.delay)
                return data
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                backoff = 2 ** attempt
                logger.warning("NHL API GET failed (%s) attempt %d/%d; retry in %ds",
                               exc, attempt + 1, _MAX_RETRIES, backoff)
                time.sleep(backoff)
        raise RuntimeError(f"NHL API GET failed after {_MAX_RETRIES} tries: {url}") from last_exc

    def get_schedule_week(self, date: str):
        """date: 'YYYY-MM-DD'. Returns the week-of-games payload (gameWeek[], nextStartDate)."""
        return self._get(f"schedule/{date}")

    def get_boxscore(self, game_id: int):
        return self._get(f"gamecenter/{game_id}/boxscore")

    def get_pbp(self, game_id: int):
        return self._get(f"gamecenter/{game_id}/play-by-play")
