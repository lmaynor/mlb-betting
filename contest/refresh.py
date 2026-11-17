"""The scheduled pass: fetch ESPN's scoreboard for a window of days and apply it to a contest (contest.sync). This is the only place
the contest talks to ESPN. One polite request per day in the window, each cached for nothing (the scheduler spaces the passes).

ESPN is an unofficial, undocumented feed (CONTEXT.md section 28 records the terms question): this is a private, invite-only,
play-money contest, requests identify themselves, and a failed day is skipped rather than retried hard.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Callable

from contest import store
from contest.espn import parse_event
from contest.sync import sync_events

ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
PATHS = {"ncaaf": ("football/college-football", "80"), "ncaab": ("basketball/mens-college-basketball", "50")}
UA = "beezy-contest/1.0 (private play-money contest)"


def http_json(url: str, params: dict | None = None, attempts: int = 3) -> dict:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for i in range(attempts):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30))
        except (urllib.error.HTTPError, urllib.error.URLError):
            if i == attempts - 1:
                raise
            time.sleep(2 ** (i + 1))
    raise RuntimeError("unreachable")


def fetch_events(sport: str, start: datetime, end: datetime, http: Callable = http_json) -> list[dict]:
    """Every scoreboard event from `start` to `end` (UTC dates, inclusive), parsed. A day ESPN will not serve is skipped."""
    path, groups = PATHS[sport]
    out: dict[str, dict] = {}
    day = start.date()
    while day <= end.date():
        try:
            for e in (http(ESPN.format(path=path), {"dates": day.strftime("%Y%m%d"), "groups": groups, "limit": 400}).get("events") or []):
                ev = parse_event(e)
                if ev:
                    out[ev["espn_id"]] = ev
        except Exception:                                          # noqa: BLE001 - one dead day must not stop the pass
            pass
        day += timedelta(days=1)
    return list(out.values())


def refresh(engine, slug: str, now: datetime, include: str | None = None, days_back: int = 2, days_ahead: int = 14, http: Callable = http_json) -> dict:
    """Fetch the window [now - days_back, now + days_ahead] and sync it. `include` is an optional regex on the round label
    (e.g. "Bowl|Playoff|Championship") that keeps the contest to the games that belong in it."""
    c = store.contest_by_slug(engine, slug)
    if c is None:
        raise ValueError(f"no contest {slug!r}")
    events = fetch_events(c["rules"].sport, now - timedelta(days=days_back), now + timedelta(days=days_ahead), http)
    rx = re.compile(include, re.I) if include else None
    res = sync_events(engine, c["id"], events, now, include=(lambda e: bool(rx.search(e["round"]))) if rx else (lambda e: True))
    res["fetched"] = len(events)
    return res
