"""
mlb.runners.bestbook_live_props -- Cloud Run Job: LIVE BestBook player props for one sport -> latest.json.

    python -m mlb.runners.bestbook_live_props --sport nfl|nhl|nba [--days N] [--out PATH]

Crawls today .. today + N days of events and offers (ev.bestbook_events.crawl_sport), keeps real two-sided
sportsbook / exchange prices only, and writes Odds/bestbook_live/{sport}/latest.json in ParlayAPI event shape for
ev.daily_projections.load_events to union with the banked ParlayAPI and BettingPros lines.

A crawl that finds games but cannot price ANY of them does NOT overwrite the last good
file, and exits 1 so the failure is visible in Cloud Run.

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

from ev import bestbook_events as bbe

log = logging.getLogger("bestbook_live_props")


def run(sport: str, days: int | None = None, out: str | None = None) -> dict:
    if os.environ.get("SKIP_STARTUP_JITTER") != "1":
        jitter = float(os.environ.get("BB_STARTUP_JITTER", "0"))
        if jitter <= 0:
            jitter = round(random.uniform(5.0, 30.0), 1)
        log.info("Anti-detection startup jitter: sleeping %.1fs before BestBook crawl", jitter)
        time.sleep(jitter)

    result = bbe.crawl_sport(sport, days=days)
    ok, why = bbe.publishable(result)
    c = result["counts"]
    log.info(
        "BESTBOOK_LIVE %s",
        json.dumps({
            "sport": sport,
            "publish": ok,
            "why": why,
            "events_upcoming": c.get("events_upcoming"),
            "events_priced": c.get("events_priced"),
            "quotes": c.get("quotes"),
            "books": c.get("books"),
            "seconds": result["seconds"],
        }, default=str),
    )
    result["publish"], result["publish_reason"] = ok, why
    if ok:
        if out:
            with open(out, "w") as fh:
                json.dump(result, fh, default=str)
            log.info("wrote %s (local, bucket untouched)", out)
        else:
            log.info("wrote %s", bbe.write_latest(result))
    else:
        log.error("NOT publishing %s: %s", sport, why)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sport", choices=bbe.SPORTS, required=True)
    ap.add_argument("--days", type=int, default=int(os.environ["BB_DAYS"]) if os.environ.get("BB_DAYS") else None)
    ap.add_argument("--out", default=None, help="write the result to this local file instead of the bucket")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = run(a.sport, a.days, a.out)
    except Exception:  # noqa: BLE001
        log.exception("bestbook live crawl failed for %s", a.sport)
        return 1
    return 0 if result["publish"] else 1


if __name__ == "__main__":
    sys.exit(main())
