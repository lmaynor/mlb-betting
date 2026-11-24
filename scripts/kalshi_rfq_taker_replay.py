#!/usr/bin/env python
"""scripts/kalshi_rfq_taker_replay.py -- Point-in-time replay of RFQ taker combos.

Replays recorded combo quotes through the taker scanner with the merged pricer,
records every fire/pass decision with blade-level reasons, and grades admitted fires
against settlement where the outcome is known.

Two real fixture sources and their limits:
  - SETTLED: production combo markets pulled from Kalshi's public API (the same
    jsonl ev.kalshi_rfq_replay.load_combos reads). bid_yes is the market's LAST
    traded YES price, labeled TRADED_YES_PRICE: a proxy for the executable bid,
    not a recorded RFQ quote.
  - TAPE: the banked recorder tape (Kalshi/rfq_tape/demo/{day}/*.jsonl in the data
    bucket). These are REAL RFQ requests, but from the DEMO exchange, recorded
    2026-10-26/27. bid_yes is the first follow-up trade's yes_price_dollars,
    labeled FOLLOWUP_TRADED_YES_PRICE. Demo combos have no settlement in the
    tape, so tape records are never graded.
  - No production RFQ tape exists anywhere (the production-readonly recorder was
    never provisioned).

Usage:
  python scripts/kalshi_rfq_taker_replay.py settled --combos PATH [--out records.jsonl] [--max-combos N] [--days ...]
  python scripts/kalshi_rfq_taker_replay.py tape --tape-dir DIR [--out records.jsonl] [--max-combos N] [--days ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ev.kalshi_rfq_replay import (
    CompositeStore,
    SnapshotStore,
    fetch_cycle_frames,
    load_combos,
    raw_game_line_snapshots,
    raw_prop_snapshots,
    store_from_game_lines,
    store_from_raw,
)
from ev.combo_taker_window2 import build_shadow_admission
from ev.kalshi_rfq_taker_replay import (
    candidate_from_legs,
    commence_lookup,
    load_tape_records,
    run_candidates,
    settled_to_fixture,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_settled = sub.add_parser("settled", help="replay settled combo markets from public API")
    p_settled.add_argument("--combos", required=True, help="path to settled combos jsonl")
    p_settled.add_argument("--out", default=None, help="output records jsonl path")
    p_settled.add_argument("--max-combos", type=int, default=None, help="max combos to evaluate")
    p_settled.add_argument("--days", default=None, help="comma-separated days (YYYY-MM-DD)")
    p_settled.add_argument("--cache-dir", default=None, help="local cache directory for raw snapshots")
    p_settled.add_argument("--max-age-min", type=float, default=120.0, help="max snapshot age in minutes")
    p_settled.add_argument("--no-raw", action="store_true", help="skip raw all-book prop payloads")
    p_settled.add_argument("--window2", action="store_true", help="enable Window 2 admission engine (wired shadow: needs --fit-rows)")
    p_settled.add_argument("--fit-rows", default=None,
                           help="settled fit rows jsonl for the wired Window 2 shadow (family/day/is_fire/"
                                "ask_no/roi_claimed/pnl_per_contract per row; see scripts/rfq_recompute_roi_claimed.py)")

    p_tape = sub.add_parser("tape", help="replay banked demo recorder tape")
    p_tape.add_argument("--tape-dir", required=True, help="directory containing tape *.jsonl files")
    p_tape.add_argument("--out", default=None, help="output records jsonl path")
    p_tape.add_argument("--max-combos", type=int, default=None, help="max combos to evaluate")
    p_tape.add_argument("--days", default=None, help="comma-separated days (YYYY-MM-DD)")
    p_tape.add_argument("--cache-dir", default=None, help="local cache directory for raw snapshots")
    p_tape.add_argument("--max-age-min", type=float, default=120.0, help="max snapshot age in minutes")
    p_tape.add_argument("--no-raw", action="store_true", help="skip raw all-book prop payloads")
    p_tape.add_argument("--window2", action="store_true", help="enable Window 2 admission engine (wired shadow: needs --fit-rows)")
    p_tape.add_argument("--fit-rows", default=None,
                        help="settled fit rows jsonl for the wired Window 2 shadow (see the settled subcommand)")

    args = ap.parse_args(argv)

    if args.cmd == "settled":
        combos, _ = load_combos(args.combos)
        fixtures = [settled_to_fixture(c) for c in combos]
    elif args.cmd == "tape":
        fixtures = load_tape_records(args.tape_dir)
    else:
        raise ValueError(f"Unknown command: {args.cmd}")

    max_age = args.max_age_min * 60.0

    if args.days:
        days = args.days.split(",")
    else:
        days = sorted({
            (datetime.fromtimestamp(f["ts"], timezone.utc).date() + timedelta(days=k)).isoformat()
            for f in fixtures if f.get("ts") for k in (-1, 0)
        })

    stores = [SnapshotStore.from_cycle_frames(fetch_cycle_frames(days))]
    if not args.no_raw:
        for sport in ("nfl", "nba", "nhl"):
            snaps = raw_prop_snapshots(sport, days, cache_dir=args.cache_dir)
            print(f"raw {sport}: {len(snaps)} snapshots", file=sys.stderr)
            stores.append(store_from_raw(sport, snaps))
            if sport in ("nba", "nhl"):
                gl = raw_game_line_snapshots(sport, days, cache_dir=args.cache_dir)
                print(f"game lines {sport}: {len(gl)} snapshots", file=sys.stderr)
                stores.append(store_from_game_lines(sport, gl))

    store = CompositeStore(stores)
    print(f"store snapshots: {len(store)} | tickers banked: {len(store.tickers)}", file=sys.stderr)

    # Skip fixtures the store cannot price at all
    priceable = [f for f in fixtures if store.may_price(f.get("legs", []))]
    priceable.sort(key=lambda x: float(x.get("ts", 0.0)))

    if args.max_combos is not None:
        priceable = priceable[:args.max_combos]

    print(f"fixtures total: {len(fixtures)} | priceable: {len(priceable)}", file=sys.stderr)

    candidates_with_meta = []
    for f in priceable:
        t = float(f.get("ts", 0.0))
        bid_yes = f.get("bid_yes")
        if bid_yes is None:
            candidates_with_meta.append((None, {**f, "status": "NO_EXECUTABLE_PRICE"}))
            continue

        book = store.book_at(t, max_age)
        lookup = commence_lookup(store, t, max_age)
        if book is None:
            candidates_with_meta.append((None, {**f, "status": "UNPRICED_LEG"}))
            continue

        cand, status = candidate_from_legs(
            combo_id=f.get("combo_id", ""),
            venue="kalshi",
            legs_with_sides=f.get("legs", []),
            book=book,
            t=t,
            bid_yes=bid_yes,
            game_start_lookup=lookup,
        )
        candidates_with_meta.append((cand, {**f, "status": status}))

    # RUL-20261123 item 2: --window2 builds the WIRED shadow (fitted calibrator + seeded trust
    # book) from settled fit rows. The pre-staging driver constructed Window2Admission() bare,
    # which ranked the shadow on raw roi_no_taker with every family pinned at the 0.75 de-rank
    # and the trust book never fed -- the locked package was not what ran. No fit rows, no
    # shadow: fail loudly instead of silently degrading.
    window2_admission = None
    if getattr(args, "window2", False):
        if not args.fit_rows:
            ap.error("--window2 requires --fit-rows (settled fit rows for the wired shadow; "
                     "see scripts/rfq_recompute_roi_claimed.py)")
        with open(args.fit_rows, encoding="utf-8") as fh:
            fit_rows = [json.loads(line) for line in fh if line.strip()]
        window2_admission = build_shadow_admission(fit_rows)
        print(f"window2 shadow wired: {len(fit_rows)} fit rows, calibrator counts_basis="
              f"{window2_admission.calibrator.counts_basis}", file=sys.stderr)
    records, summary = run_candidates(
        candidates_with_meta,
        window2_admission=window2_admission,
    )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        print(f"wrote {len(records)} records to {args.out}", file=sys.stderr)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
