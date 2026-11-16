"""Scheduler dispatcher: one small job that fires the low-frequency tail.

OPS-15 Tranche 2 (memo gcp-deep-efficiency-2026-11-16.md, section B.3).
Dozens of Cloud Scheduler definitions that fire at most twice a day each
bill $0.10/month of definition rent and exist only to POST a Cloud Run
job's :run endpoint at a fixed minute. This dispatcher replaces them:

  - a grid scheduler fires this job at every minute-of-hour any absorbed
    entry can use (see table.json, grid_minutes_utc), every hour, in UTC;
  - the job reads the checked-in fire table (ev/dispatcher/table.json),
    finds the entries whose original cron matches the current minute in
    the entry's own timezone, and runs their jobs through the Run API;
  - a daily audit run reconciles yesterday's intended fires against the
    jobs' actual executions and logs a loud AUDIT MISS line for any gap,
    so a silent dispatcher failure cannot hide (the health-check pattern
    the rest of the fleet uses).

Absorbed definitions are plain runs with no override body; table
generation (ev.dispatcher.generate_table) fails if that ever stops being
true. The dispatcher is stateless, and firing is idempotent within a
minute: before running a job it checks whether an execution was already
created in that minute (a replayed grid firing, or the original scheduler
definition during the cutover overlap) and skips if so.

Modes:
    python -m ev.dispatcher.run              fire entries due this minute
    python -m ev.dispatcher.run --dry-run    log intended fires only
    python -m ev.dispatcher.run --at ISO     evaluate at an instant
    python -m ev.dispatcher.run --simulate YYYY-MM-DD [--tz ZONE]
        dry-run every minute of one local day; prints SIM FIRE lines
    python -m ev.dispatcher.run --audit      reconcile yesterday (America/Chicago)

Environment: GCP_PROJECT (default concrete-crow-445205-m4), GCP_REGION
(default us-central1), DISPATCHER_TABLE (override table path),
DISPATCHER_DRY_RUN=1 forces dry-run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ev.dispatcher.cron import Cron

HERE = Path(__file__).resolve().parent
DEFAULT_TABLE = HERE / "table.json"
AUDIT_TZ = ZoneInfo("America/Chicago")
MATCH_TOLERANCE = timedelta(minutes=4)


def load_table(path: str | Path | None = None) -> dict:
    p = Path(path or os.environ.get("DISPATCHER_TABLE") or DEFAULT_TABLE)
    return json.loads(p.read_text())


def due_entries(table: dict, instant_utc: datetime) -> list[dict]:
    """Entries whose original cron fires at this UTC minute."""
    instant_utc = instant_utc.replace(second=0, microsecond=0)
    due = []
    for entry in table["entries"]:
        tz = ZoneInfo(entry["timezone"])
        if Cron(entry["cron"]).matches(instant_utc, tz):
            due.append(entry)
    return due


def intended_fires(table: dict, start_utc: datetime,
                   end_utc: datetime) -> list[tuple[datetime, dict]]:
    """Every (instant, entry) the table intends to fire in [start, end)."""
    out: list[tuple[datetime, dict]] = []
    for entry in table["entries"]:
        tz = ZoneInfo(entry["timezone"])
        for fire in Cron(entry["cron"]).expand(tz, start_utc, end_utc):
            out.append((fire, entry))
    return sorted(out, key=lambda x: (x[0], x[1]["scheduler"]))


class RunApi:
    """Minimal Cloud Run Admin API client (jobs :run + executions list)."""

    def __init__(self, project: str, region: str, session=None):
        self.parent = f"projects/{project}/locations/{region}"
        self.session = session or self._authorized_session()

    @staticmethod
    def _authorized_session():
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"])
        return AuthorizedSession(credentials)

    def run_job(self, job: str) -> tuple[bool, str]:
        url = f"https://run.googleapis.com/v2/{self.parent}/jobs/{job}:run"
        last = ""
        for attempt, backoff in enumerate((0, 2, 5)):
            if backoff:
                time.sleep(backoff)
            resp = self.session.post(url, json={})
            if resp.status_code < 300:
                return True, f"http {resp.status_code}"
            last = f"http {resp.status_code}: {resp.text[:200]}"
            if resp.status_code not in (429, 500, 502, 503, 504):
                break
        return False, last

    def list_executions(self, job: str, start_utc: datetime,
                        end_utc: datetime) -> list[datetime]:
        """Execution create-times for one job inside the window."""
        url = f"https://run.googleapis.com/v2/{self.parent}/jobs/{job}/executions"
        found: list[datetime] = []
        params: dict[str, str] = {"pageSize": "100"}
        while True:
            resp = self.session.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            for ex in payload.get("executions", []):
                created = _parse_ts(ex.get("createTime"))
                if created and start_utc <= created < end_utc:
                    found.append(created)
            token = payload.get("nextPageToken")
            if not token:
                return sorted(found)
            params["pageToken"] = token


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fire_due(table: dict, instant_utc: datetime, api: RunApi | None,
             dry_run: bool) -> int:
    due = due_entries(table, instant_utc)
    failed = 0
    for entry in due:
        stamp = instant_utc.strftime("%Y-%m-%dT%H:%MZ")
        if dry_run:
            print(f"DISPATCH DRY {stamp} scheduler={entry['scheduler']} "
                  f"job={entry['job']}")
            continue
        assert api is not None
        # Idempotence guard: if the job already has an execution created
        # in this minute (a replayed grid firing, or the original
        # scheduler definition during the cutover overlap), do not fire a
        # second one. Table generation's proof window contains no
        # same-job/same-minute collisions between distinct entries, so
        # this never suppresses a legitimately distinct fire.
        existing = api.list_executions(entry["job"], instant_utc,
                                       instant_utc + timedelta(minutes=1))
        if existing:
            print(f"DISPATCH SKIP {stamp} scheduler={entry['scheduler']} "
                  f"job={entry['job']} (execution already created this minute)")
            continue
        ok, detail = api.run_job(entry["job"])
        if ok:
            print(f"DISPATCH FIRE {stamp} scheduler={entry['scheduler']} "
                  f"job={entry['job']} ({detail})")
        else:
            failed += 1
            print(f"DISPATCH FAIL {stamp} scheduler={entry['scheduler']} "
                  f"job={entry['job']} ({detail})")
    print(f"DISPATCH DONE at={instant_utc:%Y-%m-%dT%H:%MZ} due={len(due)} "
          f"failed={failed} dry_run={dry_run}")
    return 1 if failed else 0


def audit(table: dict, api: RunApi, day_local: datetime) -> int:
    """Reconcile one local day's intended fires against real executions."""
    start_local = day_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = (start_local + timedelta(days=1)).astimezone(timezone.utc)
    intended = intended_fires(table, start_utc, end_utc)
    executions: dict[str, list[datetime]] = {}
    for job in sorted({e["job"] for _, e in intended}):
        executions[job] = api.list_executions(job, start_utc,
                                              end_utc + MATCH_TOLERANCE)
    misses = 0
    per_entry: dict[str, list[datetime]] = {}
    for fire, entry in intended:
        per_entry.setdefault(entry["scheduler"], []).append(fire)
    for scheduler, fires in sorted(per_entry.items()):
        job = next(e["job"] for _, e in intended if e["scheduler"] == scheduler)
        starts = executions.get(job, [])
        missing = [f for f in fires
                   if not any(f <= s < f + MATCH_TOLERANCE for s in starts)]
        if missing:
            misses += len(missing)
            for f in missing:
                print(f"AUDIT MISS scheduler={scheduler} job={job} "
                      f"intended={f:%Y-%m-%dT%H:%MZ} no execution started "
                      f"within {int(MATCH_TOLERANCE.total_seconds() // 60)}m")
        else:
            print(f"AUDIT OK scheduler={scheduler} job={job} "
                  f"fires={len(fires)}")
    print(f"AUDIT DONE day={start_local.date()} intended={len(intended)} "
          f"misses={misses}")
    return 0


def simulate(table: dict, day: str, tz_name: str) -> int:
    tz = ZoneInfo(tz_name)
    start_local = datetime.fromisoformat(day).replace(tzinfo=tz)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = (start_local + timedelta(days=1)).astimezone(timezone.utc)
    for fire, entry in intended_fires(table, start_utc, end_utc):
        print(f"SIM FIRE {fire:%Y-%m-%dT%H:%MZ} scheduler={entry['scheduler']} "
              f"job={entry['job']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ev.dispatcher.run")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--at", help="ISO instant to evaluate (default: now)")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--simulate", metavar="YYYY-MM-DD")
    parser.add_argument("--tz", default="America/Chicago",
                        help="local zone for --simulate (default America/Chicago)")
    args = parser.parse_args(argv)

    table = load_table()
    dry_run = args.dry_run or os.environ.get("DISPATCHER_DRY_RUN") == "1"

    if args.simulate:
        return simulate(table, args.simulate, args.tz)

    api = None
    if args.audit or not dry_run:
        api = RunApi(os.environ.get("GCP_PROJECT", "concrete-crow-445205-m4"),
                     os.environ.get("GCP_REGION", "us-central1"))

    if args.audit:
        yesterday = datetime.now(AUDIT_TZ) - timedelta(days=1)
        return audit(table, api, yesterday)

    if args.at:
        instant = _parse_ts(args.at)
        assert instant is not None
    else:
        instant = datetime.now(timezone.utc)
    return fire_due(table, instant, api, dry_run)


if __name__ == "__main__":
    sys.exit(main())
