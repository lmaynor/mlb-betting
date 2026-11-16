"""Generate the dispatcher's checked-in fire table from live scheduler state.

Usage:
    gcloud scheduler jobs list --location=us-central1 --format=json > /tmp/schedulers.json
    python -m ev.dispatcher.generate_table /tmp/schedulers.json

Reads ev/dispatcher/absorb_decisions.json (which definitions the dispatcher
absorbs, in which cutover wave, and which tail definitions are excluded and
why) and validates every absorbed definition against the live dump:

  - the definition exists and is ENABLED
  - its target is a Cloud Run job :run endpoint (the dispatcher fires jobs
    through the Run API; it cannot replay arbitrary HTTP targets)
  - it carries no override body (absorbed definitions are plain runs; an
    override appearing later must fail generation, not be silently dropped)
  - its cron and timezone in the dump match the decision record exactly

Any drift fails generation with a nonzero exit. The output is
ev/dispatcher/table.json: the fire table the dispatcher job reads at
runtime, plus the dispatcher grid's minute union (the grid scheduler fires
the dispatcher at every minute-of-hour any absorbed entry can use; every
fleet timezone is a whole-hour offset from UTC, which generation asserts,
so local minutes equal UTC minutes).

Generation is a build/cutover-time tool. Nothing in the running dispatcher
depends on live scheduler state.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ev.dispatcher.cron import Cron

HERE = Path(__file__).resolve().parent
DECISIONS_PATH = HERE / "absorb_decisions.json"
TABLE_PATH = HERE / "table.json"

_JOB_URI = re.compile(r"/jobs/([^/:]+)(?::run)?$")


def load_dump(path: str) -> dict[str, dict]:
    raw = json.loads(Path(path).read_text())
    if isinstance(raw, dict):
        raw = raw.get("jobs", [])
    return {j["name"].split("/")[-1]: j for j in raw}


def job_name_from_uri(uri: str) -> str | None:
    m = _JOB_URI.search(uri or "")
    return m.group(1) if m else None


def build_table(dump: dict[str, dict], decisions: dict) -> dict:
    errors: list[str] = []
    entries: list[dict] = []
    for want in decisions["entries"]:
        name = want["scheduler"]
        live = dump.get(name)
        if live is None:
            errors.append(f"{name}: not present in the scheduler dump")
            continue
        if live.get("state") != "ENABLED":
            errors.append(f"{name}: state is {live.get('state')}, not ENABLED")
            continue
        target = live.get("httpTarget") or {}
        job = job_name_from_uri(target.get("uri", ""))
        if job is None:
            errors.append(f"{name}: target is not a Cloud Run job :run URI")
            continue
        if job != want["job"]:
            errors.append(f"{name}: dump target job {job} != decision {want['job']}")
            continue
        if target.get("body"):
            errors.append(f"{name}: carries an override body; dispatcher absorbs plain runs only")
            continue
        if live.get("schedule") != want["cron"]:
            errors.append(
                f"{name}: dump cron {live.get('schedule')!r} != decision {want['cron']!r}")
            continue
        if (live.get("timeZone") or "Etc/UTC") != want["timezone"]:
            errors.append(
                f"{name}: dump timezone {live.get('timeZone')!r} != decision {want['timezone']!r}")
            continue
        entries.append({
            "scheduler": name,
            "job": job,
            "cron": want["cron"],
            "timezone": want["timezone"],
            "wave": want["wave"],
            "wave_note": want["wave_note"],
        })

    if errors:
        raise SystemExit("table generation failed:\n  " + "\n  ".join(errors))

    # Grid minutes: union of every absorbed entry's minute field. Fleet
    # timezones are whole-hour offsets from UTC; assert that for a probe
    # week in both standard and daylight time so local minutes == UTC
    # minutes on the grid.
    minutes: set[int] = set()
    for e in entries:
        minutes.update(Cron(e["cron"]).minutes)
        tz = ZoneInfo(e["timezone"])
        for probe in (datetime(2027, 1, 15, tzinfo=timezone.utc),
                      datetime(2027, 7, 15, tzinfo=timezone.utc)):
            offset = probe.astimezone(tz).utcoffset() or timedelta(0)
            if offset.total_seconds() % 3600 != 0:
                raise SystemExit(
                    f"{e['scheduler']}: timezone {e['timezone']} is not a "
                    "whole-hour offset from UTC; the UTC grid cannot "
                    "reproduce its minutes exactly")

    return {
        "generated_from": decisions["source"],
        "rule": decisions["rule"],
        "grid_minutes_utc": sorted(minutes),
        "entries": sorted(entries, key=lambda e: e["scheduler"]),
        "excluded": decisions["excluded"],
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    decisions = json.loads(DECISIONS_PATH.read_text())
    table = build_table(load_dump(argv[1]), decisions)
    TABLE_PATH.write_text(json.dumps(table, indent=1) + "\n")
    print(f"wrote {TABLE_PATH} with {len(table['entries'])} entries; "
          f"grid minutes {table['grid_minutes_utc']} "
          f"({len(table['grid_minutes_utc']) * 24} grid invocations/day)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
