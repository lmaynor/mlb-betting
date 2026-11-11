#!/usr/bin/env python3
"""Guarded CLI for the RotoWire NBA historic prose puller (nba/data/rotowire_archive.py).

DISABLED BY DEFAULT -- Luc's instruction (2026-11-11): "do the build but don't trigger the run yet."

Without BOTH gates open this script makes ZERO HTTP requests and ZERO storage writes:
  * no --execute flag            -> prints the dry-run plan and exits 0
  * --execute but the env flag   -> prints a refusal and exits 2
    ROTOWIRE_PULL_ENABLED is not "1"

The exact trigger command for the future run (do not run it until Luc says so):

    ROTOWIRE_PULL_ENABLED=1 python3 scripts/pull_rotowire_prose.py --execute \
        --players-csv ~/workspace/rotowire_player_map.csv \
        --boxscores <banked hoopR player_boxscores parquet/csv> \
        [--wide] [--delay 2.5] [--jitter 0.5]

Forward RSS banking variant (same gates):

    ROTOWIRE_PULL_ENABLED=1 python3 scripts/pull_rotowire_prose.py --execute --rss

The player map is a CSV/JSON with columns: player, rotowire_id, slug. The case set is rebuilt from
the banked box scores at run time (defaults: injury returns, missed >= 4, prior-8 baseline >= 18;
--wide widens to missed >= 2 / baseline >= 15).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nba.data import rotowire_archive as rw  # noqa: E402


def _load_boxscores(path: str):
    import pandas as pd
    return pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)


def main(argv=None, session=None, env=None, store=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--execute", action="store_true",
                    help="actually run ( ALSO requires env ROTOWIRE_PULL_ENABLED=1; without it, refused)")
    ap.add_argument("--rss", action="store_true", help="forward RSS banking variant instead of the historic pull")
    ap.add_argument("--players-csv", help="player map CSV/JSON: player, rotowire_id, slug")
    ap.add_argument("--boxscores", help="banked hoopR player_boxscores (.parquet or .csv) for the case-set builder")
    ap.add_argument("--wide", action="store_true",
                    help="widen the case population to missed >= 2 / baseline >= 15 (default: >= 4 / >= 18)")
    ap.add_argument("--delay", type=float, default=rw.DEFAULT_DELAY_S, help="seconds between requests (>= 2.5 polite default)")
    ap.add_argument("--jitter", type=float, default=0.0, help="extra uniform jitter seconds on top of --delay")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    store = rw.storage if store is None else store

    if args.rss:
        res = rw.run_rss_snapshot(session=session, execute=args.execute, env=env, store=store)
        print(json.dumps(res, indent=1))
        if res["status"] == "disabled":
            return 2 if args.execute else 0
        return 0

    players, cases = [], []
    if args.players_csv:
        players = rw.load_player_map(args.players_csv)
    if args.boxscores:
        frame = _load_boxscores(args.boxscores)
        cases = rw.build_case_set(
            frame,
            min_missed=rw.WIDE_MIN_MISSED if args.wide else rw.DEFAULT_MIN_MISSED,
            min_baseline=rw.WIDE_MIN_BASELINE if args.wide else rw.DEFAULT_MIN_BASELINE)
    res = rw.run_historic_pull(players, cases, session=session, execute=args.execute, env=env,
                               delay=args.delay, jitter=args.jitter, store=store)
    print(json.dumps(res, indent=1, default=str))
    if res["status"] == "disabled":
        # Dry-run plan (no --execute) is a successful no-op; --execute without the env flag is a refusal.
        return 2 if args.execute else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
