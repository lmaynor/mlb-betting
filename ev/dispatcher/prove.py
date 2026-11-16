"""Fire-time equivalence proof for the scheduler dispatcher (OPS-15 B.3).

For every absorbed definition this compares two independently built sets
over a window:

  ORIGINAL   the live scheduler definition's cron, expanded from the
             gcloud dump (the same dump generate_table validated against)
  DISPATCHER the minutes the dispatcher would actually emit: the grid
             scheduler fires at every (hour, grid-minute) in UTC, and the
             dispatcher job triggers an entry when its cron matches that
             minute in the entry's timezone (ev.dispatcher.run.due_entries,
             the production code path, sampled through expansion)

The sets must be byte-identical per entry. Default window is
2026-11-17..2027-02-17 UTC (92 days, no US DST transition), where cron
semantics are unambiguous. A second mode, --dst-window, expands the 2027
spring-forward week (Mar 1..15) and REPORTS any difference instead of
asserting: Cloud Scheduler's own behavior around a DST gap cannot be
observed before the fact, so that week is documented, not proven.

Usage:
    gcloud scheduler jobs list --location=us-central1 --format=json > /tmp/schedulers.json
    python -m ev.dispatcher.prove /tmp/schedulers.json
    python -m ev.dispatcher.prove /tmp/schedulers.json --dst-window

Exit code is nonzero when any in-window set differs.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ev.dispatcher.cron import Cron
from ev.dispatcher.generate_table import load_dump
from ev.dispatcher.run import load_table

PROOF_START = datetime(2026, 11, 17, tzinfo=timezone.utc)
PROOF_END = datetime(2027, 2, 17, tzinfo=timezone.utc)
DST_START = datetime(2027, 3, 1, tzinfo=timezone.utc)
DST_END = datetime(2027, 3, 15, tzinfo=timezone.utc)


def dispatcher_fires(entry: dict, grid_minutes: list[int],
                     start: datetime, end: datetime) -> list[datetime]:
    """What the dispatcher emits for one entry: grid minutes whose local
    time matches the entry cron. Iterates the grid exactly as provisioned."""
    tz = ZoneInfo(entry["timezone"])
    cron = Cron(entry["cron"])
    out: list[datetime] = []
    t = start
    one_hour = timedelta(hours=1)
    while t < end:
        for minute in grid_minutes:
            instant = t.replace(minute=minute)
            if instant < end and cron.matches(instant, tz):
                out.append(instant)
        t += one_hour
    return out


def compare(table: dict, dump: dict[str, dict], start: datetime,
            end: datetime, assert_equal: bool) -> int:
    grid = table["grid_minutes_utc"]
    total_diff = 0
    for entry in table["entries"]:
        name = entry["scheduler"]
        live = dump[name]
        tz = ZoneInfo(live.get("timeZone") or "Etc/UTC")
        original = Cron(live["schedule"]).expand(tz, start, end)
        emitted = dispatcher_fires(entry, grid, start, end)
        if original == emitted:
            print(f"EQUAL {name}: {len(original)} fires")
        else:
            only_o = sorted(set(original) - set(emitted))
            only_d = sorted(set(emitted) - set(original))
            total_diff += len(only_o) + len(only_d)
            print(f"DIFF {name}: original={len(original)} emitted={len(emitted)} "
                  f"missing={[f'{d:%m-%d %H:%M}' for d in only_o[:5]]} "
                  f"extra={[f'{d:%m-%d %H:%M}' for d in only_d[:5]]}")
    print(f"WINDOW {start:%Y-%m-%d}..{end:%Y-%m-%d} entries={len(table['entries'])} "
          f"differing_fires={total_diff}")
    return 1 if (assert_equal and total_diff) else 0


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    table = load_table()
    dump = load_dump(args[0])
    missing = [e["scheduler"] for e in table["entries"] if e["scheduler"] not in dump]
    if missing:
        print(f"dump is missing absorbed definitions: {missing}")
        return 1
    if "--dst-window" in argv:
        return compare(table, dump, DST_START, DST_END, assert_equal=False)
    return compare(table, dump, PROOF_START, PROOF_END, assert_equal=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
