"""
mlb.runners.bettingpros_live_props -- Cloud Run Job: LIVE BettingPros player props for one sport -> latest.json.

    python -m mlb.runners.bettingpros_live_props --sport nfl|nhl|nba [--days N] [--out PATH]

Crawls today .. today + N days (ET) of events and offers (ev.bettingpros_events.crawl_sport), keeps real two-sided
sportsbook / exchange prices only, and writes Odds/bettingpros_live/{sport}/latest.json in ParlayAPI event shape for
ev.daily_projections.load_events to union with the banked ParlayAPI lines. BettingPros is free: nothing here spends a
ParlayAPI credit. The crawl is paced (see BP_DELAY / BP_MARKET_SLEEP) and single-threaded.

A crawl that finds games but cannot price ANY of them because every market failed does NOT overwrite the last good
file, and exits 1 so the failure is visible in Cloud Run; a market that fails on its own is logged and skipped.

Config (env, all optional): BP_DELAY (0.8 s between offer pages), BP_MARKET_SLEEP (1.0 s between markets),
BP_DAY_SLEEP (0.4 s between event-day requests), BP_DAYS (override the per-sport window).

--out PATH writes the result to that local file INSTEAD of the bucket (smoke runs / probes; never touches GCS).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time

from ev import bettingpros_events as be

log = logging.getLogger("bettingpros_live_props")


def run(sport: str, days: int | None = None, out: str | None = None) -> dict:
    if os.environ.get("SKIP_STARTUP_JITTER") != "1":
        jitter = float(os.environ.get("BP_STARTUP_JITTER", "0"))
        if jitter <= 0:
            jitter = round(random.uniform(5.0, 30.0), 1)
        log.info("Anti-detection startup jitter: sleeping %.1fs before BettingPros crawl", jitter)
        time.sleep(jitter)

    result = be.crawl_sport(
        sport, days=days, delay=float(os.environ.get("BP_DELAY", "0.8")),
        market_sleep=float(os.environ.get("BP_MARKET_SLEEP", "1.0")),
        day_sleep=float(os.environ.get("BP_DAY_SLEEP", "0.4")))
    ok, why = be.publishable(result)
    c = result["counts"]
    log.info("BP_LIVE %s", json.dumps({
        "sport": sport, "publish": ok, "why": why, "events_upcoming": c.get("events_upcoming"),
        "events_priced": c.get("events_priced"), "quotes": c.get("quotes"), "books": c.get("books"),
        "requests": result["requests"], "seconds": result["seconds"], "errors": len(result["errors"]),
        "newest_quote_update": result["newest_quote_update"]}, default=str))
    result["publish"], result["publish_reason"] = ok, why
    if ok:
        if out:
            with open(out, "w") as fh:
                json.dump(result, fh, default=str)
            log.info("wrote %s (local, bucket untouched)", out)
        else:
            log.info("wrote %s", be.write_latest(result))
    else:
        log.error("NOT publishing %s: %s", sport, why)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sport", choices=be.SPORTS, required=True)
    ap.add_argument("--days", type=int, default=int(os.environ["BP_DAYS"]) if os.environ.get("BP_DAYS") else None)
    ap.add_argument("--out", default=None, help="write the result to this local file instead of the bucket")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = run(a.sport, a.days, a.out)
    except Exception:                                                                   # noqa: BLE001
        log.exception("bettingpros live crawl failed for %s", a.sport)
        return 1
    return 0 if result["publish"] else 1


if __name__ == "__main__":
    sys.exit(main())
