"""
bettingpros_api.py -- local CLI for the BettingPros multi-market backfill.

Thin wrapper over mlb_core.odds.bettingpros (the shared API client + parsers).
Writes one CSV per market to a local --output-dir. For the server-side
unattended version that writes to GCS, see mlb.runners.backfill_bettingpros
(Cloud Run Job) -- both share the same parsing core so they never drift.

Usage
-----
  python scripts/bettingpros_api.py markets
  python scripts/bettingpros_api.py daily 2024-05-01 --markets all
  python scripts/bettingpros_api.py backfill --start 2024-04-01 --end 2026-06-29 \
      --markets player,lines,innings
  # market groups: player | lines | innings | all   (or explicit ids: 299,285)

Run from Cloud Shell -- local network egress to api.bettingpros.com is blocked.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from datetime import date
from pathlib import Path

# Make mlb_core importable when run as `python scripts/bettingpros_api.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlb_core.odds import bettingpros as bp  # noqa: E402

DEFAULT_OUTPUT_DIR = Path("data/bettingpros")


def path_for(output_dir: Path, market_id: int) -> Path:
    name, _ = bp.MARKETS[market_id]
    return output_dir / f"{name}_odds.csv"


def ensure_csv(path: Path, kind: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=bp.headers(kind)).writeheader()


def completed_dates(path: Path) -> set:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open("r", newline="", encoding="utf-8") as f:
        return {r["Date"] for r in csv.DictReader(f) if r.get("Date")}


def append_rows(path: Path, kind: str, rows: list) -> None:
    ensure_csv(path, kind)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=bp.headers(kind), extrasaction="ignore")
        for r in rows:
            writer.writerow(r)


def list_markets() -> None:
    sess = bp.make_session()
    labels = bp.fetch_markets(sess)
    print(f"{len(labels)} MLB markets ({len(bp.MARKETS)} wired):")
    for mid, (name, kind) in bp.MARKETS.items():
        print(f"  {mid:>4}  {kind:<11} {name:<24} ({labels.get(mid, '?')})")


def collect_date(sess, ds: str, markets: list, output_dir: Path,
                 done: dict, verbose: bool) -> int:
    need = [m for m in markets if ds not in done[m]]
    if not need:
        return 0
    ev_map = bp.fetch_events(sess, ds)
    if not ev_map:
        if verbose:
            print(f"{ds}: no events")
        return 0
    eids = list(ev_map.keys())
    total = 0
    for mid in need:
        name, kind = bp.MARKETS[mid]
        offers = bp.fetch_offers(sess, mid, eids)
        rows = bp.build_rows(kind, offers, ds, ev_map)
        if rows:
            append_rows(path_for(output_dir, mid), kind, rows)
        done[mid].add(ds)
        total += len(rows)
        if verbose:
            print(f"{ds} {name}: {len(rows)}")
        time.sleep(0.2)
    return total


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(description="API-based BettingPros MLB multi-market backfill")
    p.add_argument("mode", choices=["markets", "daily", "backfill"])
    p.add_argument("date", nargs="?", help="YYYY-MM-DD for daily mode")
    p.add_argument("--markets", default="all", help="groups (player,lines,innings,all) or ids")
    p.add_argument("--start", default="2024-04-01")
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--delay", type=float, default=0.6)
    args = p.parse_args(argv)

    if args.mode == "markets":
        list_markets()
        return 0

    sess = bp.make_session()
    markets = bp.resolve_markets(args.markets)
    for mid in markets:
        ensure_csv(path_for(args.output_dir, mid), bp.MARKETS[mid][1])
    done = {mid: completed_dates(path_for(args.output_dir, mid)) for mid in markets}

    if args.mode == "daily":
        if not args.date:
            p.error("daily mode requires a date")
        n = collect_date(sess, args.date, markets, args.output_dir, done, verbose=True)
        print(f"{args.date}: {n} rows total across {len(markets)} markets")
        return 0

    dates = [d for d in bp.date_range(args.start, args.end) if bp.in_season(d)]
    print(f"Backfill {args.start} -> {args.end}: {len(dates)} in-season dates x {len(markets)} markets")
    errors = 0
    for ds in dates:
        if all(ds in done[m] for m in markets):
            continue
        try:
            n = collect_date(sess, ds, markets, args.output_dir, done, verbose=False)
            print(f"{ds}: {n} rows")
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"{ds}: ERROR {exc}", file=sys.stderr)
        time.sleep(args.delay * random.uniform(0.6, 1.4))
    print(f"Done. errors={errors}, output_dir={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
