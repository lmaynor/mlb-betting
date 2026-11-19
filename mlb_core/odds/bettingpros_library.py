"""
mlb_core.odds.bettingpros_library -- BettingPros full-catalog odds library banker.

Architecture & Decisions:
-------------------------
1. Collection Set = The Live Catalog:
   Historical crawlers relied on hand-kept market maps (e.g. SPORTS in bettingpros_props),
   which caused the BNK-13 coverage gaps whenever new markets were introduced or catalog slugs
   drifted. This library walks the live catalog (/v3/markets?sport={SPORT}) on every run,
   discovering all available markets dynamically.

2. Raw-Only Writes:
   BNK-12's canonical layer is a separate, unbuilt architectural decision. This library banks
   pure raw long-format frames partitioned as {prefix}/{slug}/{YYYY-MM-DD}.parquet. No opinionated
   book dropping or synthetic consensus calculation happens at banking time.

3. Incomplete-Is-Not-Invalid Retention:
   A quote missing an attribute is banked with NULLs, never dropped for missingness. If a book
   quotes odds without a line, or a line without odds, or a participant has no known team or
   name, the row is preserved. Only lines that are withdrawn (is_off=True) or completely void
   (both line and cost are None) or selections with no resolvable side are skipped.

4. 0-Row Markers:
   Empty days and markets with zero active offers write a 0-row parquet frame conforming to the
   market schema. This acts as an authoritative "crawled, nothing there" marker so settled dates
   are never re-asked.

5. Shared 0.8 s Limiter:
   Every outbound HTTP request across the entire run routes through a single shared SharedLimiter
   instance. No arbitrary extra sleeps or uncontrolled bursts occur between pages or sports.

6. Futures Access Pattern:
   Futures markets (outrights and season O/U props) do not accept an event_id parameter. They
   require season={SEASON} (the season's starting year, e.g. "2026"). Outright shapes carry
   placeholder line=1 quotes which are normalized to None (NaN), whereas season O/U props retain
   their real statistical lines.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

import pandas as pd

from mlb_core import storage
from mlb_core.odds import bettingpros as bp
from mlb_core.odds import bettingpros_games as bpg
from mlb_core.odds import bettingpros_props as bpp

log = logging.getLogger("bettingpros_library")

Blocked = bpg.Blocked

SPORTS = ["MLB", "NBA", "NFL", "NHL", "NCAAB", "NCAAF", "WNBA", "UFC"]

SPORT_MONTHS: dict[str, set[int]] = {
    "MLB": {2, 3, 4, 5, 6, 7, 8, 9, 10, 11},
    "NBA": {10, 11, 12, 1, 2, 3, 4, 5, 6},
    "NFL": {9, 10, 11, 12, 1, 2},
    "NHL": {10, 11, 12, 1, 2, 3, 4, 5, 6},
    "NCAAB": {11, 12, 1, 2, 3, 4},
    "NCAAF": {8, 9, 10, 11, 12, 1},
    "WNBA": {5, 6, 7, 8, 9, 10},
    "UFC": set(range(1, 13)),
}

SEASON_START_MONTH: dict[str, int] = {
    "MLB": 3,
    "NBA": 10,
    "NHL": 10,
    "NFL": 9,
    "NCAAF": 8,
    "NCAAB": 11,
    "WNBA": 5,
    "UFC": 1,
}

_CATALOG_CACHE: dict[str, list[dict[str, Any]]] = {}


def clear_catalog_cache() -> None:
    """Clear internal module catalog cache (useful for test isolation)."""
    _CATALOG_CACHE.clear()


def season_for(sport: str, on_date: date | str) -> str:
    """Return the starting year of the active season for a given sport as a string."""
    if isinstance(on_date, str):
        on_date = datetime.strptime(on_date, "%Y-%m-%d").date()
    start_month = SEASON_START_MONTH[sport]
    year = on_date.year if on_date.month >= start_month else on_date.year - 1
    return str(year)


class SharedLimiter:
    """Shared rate limiter enforcing at least min_interval seconds between successive wait() returns."""

    def __init__(
        self,
        min_interval: float = 0.8,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = float(min_interval)
        self.clock = clock
        self.sleep = sleep
        self._last_time: float | None = None

    def wait(self) -> None:
        if self._last_time is not None:
            now = self.clock()
            elapsed = now - self._last_time
            remaining = self.min_interval - elapsed
            if remaining > 0:
                self.sleep(remaining)
        self._last_time = self.clock()


def get(sess: Any, limiter: SharedLimiter, path: str, params: dict[str, Any]) -> dict[str, Any]:
    """Execute a GET request via bp.api_get after pacing through the shared limiter.

    Detects provider blocks (401, 403, Cloudflare/CAPTCHA challenges) and translates
    them to bpg.Blocked.
    """
    limiter.wait()
    try:
        return bp.api_get(sess, path, params)
    except RuntimeError as exc:
        if bpg._is_block(exc):
            raise bpg.Blocked(str(exc)[:300]) from exc
        raise


def make_session(sport: str) -> Any:
    """Create a configured BettingPros requests.Session for the specified sport."""
    return bpp.make_session(sport)


def fetch_catalog(sess: Any, limiter: SharedLimiter, sport: str) -> list[dict[str, Any]]:
    """Fetch all markets from the live catalog for a sport."""
    data = get(sess, limiter, "markets", {"sport": sport})
    markets = data.get("markets") or []
    _CATALOG_CACHE[sport] = markets
    return markets


def fetch_books(sess: Any, limiter: SharedLimiter) -> dict[str, Any]:
    """Fetch raw sportsbooks payload from the BettingPros API."""
    return get(sess, limiter, "books", {})


def route_market(market: dict[str, Any]) -> str | None:
    """Route a catalog market dictionary to its target collection category."""
    cat = market.get("category")
    if cat == "player-props":
        return "props"
    if cat in ("game-odds", "game-props"):
        return "games"
    if cat in ("player-futures", "team-futures"):
        return "futures"
    if cat == "specials":
        return "props"
    return None


def props_prefix(sport: str) -> str:
    """Storage prefix for player props and specials."""
    if sport.upper() == "MLB":
        return "Odds/bettingpros"
    return f"Odds/bettingpros_{sport.lower()}"


def games_prefix(sport: str) -> str:
    """Storage prefix for game lines and game props."""
    return f"Odds/bettingpros_{sport.lower()}_games"


def futures_prefix(sport: str) -> str:
    """Storage prefix for futures markets."""
    return f"Odds/bettingpros_{sport.lower()}_futures"


def snapshot_catalog(
    ds: str,
    catalogs: dict[str, list[dict[str, Any]]],
    books_payload: dict[str, Any],
) -> dict[str, Any]:
    """Write catalog & books snapshot to storage and diff against previous snapshot."""
    doc = {
        "date": ds,
        "books": books_payload,
        "markets": catalogs,
    }
    raw_json = json.dumps(doc, indent=2).encode("utf-8")
    storage.write_bytes(raw_json, f"Odds/bettingpros_catalog/{ds}.json")

    # Discover previous snapshots
    keys = storage.list_keys("Odds/bettingpros_catalog/")
    earlier_dates: list[str] = []
    for k in keys:
        stem = k.rsplit("/", 1)[-1]
        if stem.endswith(".json"):
            d = stem[:-5]
            if d < ds:
                earlier_dates.append(d)

    current_map: dict[tuple[str, int], str] = {}
    for sport, mkts in (catalogs or {}).items():
        for m in mkts or []:
            mid = m.get("id")
            if mid is not None:
                current_map[(sport, mid)] = m.get("slug", "")

    if not earlier_dates:
        diff = {"first_snapshot": True, "new_markets": [], "removed_markets": []}
        log.info(
            "Catalog snapshot %s: first snapshot (%d markets across %d sports)",
            ds,
            len(current_map),
            len(catalogs),
        )
        return diff

    latest_earlier = max(earlier_dates)
    try:
        prev_raw = storage.read_bytes(f"Odds/bettingpros_catalog/{latest_earlier}.json")
        prev_doc = json.loads(prev_raw.decode("utf-8"))
        prev_map: dict[tuple[str, int], str] = {}
        for sport, mkts in (prev_doc.get("markets") or {}).items():
            for m in mkts or []:
                mid = m.get("id")
                if mid is not None:
                    prev_map[(sport, mid)] = m.get("slug", "")
    except Exception as exc:
        log.warning("Could not read previous catalog snapshot %s: %s", latest_earlier, exc)
        return {"first_snapshot": True, "new_markets": [], "removed_markets": []}

    new_keys = set(current_map.keys()) - set(prev_map.keys())
    removed_keys = set(prev_map.keys()) - set(current_map.keys())

    new_markets = [
        {"sport": s, "market_id": mid, "slug": current_map[(s, mid)]}
        for s, mid in sorted(new_keys)
    ]
    removed_markets = [
        {"sport": s, "market_id": mid, "slug": prev_map[(s, mid)]}
        for s, mid in sorted(removed_keys)
    ]

    if new_markets:
        log.info("Catalog diff vs %s: %d new markets: %s", latest_earlier, len(new_markets), new_markets)
    if removed_markets:
        log.info("Catalog diff vs %s: %d removed markets: %s", latest_earlier, len(removed_markets), removed_markets)

    return {
        "first_snapshot": False,
        "new_markets": new_markets,
        "removed_markets": removed_markets,
    }


def build_library_prop_rows(
    sport: str,
    slug: str,
    market_id: int,
    ds: str,
    offers: list[dict[str, Any]],
    ev_map: dict[Any, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten player props / specials offers into long-format rows under retention rules."""
    rows: list[dict[str, Any]] = []
    for o in offers:
        parts = o.get("participants") or []
        p = parts[0] if parts else {}
        player_dict = p.get("player") or {}
        ev = ev_map.get(o.get("event_id")) or {}

        base = {
            "date": ds,
            "sport": sport,
            "market": slug,
            "market_id": market_id,
            "event_id": o.get("event_id"),
            "scheduled": ev.get("scheduled", ""),
            "season_type": ev.get("season_type", ""),
            "event_status": ev.get("status", ""),
            "away": ev.get("away", ""),
            "home": ev.get("home", ""),
            "player_id": o.get("player_id") if o.get("player_id") is not None else p.get("id"),
            "player": p.get("name") or "",
            "team": player_dict.get("team") or "",
            "position": player_dict.get("position") or "",
        }

        for sel in o.get("selections") or []:
            side = (sel.get("selection") or sel.get("label") or "").strip().lower()
            if not side:
                continue

            op = sel.get("opening_line") or {}
            if op.get("cost") is not None and op.get("line") is not None:
                bid = op.get("book_id")
                rows.append({
                    **base,
                    "line": op["line"],
                    "side": side,
                    "book_id": bid if bid is not None else -1,
                    "book": bpp.BOOK_NAMES.get(bid, f"book_{bid}"),
                    "book_kind": "open",
                    "odds": op["cost"],
                    "is_main": None,
                    "is_best": None,
                    "is_open": True,
                    "updated": op.get("created"),
                })

            for book in sel.get("books") or []:
                bid = book.get("id")
                for ln in book.get("lines") or []:
                    if ln.get("is_off"):
                        continue
                    cost = ln.get("cost")
                    line = ln.get("line")
                    # Retention rule: kept when cost is None XOR line is None; dropped if both are None
                    if cost is None and line is None:
                        continue
                    rows.append({
                        **base,
                        "line": line,
                        "side": side,
                        "book_id": bid,
                        "book": bpp.BOOK_NAMES.get(bid, f"book_{bid}"),
                        "book_kind": bpp.book_kind(bid) if bid is not None else "book",
                        "odds": cost,
                        "is_main": ln.get("main"),
                        "is_best": ln.get("best"),
                        "is_open": False,
                        "updated": ln.get("updated"),
                    })
    return rows


def build_library_game_rows(
    slug: str,
    market_id: int,
    ds: str,
    offers: list[dict[str, Any]],
    ev_map: dict[Any, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten game lines / game props offers into game-schema long rows."""
    is_moneyline = (slug == "moneyline" or slug.startswith("result-after"))
    rows: list[dict[str, Any]] = []

    for o in offers:
        ev = ev_map.get(o.get("event_id")) or {}
        ev_home = str(ev.get("home", "")).strip()
        ev_away = str(ev.get("away", "")).strip()

        base = {
            "date": ds,
            "market": slug,
            "market_id": market_id,
            "event_id": o.get("event_id"),
            "scheduled": ev.get("scheduled", ""),
            "season_type": ev.get("season_type", ""),
            "event_status": ev.get("status", ""),
            "away": ev_away,
            "home": ev_home,
        }

        for sel in o.get("selections") or []:
            part = sel.get("participant")
            part_str = str(part).strip() if part is not None else ""
            label = (sel.get("selection") or sel.get("label") or "").strip()

            if ev_home and part_str == ev_home:
                side = "home"
            elif ev_away and part_str == ev_away:
                side = "away"
            elif label:
                side = label.lower()
            elif part_str:
                side = part_str.lower()
            else:
                continue

            op = sel.get("opening_line") or {}
            if op.get("cost") is not None and (is_moneyline or op.get("line") is not None):
                bid = op.get("book_id")
                rows.append({
                    **base,
                    "side": side,
                    "line": None if is_moneyline else op.get("line"),
                    "book_id": bid if bid is not None else -1,
                    "book": bpp.BOOK_NAMES.get(bid, f"book_{bid}"),
                    "book_kind": "open",
                    "odds": op["cost"],
                    "is_main": None,
                    "is_open": True,
                    "updated": op.get("created"),
                })

            for book in sel.get("books") or []:
                bid = book.get("id")
                for ln in book.get("lines") or []:
                    if ln.get("is_off"):
                        continue
                    cost = ln.get("cost")
                    raw_line = ln.get("line")
                    if cost is None and raw_line is None:
                        continue
                    if is_moneyline and cost is None:
                        continue
                    rows.append({
                        **base,
                        "side": side,
                        "line": None if is_moneyline else raw_line,
                        "book_id": bid,
                        "book": bpp.BOOK_NAMES.get(bid, f"book_{bid}"),
                        "book_kind": bpp.book_kind(bid) if bid is not None else "book",
                        "odds": cost,
                        "is_main": ln.get("main"),
                        "is_open": False,
                        "updated": ln.get("updated"),
                    })
    return rows


def build_futures_rows(
    sport: str,
    slug: str,
    market_id: int,
    ds: str,
    offers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten futures offers (outrights or season O/U) into props-schema long rows."""
    rows: list[dict[str, Any]] = []
    base = {
        "date": ds,
        "sport": sport,
        "market": slug,
        "market_id": market_id,
        "event_id": None,
        "scheduled": "",
        "season_type": "",
        "event_status": "",
        "away": "",
        "home": "",
    }

    for o in offers:
        parts = o.get("participants") or []
        part_map = {str(p["id"]): p for p in parts if p.get("id") is not None}

        for sel in o.get("selections") or []:
            label = (sel.get("selection") or sel.get("label") or "").strip()
            if not label:
                continue
            side = label.lower()

            sel_part = sel.get("participant")
            resolved_part: dict[str, Any] | None = None
            is_outright = False

            if sel_part is not None and str(sel_part) in part_map:
                resolved_part = part_map[str(sel_part)]
                is_outright = True
            elif side in ("over", "under") and parts:
                resolved_part = parts[0]
                is_outright = False
            else:
                resolved_part = None
                is_outright = False

            player = (resolved_part.get("name") or "") if resolved_part else ""
            pid = resolved_part.get("id") if resolved_part else None
            if isinstance(pid, int):
                player_id = pid
            elif isinstance(pid, str) and pid.isdigit():
                player_id = int(pid)
            else:
                player_id = None

            if resolved_part:
                t_dict = resolved_part.get("team")
                if isinstance(t_dict, dict) and t_dict.get("abbreviation"):
                    team = t_dict["abbreviation"]
                else:
                    team = (resolved_part.get("player") or {}).get("team") or ""
                position = (resolved_part.get("player") or {}).get("position") or ""
            else:
                team = ""
                position = ""

            op = sel.get("opening_line") or {}
            if op.get("cost") is not None and (is_outright or op.get("line") is not None):
                bid = op.get("book_id")
                rows.append({
                    **base,
                    "player_id": player_id,
                    "player": player,
                    "team": team,
                    "position": position,
                    "line": None if is_outright else op.get("line"),
                    "side": side,
                    "book_id": bid if bid is not None else -1,
                    "book": bpp.BOOK_NAMES.get(bid, f"book_{bid}"),
                    "book_kind": "open",
                    "odds": op["cost"],
                    "is_main": None,
                    "is_best": None,
                    "is_open": True,
                    "updated": op.get("created"),
                })

            for book in sel.get("books") or []:
                bid = book.get("id")
                for ln in book.get("lines") or []:
                    if ln.get("is_off"):
                        continue
                    cost = ln.get("cost")
                    raw_line = ln.get("line")
                    if cost is None and raw_line is None:
                        continue
                    rows.append({
                        **base,
                        "player_id": player_id,
                        "player": player,
                        "team": team,
                        "position": position,
                        "line": None if is_outright else raw_line,
                        "side": side,
                        "book_id": bid,
                        "book": bpp.BOOK_NAMES.get(bid, f"book_{bid}"),
                        "book_kind": bpp.book_kind(bid) if bid is not None else "book",
                        "odds": cost,
                        "is_main": ln.get("main"),
                        "is_best": ln.get("best"),
                        "is_open": False,
                        "updated": ln.get("updated"),
                    })
    return rows


def fetch_events(sess: Any, limiter: SharedLimiter, sport: str, ds: str) -> dict[Any, dict[str, Any]]:
    """Fetch all events detail for sport and date through the shared limiter."""
    data = get(sess, limiter, "events", {"sport": sport, "date": ds})
    out: dict[Any, dict[str, Any]] = {}
    for e in data.get("events") or []:
        if e.get("id") is None:
            continue
        out[e["id"]] = {
            "away": e.get("visitor") or "",
            "home": e.get("home") or "",
            "scheduled": e.get("scheduled") or "",
            "season_type": e.get("season_type") or "",
            "status": e.get("status") or "",
        }
    return out


def fetch_offers_paged(
    sess: Any,
    limiter: SharedLimiter,
    sport: str,
    market_id: int,
    event_ids: list[Any],
) -> list[dict[str, Any]]:
    """Fetch paginated offers for game-day events via the shared limiter."""
    if not event_ids:
        return []
    offers: list[dict[str, Any]] = []
    page = 1
    while True:
        data = get(
            sess,
            limiter,
            "offers",
            {
                "sport": sport,
                "market_id": market_id,
                "event_id": ":".join(str(e) for e in event_ids),
                "location": "ALL",
                "limit": 10,
                "page": page,
            },
        )
        batch = data.get("offers") or []
        offers.extend(batch)
        total = (data.get("_pagination") or {}).get("total_pages") or 1
        if page >= total or not batch:
            return offers
        page += 1


def fetch_futures_offers(
    sess: Any,
    limiter: SharedLimiter,
    sport: str,
    market_id: int,
    season: str,
) -> list[dict[str, Any]]:
    """Fetch paginated offers for futures markets using the season parameter."""
    offers: list[dict[str, Any]] = []
    page = 1
    while True:
        data = get(
            sess,
            limiter,
            "offers",
            {
                "sport": sport,
                "market_id": market_id,
                "season": str(season),
                "location": "ALL",
                "limit": 10,
                "page": page,
            },
        )
        batch = data.get("offers") or []
        offers.extend(batch)
        total = (data.get("_pagination") or {}).get("total_pages") or 1
        if page >= total or not batch:
            return offers
        page += 1


def bank_day(
    sess: Any,
    limiter: SharedLimiter,
    sport: str,
    ds: str,
    today: date | str | None = None,
    catalog: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Bank daily game lines and player props for a settled day."""
    if today is None:
        today_date = date.today()
    elif isinstance(today, date):
        today_date = today
    else:
        today_date = datetime.strptime(str(today), "%Y-%m-%d").date()

    ds_date = datetime.strptime(ds, "%Y-%m-%d").date() if isinstance(ds, str) else ds

    stats: dict[str, Any] = {
        "dates": 0,
        "rows": 0,
        "empty_days": 0,
        "errors": 0,
        "markets_banked": 0,
        "blocked": False,
    }

    # Refuse non-closed days
    if ds_date >= today_date:
        stats["skipped_not_closed"] = True
        return stats

    # Skip out-of-season months without making requests
    if ds_date.month not in SPORT_MONTHS.get(sport, set()):
        stats["skipped_offseason"] = True
        return stats

    # Resolve catalog (using cache or fetching)
    if catalog is not None:
        _CATALOG_CACHE[sport] = catalog
    elif sport in _CATALOG_CACHE:
        catalog = _CATALOG_CACHE[sport]
    else:
        try:
            catalog = fetch_catalog(sess, limiter, sport)
        except bpg.Blocked:
            stats["blocked"] = True
            stats["errors"] += 1
            return stats
        except Exception as exc:
            stats["errors"] += 1
            log.exception("%s %s: failed to fetch catalog: %s", sport, ds, exc)
            return stats

    daily_markets = [m for m in catalog if route_market(m) in ("props", "games")]
    needed: list[dict[str, Any]] = []

    for m in daily_markets:
        slug = m["slug"]
        route = route_market(m)
        if route == "props":
            prefix = props_prefix(sport)
            if ds not in bpp.done_dates(prefix, slug):
                needed.append(m)
        elif route == "games":
            prefix = games_prefix(sport)
            if ds not in bpg.done_dates(prefix, slug):
                needed.append(m)

    # Idempotence: if all partitions already exist, return early without events/offers requests
    if not needed:
        log.info("%s %s: all %d daily markets already banked", sport, ds, len(daily_markets))
        return stats

    try:
        ev_map = fetch_events(sess, limiter, sport, ds)
    except bpg.Blocked:
        stats["blocked"] = True
        stats["errors"] += 1
        return stats
    except Exception as exc:
        stats["errors"] += 1
        log.exception("%s %s: error fetching events: %s", sport, ds, exc)
        return stats

    if not ev_map:
        # Write 0-row markers for every needed market
        for m in needed:
            slug = m["slug"]
            route = route_market(m)
            if route == "props":
                bpp.write_day(props_prefix(sport), slug, ds, [])
            elif route == "games":
                bpg.write_day(games_prefix(sport), slug, ds, [])
            stats["markets_banked"] += 1
        stats["empty_days"] += 1
        stats["dates"] += 1
        return stats

    event_ids = list(ev_map.keys())
    for m in needed:
        slug = m["slug"]
        mid = m["id"]
        route = route_market(m)
        try:
            offers = fetch_offers_paged(sess, limiter, sport, mid, event_ids)
            if route == "props":
                rows = build_library_prop_rows(sport, slug, mid, ds, offers, ev_map)
                bpp.write_day(props_prefix(sport), slug, ds, rows)
            elif route == "games":
                rows = build_library_game_rows(slug, mid, ds, offers, ev_map)
                bpg.write_day(games_prefix(sport), slug, ds, rows)
            stats["rows"] += len(rows)
            stats["markets_banked"] += 1
        except bpg.Blocked:
            stats["blocked"] = True
            stats["errors"] += 1
            return stats
        except Exception as exc:
            stats["errors"] += 1
            log.exception("%s %s market %s: error: %s", sport, ds, slug, exc)

    stats["dates"] += 1
    return stats


def bank_futures(sess: Any, limiter: SharedLimiter, sport: str, ds: str) -> dict[str, Any]:
    """Bank futures markets for a sport on the snapshot date ds."""
    parsed_date = datetime.strptime(ds, "%Y-%m-%d").date() if isinstance(ds, str) else ds
    season = season_for(sport, parsed_date)
    prefix = futures_prefix(sport)

    stats: dict[str, Any] = {
        "dates": 1,
        "rows": 0,
        "errors": 0,
        "markets_banked": 0,
        "blocked": False,
    }

    try:
        catalog = fetch_catalog(sess, limiter, sport)
    except bpg.Blocked:
        stats["blocked"] = True
        stats["errors"] += 1
        return stats
    except Exception as exc:
        stats["errors"] += 1
        log.exception("%s futures %s: error fetching catalog: %s", sport, ds, exc)
        return stats

    futures_markets = [m for m in catalog if route_market(m) == "futures"]
    for m in futures_markets:
        slug = m["slug"]
        mid = m["id"]
        key = f"{prefix}/{slug}/{ds}.parquet"
        if storage.exists(key):
            continue
        try:
            offers = fetch_futures_offers(sess, limiter, sport, mid, season)
            rows = build_futures_rows(sport, slug, mid, ds, offers)
            bpp.write_day(prefix, slug, ds, rows)
            stats["rows"] += len(rows)
            stats["markets_banked"] += 1
        except bpg.Blocked:
            stats["blocked"] = True
            stats["errors"] += 1
            return stats
        except Exception as exc:
            stats["errors"] += 1
            log.exception("%s futures %s market %s: error: %s", sport, ds, slug, exc)

    return stats


def run(
    date_str: str | None = None,
    sports: list[str] = SPORTS,
    futures: bool = False,
    daily: bool = True,
    delay: float = 0.8,
) -> dict[str, Any]:
    """Orchestrate the BettingPros catalog walk, daily banking, and futures snapshots."""
    today = date.today()
    today_str = today.isoformat()
    target_ds = date_str or (today - timedelta(days=1)).isoformat()

    limiter = SharedLimiter(min_interval=delay)
    sessions = {sport: make_session(sport) for sport in sports}

    # Always: fetch catalogs for ALL sports + books, snapshot catalog and log diff
    catalogs: dict[str, list[dict[str, Any]]] = {}
    for s in SPORTS:
        sess = sessions.get(s) or make_session(s)
        try:
            catalogs[s] = fetch_catalog(sess, limiter, s)
        except bpg.Blocked:
            log.error("Blocked fetching catalog for %s", s)
            return {"daily": {}, "futures": {}, "blocked": True, "errors": 1}
        except Exception as exc:
            log.exception("Error fetching catalog for %s: %s", s, exc)
            catalogs[s] = []

    first_sess = next(iter(sessions.values())) if sessions else make_session("MLB")
    books_payload: dict[str, Any] = {}
    try:
        books_payload = fetch_books(first_sess, limiter)
    except bpg.Blocked:
        log.error("Blocked fetching books")
        return {"daily": {}, "futures": {}, "blocked": True, "errors": 1}
    except Exception as exc:
        log.exception("Error fetching books: %s", exc)

    diff = snapshot_catalog(today_str, catalogs, books_payload)

    combined: dict[str, Any] = {
        "daily": {},
        "futures": {},
        "catalog_diff": diff,
        "blocked": False,
        "errors": 0,
    }

    # Daily pass
    if daily:
        for s in sports:
            sess = sessions[s]
            stats = bank_day(sess, limiter, s, target_ds, today=today)
            combined["daily"][s] = stats
            if stats.get("blocked"):
                combined["blocked"] = True
                combined["errors"] += stats.get("errors", 0)
                break
            combined["errors"] += stats.get("errors", 0)

    # Futures pass
    if futures and not combined["blocked"]:
        for s in sports:
            sess = sessions[s]
            stats = bank_futures(sess, limiter, s, today_str)
            combined["futures"][s] = stats
            if stats.get("blocked"):
                combined["blocked"] = True
                combined["errors"] += stats.get("errors", 0)
                break
            combined["errors"] += stats.get("errors", 0)

    return combined


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint mirroring bettingpros_games."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="BettingPros full-catalog odds library banker")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: yesterday for daily)")
    parser.add_argument("--sports", default=None, help="Comma-separated sports list (default: all)")
    parser.add_argument("--futures", action="store_true", help="Also run the futures pass")
    parser.add_argument("--futures-only", action="store_true", help="Only run the futures pass")
    parser.add_argument("--delay", type=float, default=0.8, help="Shared rate limiter interval (default 0.8)")

    args = parser.parse_args(argv)
    if args.sports:
        sports = [s.strip().upper() for s in args.sports.split(",") if s.strip()]
    else:
        sports = SPORTS

    if args.futures_only:
        daily = False
        futures = True
    elif args.futures:
        daily = True
        futures = True
    else:
        daily = True
        futures = False

    res = run(date_str=args.date, sports=sports, futures=futures, daily=daily, delay=args.delay)
    if res.get("blocked"):
        return 3
    if res.get("errors", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
