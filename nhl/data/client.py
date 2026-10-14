"""Official NHL web API client (sync, unauthenticated).

Base https://api-web.nhle.com/v1 . A 404 is "no data" (None); /roster/{team}/current answers 307 (requests follows
it). Transient network/JSON errors retry with exponential backoff; a persistent failure RAISES (a silently empty
box score would look like "no games" and quietly punch a hole in the masters).
"""
import logging
import time

import requests

from nhl.config import API_BASE, REQUEST_DELAY_SEC, USER_AGENT

logger = logging.getLogger(__name__)

_TIMEOUT = 30
_MAX_RETRIES = 4


class NhlClient:
    def __init__(self, base: str = API_BASE, delay: float = REQUEST_DELAY_SEC, session=None):
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
                if resp.status_code == 429:
                    # rate limited (hit live 2026-10-14 when two jobs shared one IP): honour Retry-After, else back
                    # off hard -- the normal 1/2/4/8s ladder is too short for a throttle window.
                    try:
                        wait = float(resp.headers.get("Retry-After", ""))
                    except ValueError:
                        wait = 5.0 * (2 ** attempt)
                    logger.warning("NHL API 429 for %s; sleeping %.0fs (attempt %d/%d)", url, wait, attempt + 1, _MAX_RETRIES)
                    time.sleep(min(wait, 60.0))
                    last_exc = RuntimeError("429 Too Many Requests")
                    continue
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

    def schedule(self, day: str):
        """/schedule/{YYYY-MM-DD} -> the 7-day `gameWeek` starting at that date."""
        return self._get(f"schedule/{day}")

    def score(self, day: str):
        return self._get(f"score/{day}")

    def boxscore(self, game_id):
        return self._get(f"gamecenter/{int(game_id)}/boxscore")

    def landing(self, game_id):
        return self._get(f"gamecenter/{int(game_id)}/landing")

    def play_by_play(self, game_id):
        return self._get(f"gamecenter/{int(game_id)}/play-by-play")

    def roster(self, team_abbrev: str, season: str = "current"):
        return self._get(f"roster/{team_abbrev}/{season}")

    def standings(self, day: str = "now"):
        return self._get(f"standings/{day}")
