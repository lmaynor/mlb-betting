"""Bank daily NBA.com Stats Passing tracking data to GCS via mlb_core.storage."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from mod04c.passing import bank_passing, fetch_passing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch and bank NBA.com Stats Passing data.")
    ap.add_argument("--season", default="2025-26")
    ap.add_argument("--season-type", default="Regular Season")
    ap.add_argument("--per-mode", default="PerGame")
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--timeout", type=float, default=90.0,
                    help="Per-request timeout seconds (stats.nba.com is slow through the egress proxy)")
    args = ap.parse_args(argv)

    try:
        df = fetch_passing(
            season=args.season,
            season_type=args.season_type,
            per_mode=args.per_mode,
            timeout=args.timeout,
        )
        key = bank_passing(df, args.date)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Banked {len(df)} rows -> {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
