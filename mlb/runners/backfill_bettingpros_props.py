"""
mlb.runners.backfill_bettingpros_props -- Cloud Run Job: SLOW crawl of BettingPros player-prop history (NFL/NHL/NBA).

Long format, one parquet per (market, date), every book kept (see mlb_core.odds.bettingpros_props):

    {BP_PREFIX}/{market_slug}/{YYYY-MM-DD}.parquet        default prefix Odds/bettingpros_{sport lower}

Why slow: the offers endpoint caps a page at 10 offers, so one NHL game day is ~100-150 requests across the markets.
The crawl is paced (BP_DELAY between pages, BP_MARKET_SLEEP between markets, BP_DATE_SLEEP between dates, all
jittered) and runs NEWEST FIRST so the most valuable seasons (Novig/ProphetX only appear from 2025) land first.
Resume is exact: a (market, date) partition that exists -- including a 0-row "nothing there" marker -- is skipped, so
re-executing after a timeout/retry/limit just continues. Today's partition is never written (prices not closed).

Config (env):
    BP_SPORT          NFL | NHL | NBA                    (required)
    BP_START          default: the sport's first useful date (bettingpros_props.SPORT_START)
    BP_END            default: yesterday (UTC)
    BP_MARKETS        "all" or comma list of stat labels / slugs (e.g. "sog,goals")
    BP_PREFIX         default Odds/bettingpros_{sport lower}
    BP_DELAY          0.8   seconds between offer pages
    BP_MARKET_SLEEP   1.5   seconds between markets
    BP_DATE_SLEEP     3.0   seconds between dates
    BP_MAX_DATES      0     stop after this many NEW dates (0 = no limit; use for a smoke run)
    BP_OLDEST_FIRST   0     set 1 to crawl oldest first

Local smoke run (writes under MLB_BASE_DATA when no bucket env is set):
    BP_SPORT=NHL BP_START=2025-12-10 BP_END=2025-12-10 BP_MARKETS=sog PYTHONPATH=. python3 -m mlb.runners.backfill_bettingpros_props
"""
from __future__ import annotations

import logging
import os
import random
import time
from datetime import date, timedelta

from mlb_core.odds import bettingpros_props as bpp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_bettingpros_props")


def _sleep(base: float) -> None:
    time.sleep(base * random.uniform(0.6, 1.4))


def crawl(sport: str, start: str, end: str, wanted: list[str] | None, prefix: str, delay: float, market_sleep: float,
          date_sleep: float, max_dates: int, newest_first: bool, sess=None) -> dict:
    sess = sess or bpp.make_session(sport)
    markets = bpp.resolve_markets(sess, sport, wanted)
    if not markets:
        raise SystemExit(f"no configured markets resolved for {sport} (wanted={wanted})")
    log.info("%s markets resolved live: %s", sport, {s: m["market_id"] for s, m in markets.items()})
    done = {m["slug"]: bpp.done_dates(prefix, m["slug"]) for m in markets.values()}
    dates = [d for d in bpp.crawl_dates(sport, start, end, newest_first) if bpp.is_closed_day(d)]
    log.info("crawl %s %s -> %s: %d candidate dates x %d markets -> %s", sport, start, end, len(dates), len(markets),
             prefix)
    stats = {"dates": 0, "rows": 0, "errors": 0, "empty_days": 0}
    for ds in dates:
        need = {s: m for s, m in markets.items() if ds not in done[m["slug"]]}
        if not need:
            continue
        if max_dates and stats["dates"] >= max_dates:
            log.info("BP_MAX_DATES=%d reached", max_dates)
            break
        try:
            ev_map = bpp.fetch_events_detail(sess, sport, ds)
            day_rows = 0
            if not ev_map:
                # no games that day: mark every market done so the day is never re-asked
                for m in need.values():
                    bpp.write_day(prefix, m["slug"], ds, [])
                stats["empty_days"] += 1
                log.info("%s: no events", ds)
                _sleep(date_sleep / 3)
                continue
            for stat, m in need.items():
                offers = bpp.fetch_offers_paged(sess, sport, m["market_id"], list(ev_map), delay)
                rows = bpp.build_prop_rows(sport, m["slug"], m["market_id"], ds, offers, ev_map)
                bpp.write_day(prefix, m["slug"], ds, rows)   # 0 rows = "nothing there" marker
                day_rows += len(rows)
                _sleep(market_sleep)
            stats["dates"] += 1
            stats["rows"] += day_rows
            log.info("%s: %d events, %d rows across %d markets", ds, len(ev_map), day_rows, len(need))
        except Exception as exc:  # noqa: BLE001 -- keep crawling; partitions already written stay (resume)
            stats["errors"] += 1
            log.exception("%s: ERROR %s", ds, exc)
        _sleep(date_sleep)
    log.info("DONE %s", stats)
    return stats


def main() -> int:
    sport = os.environ.get("BP_SPORT", "").upper()
    if sport not in bpp.SPORTS:
        raise SystemExit(f"BP_SPORT must be one of {sorted(bpp.SPORTS)}")
    start = os.environ.get("BP_START") or bpp.SPORT_START[sport]
    end = os.environ.get("BP_END") or (date.today() - timedelta(days=1)).isoformat()
    wanted_arg = os.environ.get("BP_MARKETS", "all").strip()
    wanted = None if wanted_arg in ("", "all") else [w.strip() for w in wanted_arg.split(",") if w.strip()]
    stats = crawl(
        sport, start, end, wanted,
        prefix=os.environ.get("BP_PREFIX", bpp.default_prefix(sport)).rstrip("/"),
        delay=float(os.environ.get("BP_DELAY", "0.8")),
        market_sleep=float(os.environ.get("BP_MARKET_SLEEP", "1.5")),
        date_sleep=float(os.environ.get("BP_DATE_SLEEP", "3.0")),
        max_dates=int(os.environ.get("BP_MAX_DATES", "0")),
        newest_first=os.environ.get("BP_OLDEST_FIRST", "0") != "1")
    # non-zero on errors so a Cloud Run Job retry resumes (idempotent)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
