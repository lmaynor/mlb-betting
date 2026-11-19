"""
scripts.bp_library_gap_backfill -- OPTIONAL one-off historical gap backfill.

OPTIONAL, ONE-OFF, NOT PART OF THE DAILY JOB.
Closes the Oct 15, 2026 -> build gap in the NFL / NBA / NHL history banks (BettingPros
retains settled-day boards).

Loops closed days oldest-first, calling bettingpros_library.bank_day per sport with
one shared session per sport and one shared limiter. Idempotence comes from bank_day's
partition-exists skip: partitions that already exist are skipped immediately without
making unnecessary API requests.

Usage:
    PYTHONPATH=. python3 scripts/bp_library_gap_backfill.py --start 2026-10-15 --sports NBA,NHL,NFL
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta

from mlb_core.odds import bettingpros_library

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bp_library_gap_backfill")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-off gap backfill for BettingPros library")
    parser.add_argument(
        "--start",
        default="2026-10-15",
        help="Start date YYYY-MM-DD (default: 2026-10-15)",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="End date YYYY-MM-DD (default: yesterday)",
    )
    parser.add_argument(
        "--sports",
        default="NBA,NHL,NFL",
        help="Comma-separated sports list (default: NBA,NHL,NFL)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.8,
        help="Shared rate limiter interval seconds (default: 0.8)",
    )

    args = parser.parse_args(argv)

    sports = [s.strip().upper() for s in args.sports.split(",") if s.strip()]
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    end_ds = args.end or yesterday

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_ds, "%Y-%m-%d").date()

    if start_date > end_date:
        log.error("Start date %s is after end date %s", args.start, end_ds)
        return 1

    # Generate dates oldest-first
    cur = start_date
    dates: list[str] = []
    while cur <= end_date:
        dates.append(cur.isoformat())
        cur += timedelta(days=1)

    log.info(
        "Starting gap backfill: sports=%s, %s -> %s (%d days), delay=%.2f",
        sports,
        args.start,
        end_ds,
        len(dates),
        args.delay,
    )

    limiter = bettingpros_library.SharedLimiter(min_interval=args.delay)
    sessions = {sport: bettingpros_library.make_session(sport) for sport in sports}

    total_stats = {
        "dates": 0,
        "rows": 0,
        "empty_days": 0,
        "errors": 0,
        "markets_banked": 0,
        "blocked": False,
    }

    for idx, ds in enumerate(dates, 1):
        log.info("[%d/%d] Processing date %s", idx, len(dates), ds)
        for sport in sports:
            sess = sessions[sport]
            stats = bettingpros_library.bank_day(sess, limiter, sport, ds, today=date.today())
            if stats.get("blocked"):
                log.error("BLOCKED on %s %s. Aborting backfill.", sport, ds)
                total_stats["blocked"] = True
                total_stats["errors"] += 1
                break
            total_stats["dates"] += stats.get("dates", 0)
            total_stats["rows"] += stats.get("rows", 0)
            total_stats["empty_days"] += stats.get("empty_days", 0)
            total_stats["errors"] += stats.get("errors", 0)
            total_stats["markets_banked"] += stats.get("markets_banked", 0)

        if total_stats["blocked"]:
            break

    log.info("Backfill complete. Summary: %s", total_stats)
    print("\n=== Gap Backfill Summary ===")
    print(f"Dates processed:  {total_stats['dates']}")
    print(f"Rows banked:      {total_stats['rows']}")
    print(f"Empty days:       {total_stats['empty_days']}")
    print(f"Markets banked:   {total_stats['markets_banked']}")
    print(f"Errors:           {total_stats['errors']}")
    print(f"Blocked:          {total_stats['blocked']}")

    if total_stats["blocked"]:
        return 3
    if total_stats["errors"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
