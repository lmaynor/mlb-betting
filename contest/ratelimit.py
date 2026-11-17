"""A small sliding-window rate limiter for the contest API: N calls per window per (user, bucket).

In memory and per process: Cloud Run may run several instances, so the real ceiling is limit x instances. That is enough to stop a script
hammering the place route or guessing invite codes from one account; it is not a defence against a distributed attack (the contest is
private and invite-only, and the invite code itself is the gate). The clock is injectable so the tests need no sleeping.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Callable

# bucket -> (calls, window seconds). Joins are the guessable surface (an invite code), so they are the tightest.
DEFAULT_LIMITS = {"place": (20, 60), "cashout": (20, 60), "join": (6, 600), "rename": (6, 600), "read": (60, 60)}


class RateLimiter:
    def __init__(self, limits: dict[str, tuple[int, int]] | None = None, clock: Callable[[], float] = time.monotonic):
        self.limits = dict(limits or DEFAULT_LIMITS)
        self.clock = clock
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)

    def check(self, user: str, bucket: str) -> float:
        """Record one call and return 0.0 when allowed, else the seconds until a call would be allowed (nothing is recorded then)."""
        calls, window = self.limits.get(bucket, self.limits["read"])
        now = self.clock()
        q = self._hits[(user, bucket)]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= calls:
            return max(0.001, q[0] + window - now)
        q.append(now)
        return 0.0
