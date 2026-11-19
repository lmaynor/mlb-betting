"""
mlb.runners.bettingpros_library -- Cloud Run Job: BettingPros full-catalog odds library banker.

Walks the live BettingPros catalog for all supported sports, banking daily game odds and player props
into long-format Parquet partitions, and optionally taking snapshots of the futures markets.

Storage layout:
    Catalog snapshots:
        Odds/bettingpros_catalog/{YYYY-MM-DD}.json
    Daily props & specials:
        Odds/bettingpros/{market_slug}/{YYYY-MM-DD}.parquet               (MLB)
        Odds/bettingpros_{sport lower}/{market_slug}/{YYYY-MM-DD}.parquet (Others)
    Daily game lines (moneyline, spread, totals, innings):
        Odds/bettingpros_{sport lower}_games/{market_slug}/{YYYY-MM-DD}.parquet
    Futures (championship, awards, season totals):
        Odds/bettingpros_{sport lower}_futures/{market_slug}/{YYYY-MM-DD}.parquet

Env config:
    BP_MODE     daily | futures | both (default: daily)
    BP_DATE     target date YYYY-MM-DD (default: yesterday for daily, today for futures)
    BP_SPORTS   comma-separated list of sports (default: all supported)
    BP_DELAY    seconds between API calls for shared rate limiter (default: 0.8)

Exit codes:
    0: Success
    1: Errors encountered during banking
    3: Blocked by provider (401/403/CAPTCHA) - never retry
"""
from __future__ import annotations

import logging
import os
import sys

from mlb_core.odds import bettingpros_library

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("runner_bettingpros_library")


def main() -> int:
    mode = os.environ.get("BP_MODE", "daily").strip().lower()
    date_str = os.environ.get("BP_DATE", "").strip() or None
    sports_env = os.environ.get("BP_SPORTS", "").strip()
    delay_env = os.environ.get("BP_DELAY", "0.8").strip()

    try:
        delay = float(delay_env)
    except ValueError:
        delay = 0.8

    if sports_env:
        sports = [s.strip().upper() for s in sports_env.split(",") if s.strip()]
    else:
        sports = bettingpros_library.SPORTS

    if mode == "futures":
        daily = False
        futures = True
    elif mode == "both":
        daily = True
        futures = True
    else:
        daily = True
        futures = False

    log.info(
        "Starting BettingPros library runner: mode=%s date=%s sports=%s delay=%.2f",
        mode,
        date_str or "auto",
        sports,
        delay,
    )

    res = bettingpros_library.run(
        date_str=date_str,
        sports=sports,
        futures=futures,
        daily=daily,
        delay=delay,
    )

    if res.get("blocked"):
        log.error("Run ended BLOCKED by provider")
        return 3
    if res.get("errors", 0) > 0:
        log.warning("Run completed with %d errors", res["errors"])
        return 1

    log.info("Run completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
