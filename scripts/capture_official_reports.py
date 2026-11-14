#!/usr/bin/env python3
"""scripts/capture_official_reports.py -- scheduler-ready entrypoint for MIN-08, the
as-published capture of the official NBA injury report (nba.data.official_report_capture).

WHAT IT DOES: one capture pass over one ET day (default: today ET): every due quarter-hour
version of the day's official report that has been published and is not yet banked gets
banked (parsed CSV + raw PDF + provenance), and the day's capture ledger is updated. A pass
is idempotent and safe at any cadence; versions not yet published are simply left for the
next pass. This is the feed that makes decision-time designations (tip - 60m) reproducible
from banked data: versions land in the bank when they publish, not in a later reconstruction.

SCHEDULER-READY, NOT PROVISIONED: this script creates no Cloud Run job and no scheduler.
The intended invocation (provisioning is a separate, explicit step) is every 15 minutes
between 11:00 and 23:59 ET on game days, e.g. a Cloud Scheduler -> Cloud Run job running:

    MINUTES_RUN_ENABLED=1 python3 scripts/capture_official_reports.py --execute

Both gates are required to touch the network or storage (--execute AND the env var), the
same convention as scripts/bank_nba_designations.py: without both it prints the plan and
exits 0. Exit codes on an execute run: 0 when the pass completed (including "nothing new"),
1 when any stamp failed (it will be retried by the next pass) or verification failed.

    # capture a specific day (e.g. a late start, or the evening of a game day):
    MINUTES_RUN_ENABLED=1 python3 scripts/capture_official_reports.py --execute --date 2026-11-13
    # ... and re-verify the whole day against its banked raw PDFs afterwards:
    MINUTES_RUN_ENABLED=1 python3 scripts/capture_official_reports.py --execute --date 2026-11-13 --verify

The fetcher needs the egress proxy env on the dev VM (HTTPS_PROXY) and poppler's pdftotext;
on Cloud Run both are the image's concern (see nba/data/nba_injury_report.py).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # runnable as `python scripts/...`

from nba.config import TIMEZONE
from nba.data import official_report_capture as C

log = logging.getLogger("capture_official_reports")
_ET = ZoneInfo(TIMEZONE)
ENABLE_ENV = "MINUTES_RUN_ENABLED"


def execution_allowed(execute_flag: bool, environ) -> tuple:
    """(allowed, reason). BOTH gates are required; the reason names the missing gate."""
    if not execute_flag:
        return False, "no --execute flag"
    if environ.get(ENABLE_ENV) != "1":
        return False, f"{ENABLE_ENV} is not set to 1"
    return True, "both gates present (--execute and MINUTES_RUN_ENABLED=1)"


def resolve_day(arg_date: str, now: datetime) -> date:
    if arg_date:
        return date.fromisoformat(arg_date)
    return now.astimezone(_ET).date()


def main(argv=None, environ=None, now=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", help="ET day to capture (YYYY-MM-DD); default: today ET")
    ap.add_argument("--verify", action="store_true",
                    help="after capturing, re-parse the day's banked raw PDFs and compare "
                         "against the banked parses (exit 1 on any mismatch)")
    ap.add_argument("--delay", type=float, default=0.1, help="seconds between fetches")
    ap.add_argument("--execute", action="store_true",
                    help="actually fetch + bank. Also requires env MINUTES_RUN_ENABLED=1; "
                         "without both, plan only")
    a = ap.parse_args(argv)
    environ = os.environ if environ is None else environ
    now = now or datetime.now(_ET)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    day = resolve_day(a.date, now)
    due = C.due_stamps(day, now)
    print(f"day: {day.isoformat()} (ET) | grid stamps: {len(C.stamp_grid(day))} | "
          f"due at {now.astimezone(_ET).strftime('%H:%M ET')}: {len(due)}")

    allowed, reason = execution_allowed(a.execute, environ)
    if not allowed:
        print(f"mode: PLAN ONLY ({reason}); zero fetches, zero writes. "
              f"To execute: {ENABLE_ENV}=1 python3 scripts/capture_official_reports.py --execute")
        return 0

    print("mode: EXECUTE (both gates present)")
    res = C.capture_day(day, now=now, delay=a.delay)
    print(json.dumps({k: v for k, v in res.items() if k != "failed"}))
    for f in res["failed"]:
        print("FAILED", f)
    if res["failed"]:
        return 1
    if a.verify:
        v = C.verify_day(day)
        print(json.dumps({k: v[k] for k in ("day", "versions", "equal", "n_banked",
                                            "n_reparsed", "versions_without_raw")}))
        if not v["equal"]:
            print("VERIFY MISMATCH", json.dumps(
                {"only_in_banked": v["only_in_banked"], "only_in_reparsed": v["only_in_reparsed"]}))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
