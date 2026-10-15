"""Build the historical BettingPros market-consensus table for a sport and write it to a parquet.

    python scripts/join_bettingpros_history.py --sport nfl --start 2021-09-01 --end 2025-02-15 --out /tmp/nfl_cons.parquet
    python scripts/join_bettingpros_history.py --sport nhl --markets sog --out /tmp/nhl_sog.parquet [--min-books 3]

Reads the daily partitions written by the BettingPros backfill (Odds/bettingpros_{sport}/{market}/{date}.parquet) through
mlb_core.storage, keeps real books + exchanges (never Kalshi/DFS/consensus/open/sharp), pairs over/under per book,
de-vigs, and writes ONE row per (game, player, stat) at the modal line (see mlb_core/odds/bettingpros_loader.py).
The output is written LOCALLY by default; pass --gcs to write the same parquet through mlb_core.storage instead
(--out is then a storage key such as Odds/bettingpros_consensus/nfl.parquet).

Prints coverage: rows, game days, books-per-row histogram, rows per stat. Selection note: each row is the LAST quote
BettingPros held for that day (close-ish, one snapshot per day), not an opening line or a time series.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mlb_core.odds import bettingpros_loader as L  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sport", required=True, choices=["nfl", "nba", "nhl"])
    ap.add_argument("--start", default=None, help="first partition date, YYYY-MM-DD (default: all)")
    ap.add_argument("--end", default=None, help="last partition date, YYYY-MM-DD (default: all)")
    ap.add_argument("--out", required=True, help="output parquet path (a storage key with --gcs)")
    ap.add_argument("--markets", default=None, help="comma list of stat labels or slugs (default: every market)")
    ap.add_argument("--prefix", default=None, help="partition prefix (default Odds/bettingpros_{sport})")
    ap.add_argument("--min-books", type=int, default=3, help="min two-sided books at the modal line (default 3)")
    ap.add_argument("--pregame-only", action="store_true",
                    help="drop quotes whose `updated` is after the scheduled start (assumes both are UTC)")
    ap.add_argument("--gcs", action="store_true", help="write via mlb_core.storage instead of a local file")
    a = ap.parse_args(argv)

    markets = [m.strip() for m in a.markets.split(",") if m.strip()] if a.markets else None
    cons = L.build_market_consensus(a.sport, a.start, a.end, markets=markets, prefix=a.prefix,
                                    min_books=a.min_books, pregame_only=a.pregame_only)
    cov = L.coverage(cons)
    print(f"sport={a.sport} rows={cov['rows']} game_days={cov['days']} min_books={a.min_books}")
    print("books per row: " + (", ".join(f"{k}:{v}" for k, v in cov["books_hist"].items()) or "(none)"))
    print("rows per stat: " + (", ".join(f"{k}:{v}" for k, v in cov["by_stat"].items()) or "(none)"))
    if cov["rows"] == 0:
        print("no consensus rows (no partitions in range, or no prop reached min_books) -- nothing written")
        return 1
    if a.gcs:
        from mlb_core import storage
        storage.write_parquet(cons, a.out)
        print(f"wrote {a.out} via mlb_core.storage")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        cons.to_parquet(a.out, index=False)
        print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
