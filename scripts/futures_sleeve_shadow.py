#!/usr/bin/env python3
"""scripts/futures_sleeve_shadow.py -- Runner for futures sleeve shadow entry-signal logging.

Reads banked Kalshi and BettingPros futures partitions for a given date, builds the devigged
futures board, and writes shadow signal records with P_sim explicitly null.

If NO partitions exist for the date, exits 0 with a clear "no boards banked for <date>" message.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from ev.futures_board_feed import (
    NullFuturesFairProvider,
    _read_parquet_key,
    basis_monitor,
    bp_rows_to_snapshots,
    build_board,
    discover_bp_futures_partitions,
    kalshi_rows_to_snapshots,
    load_kalshi_partition,
    shadow_signal_log,
    write_shadow_log,
)


def get_today_et() -> str:
    """Return today's date in America/New_York (ET) as YYYY-MM-DD."""
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Futures sleeve shadow entry-signal logger"
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Target partition date (YYYY-MM-DD, default today ET)",
    )
    parser.add_argument(
        "--bucket-root",
        default="gs://concrete-crow-445205-m4-mlb-data",
        help="Root storage path (default gs://concrete-crow-445205-m4-mlb-data)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output destination path or '-' for stdout (default Futures/sleeve_shadow/dt=<date>/signals.jsonl)",
    )

    args = parser.parse_args()
    date_str = args.date or get_today_et()
    root = args.bucket_root
    out_path = args.out or f"Futures/sleeve_shadow/dt={date_str}/signals.jsonl"
    diag_out = sys.stderr if out_path == "-" else sys.stdout

    # 1. Load Kalshi partition
    kalshi_df = load_kalshi_partition(root, date_str)

    # 2. Discover and load BP futures partitions
    bp_partitions = discover_bp_futures_partitions(root, date_str)
    bp_frames: list[tuple[pd.DataFrame, str, str]] = []
    for sport, slug, key in bp_partitions:
        df = _read_parquet_key(root, key)
        if df is not None and not df.empty:
            if "market" not in df.columns or df["market"].isna().all():
                df = df.copy()
                df["market"] = slug
            bp_frames.append((df, sport, slug))

    has_kalshi = kalshi_df is not None and not kalshi_df.empty
    has_bp = len(bp_frames) > 0

    # 3. Check partition existence -- absence is not an error
    if not has_kalshi and not has_bp:
        print(f"no boards banked for {date_str}", file=diag_out)
        sys.exit(0)

    # 4. Build board via n-way Power devig
    board_rows = build_board(kalshi_df=kalshi_df, bp_frames=bp_frames)
    if not board_rows:
        print(f"no boards banked for {date_str} (zero valid quotes in partitions)", file=diag_out)
        sys.exit(0)

    # 5. Generate shadow signal log (Null provider: p_sim None -> NO_BET_NO_PSIM)
    log_records = shadow_signal_log(board_rows, fair_provider=NullFuturesFairProvider())

    # 5b. Shadow cross-venue basis monitor (SHADOW_BASIS_ONLY, measurement only)
    if has_kalshi and has_bp:
        k_snaps, _ = kalshi_rows_to_snapshots(kalshi_df)
        b_snaps_all: list = []
        for df_b, sport_b, slug_b in bp_frames:
            snaps_b, _ = bp_rows_to_snapshots(df_b, sport_b, slug=slug_b)
            b_snaps_all.extend(snaps_b)
        log_records = log_records + basis_monitor(k_snaps, b_snaps_all)

    # 6. Write output
    dest = write_shadow_log(log_records, out_path, root=root)

    # 7. Print counts
    if has_kalshi:
        _, k_stats = kalshi_rows_to_snapshots(kalshi_df)
        print(
            f"Kalshi: {k_stats['total_rows']} rows, {k_stats['valid_snapshots']} valid snapshots, "
            f"{k_stats['skipped_no_price']} skipped",
            file=diag_out,
        )
    else:
        print("Kalshi: no partition found", file=diag_out)

    if has_bp:
        total_bp_rows = sum(len(df) for df, _, _ in bp_frames)
        total_bp_valid = 0
        total_bp_ou_excluded = 0
        total_bp_open_excluded = 0
        sports_found = set()
        slugs_found = set()
        for df, sp, sl in bp_frames:
            sports_found.add(sp)
            slugs_found.add(sl)
            _, bp_stat = bp_rows_to_snapshots(df, sp, slug=sl)
            total_bp_valid += bp_stat["valid_snapshots"]
            total_bp_ou_excluded += bp_stat["excluded_season_ou"]
            total_bp_open_excluded += bp_stat["excluded_is_open"]
        print(
            f"BettingPros: {len(bp_frames)} partitions ({len(sports_found)} sports: {', '.join(sorted(sports_found))}; "
            f"{len(slugs_found)} slugs), {total_bp_rows} raw rows, {total_bp_valid} valid snapshots "
            f"({total_bp_ou_excluded} season O/U excluded, {total_bp_open_excluded} openers excluded)",
            file=diag_out,
        )
    else:
        print("BettingPros: no partitions found", file=diag_out)

    n_signals = sum(1 for r in log_records if r.get("kind") == "signal")
    n_summaries = sum(1 for r in log_records if r.get("kind") == "board_summary")
    print(
        f"Board: {len(board_rows)} devigged rows across {n_summaries} venue-market pairs",
        file=diag_out,
    )
    print(
        f"Log: {len(log_records)} records ({n_signals} signals, {n_summaries} summaries) written to {dest}",
        file=diag_out,
    )


if __name__ == "__main__":
    main()
