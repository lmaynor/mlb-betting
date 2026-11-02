#!/usr/bin/env python
"""Point-in-time replay of real settled Kalshi sports combos through the RFQ pricer (see ev/kalshi_rfq_replay.py).

  # 1. pull (public API, no key; writes jsonl; a busy window is a capped sample)
  python scripts/kalshi_rfq_replay.py pull --start 2026-10-16 --end 2026-11-01 --out combos.jsonl
  # 2. replay against the banked NFL sweep cycles and print the summary
  python scripts/kalshi_rfq_replay.py replay --combos combos.jsonl [--csv out.csv]
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev.kalshi_rfq_replay import SnapshotStore, fetch_cycle_frames, load_combos, pull_combos, replay_one, summarize  # noqa: E402


def _day(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--window-hours", type=int, default=4)
    p.add_argument("--max-pages", type=int, default=8)
    r = sub.add_parser("replay")
    r.add_argument("--combos", required=True)
    r.add_argument("--csv", default=None)
    r.add_argument("--days", default=None, help="comma list of EVSweep/nfl_props day folders (default: every day the combos span, +1 before)")
    a = ap.parse_args(argv)

    if a.cmd == "pull":
        with open(a.out, "w") as fh:
            for rec in pull_combos(_day(a.start), _day(a.end), window_hours=a.window_hours, max_pages=a.max_pages):
                fh.write(json.dumps(rec) + "\n")
        return 0

    combos, windows = load_combos(a.combos)
    days = a.days.split(",") if a.days else sorted({
        (datetime.fromisoformat(c["created_time"].replace("Z", "+00:00")).date() + timedelta(days=k)).isoformat()
        for c in combos for k in (-1, 0)})
    store = SnapshotStore.from_cycle_frames(fetch_cycle_frames(days))
    if not len(store):
        raise SystemExit("no sweep cycle snapshots found for " + ",".join(days[:3]) + "...: set GCS_BUCKET (or MLB_GCS_BUCKET) "
                         "to the data bucket, or point MLB_BASE_DATA at a local copy")
    print(f"combos with volume+result: {len(combos)} | windows {len(windows)} (truncated {sum(w['truncated'] for w in windows)}) | "
          f"snapshots {len(store)} over {len(days)} day folders | tickers banked {len(store.tickers)}", file=sys.stderr)
    recs, skipped = [], 0
    for c in combos:
        if not any(t in store.tickers for t, _ in c["legs"]):
            skipped += 1                                     # no leg was ever banked: unpriceable, cheap to skip
            continue
        rec = replay_one(c, store)
        if rec:
            recs.append(rec)
    print(f"replayed {len(recs)} | skipped (no banked leg) {skipped}", file=sys.stderr)
    print(json.dumps(summarize(recs), indent=1))
    if a.csv and recs:
        with open(a.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(recs[0]))
            w.writeheader()
            w.writerows(recs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
