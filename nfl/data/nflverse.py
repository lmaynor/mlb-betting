"""nflverse release-asset client.

Downloads one parquet asset from a nflverse-data GitHub release. No auth, no API
quota (release downloads are plain HTTPS). Mirrors nba.data.sportsblaze.SbClient's
contract: a 404 is "not published (yet)" and returns None rather than raising;
transient network / 5xx / rate-limit responses retry with exponential backoff and
then raise, so one bad file fails loudly instead of being silently skipped.
"""
import logging
import time
from typing import Optional

import requests

from nfl.config import MAX_ASSET_BYTES, REQUEST_DELAY_SEC, USER_AGENT

logger = logging.getLogger(__name__)

_TIMEOUT = 120          # a play-by-play season is tens of MB
_MAX_RETRIES = 4
_RETRY_STATUS = {403, 429, 500, 502, 503, 504}   # 403/429 = GitHub secondary rate limiting


class NflverseError(RuntimeError):
    """A download failed after retries (distinct from 'not published', which is None)."""


class NflverseClient:
    def __init__(self, delay: float = REQUEST_DELAY_SEC, session=None, max_bytes: int = MAX_ASSET_BYTES):
        self.delay = delay
        self.max_bytes = max_bytes
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/octet-stream, */*"})

    def head_size(self, url: str) -> Optional[int]:
        """Content-Length of an asset (None if unpublished or not reported). Used by --probe."""
        resp = self.session.head(url, timeout=_TIMEOUT, allow_redirects=True)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        n = resp.headers.get("Content-Length")
        return int(n) if n and n.isdigit() else None

    def get_bytes(self, url: str) -> Optional[bytes]:
        """Return the asset bytes, or None if it is not published (HTTP 404)."""
        last_exc: Optional[BaseException] = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self.session.get(url, timeout=_TIMEOUT)
                if resp.status_code == 404:
                    logger.info("nflverse 404 (not published): %s", url)
                    return None
                if resp.status_code in _RETRY_STATUS:
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                resp.raise_for_status()
                data = resp.content
                if len(data) > self.max_bytes:
                    raise NflverseError(f"asset is {len(data)} bytes (> cap {self.max_bytes}): {url}")
                if self.delay:
                    time.sleep(self.delay)
                return data
            except NflverseError:
                raise
            except requests.RequestException as exc:
                last_exc = exc
                backoff = 2 ** attempt
                logger.warning("nflverse GET failed (%s) attempt %d/%d; retry in %ds",
                               exc, attempt + 1, _MAX_RETRIES, backoff)
                time.sleep(backoff)
        raise NflverseError(f"nflverse GET failed after {_MAX_RETRIES} tries: {url}") from last_exc
