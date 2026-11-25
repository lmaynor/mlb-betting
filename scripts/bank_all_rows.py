#!/usr/bin/env python3
"""Backfill CLI for banking daily_projections archives or MLB odds_board snapshots."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ev.all_rows_store import bank_payload


def _iter_days(from_day: str, to_day: str) -> list[str]:
    start = date.fromisoformat(from_day)
    end = date.fromisoformat(to_day)
    if end < start:
        return []
    days: list[str] = []
    cur = start
    while cur <= end:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def _gcloud_cp(gcloud_bin: str, uri: str, dest: Path) -> bool:
    proc = subprocess.run(
        [gcloud_bin, "storage", "cp", uri, str(dest)],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bank all-rows probability parquet partitions.")
    parser.add_argument("--sport", required=True, help="Sport name (e.g. nba, nhl, nfl, mlb)")
    parser.add_argument("--from", dest="from_day", help="Start day YYYY-MM-DD (inclusive, archive mode)")
    parser.add_argument("--to", dest="to_day", help="End day YYYY-MM-DD (inclusive, archive mode)")
    parser.add_argument("--out", required=True, help="Local output root directory")
    parser.add_argument("--source", choices=["archive", "odds_board"], default="archive")
    parser.add_argument("--bucket", default="concrete-crow-445205-m4-mlb-data")
    parser.add_argument("--gcloud", default="~/google-cloud-sdk/bin/gcloud")
    args = parser.parse_args(argv)

    sport = args.sport.lower()
    gcloud_bin = os.path.expanduser(args.gcloud)

    if args.source == "archive":
        if not args.from_day or not args.to_day:
            parser.error("--from and --to are required when --source=archive")

        totals: dict = {
            "sport": sport,
            "source": "archive",
            "from": args.from_day,
            "to": args.to_day,
            "rows_in": 0,
            "rows_banked": 0,
            "rows_unpriced": 0,
            "rows_duplicate": 0,
            "partitions": 0,
            "days_banked": 0,
            "days_missing": 0,
            "per_day": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            for day in _iter_days(args.from_day, args.to_day):
                uri = f"gs://{args.bucket}/EVSweep/{sport}_props/archive/{day}.json"
                local_json = tmp_path / f"{day}.json"
                if not _gcloud_cp(gcloud_bin, uri, local_json):
                    print(f"no archive {sport} {day}")
                    totals["days_missing"] += 1
                    continue

                payload = json.loads(local_json.read_text(encoding="utf-8"))
                summary = bank_payload(payload, "daily_projections", args.out)
                print(
                    f"{sport} {day} rows_banked={summary['rows_banked']} "
                    f"unpriced={summary['rows_unpriced']}"
                )
                totals["rows_in"] += summary["rows_in"]
                totals["rows_banked"] += summary["rows_banked"]
                totals["rows_unpriced"] += summary["rows_unpriced"]
                totals["rows_duplicate"] += summary["rows_duplicate"]
                totals["partitions"] += summary["partitions"]
                totals["days_banked"] += 1
                totals["per_day"][day] = summary

        print(json.dumps(totals))
        return 0

    # odds_board mode
    with tempfile.TemporaryDirectory() as tmpdir:
        local_json = Path(tmpdir) / "odds_board_latest.json"
        uri = f"gs://{args.bucket}/EVSweep/mlb_props/odds_board_latest.json"
        if not _gcloud_cp(gcloud_bin, uri, local_json):
            print("no archive mlb odds_board")
            print(json.dumps({"sport": sport, "source": "odds_board", "rows_banked": 0}))
            return 0

        payload = json.loads(local_json.read_text(encoding="utf-8"))
        summary = bank_payload(payload, "mlb_odds_board", args.out)
        print(
            f"{sport} odds_board rows_banked={summary['rows_banked']} "
            f"unpriced={summary['rows_unpriced']}"
        )
        out = {"sport": sport, "source": "odds_board", **summary}
        print(json.dumps(out))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
