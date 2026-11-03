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
from ev.kalshi_rfq_replay import (  # noqa: E402
    CompositeStore, SnapshotStore, audit_legs, fetch_cycle_frames, leg_records, load_combos, pull_combos, raw_game_line_snapshots,
    raw_prop_snapshots, replay_one, store_from_game_lines, store_from_raw, summarize,
)


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
    r.add_argument("--no-raw", action="store_true", help="skip the raw all-book prop payloads (NFL/NBA/NHL); cycle rows only")
    r.add_argument("--cache-dir", default=None, help="local cache for raw payloads (they are re-read on every run otherwise)")
    r.add_argument("--max-age-min", type=float, default=120.0, help="oldest pre-game snapshot a combo may be priced from (default 120)")
    r.add_argument("--days", default=None, help="comma list of EVSweep/nfl_props day folders (default: every day the combos span, +1 before)")
    au = sub.add_parser("audit", help="per-series leg coverage: why legs do or do not join")
    au.add_argument("--combos", required=True)
    au.add_argument("--no-raw", action="store_true", help="skip the raw all-book prop payloads (NFL/NBA/NHL); cycle rows only")
    au.add_argument("--cache-dir", default=None, help="local cache for raw payloads (they are re-read on every run otherwise)")
    au.add_argument("--max-age-min", type=float, default=120.0)
    au.add_argument("--top", type=int, default=30)
    au.add_argument("--days", default=None)
    lg = sub.add_parser("legs", help="export each combo's priced legs (fair p per leg at creation) as jsonl, for the correlation studies")
    lg.add_argument("--combos", required=True)
    lg.add_argument("--out", required=True)
    lg.add_argument("--no-raw", action="store_true")
    lg.add_argument("--cache-dir", default=None)
    lg.add_argument("--max-age-min", type=float, default=120.0)
    lg.add_argument("--days", default=None)
    lg.add_argument("--min-priced", type=int, default=2, help="keep combos with at least this many priced legs")
    a = ap.parse_args(argv)

    if a.cmd == "pull":
        with open(a.out, "w") as fh:
            for rec in pull_combos(_day(a.start), _day(a.end), window_hours=a.window_hours, max_pages=a.max_pages):
                fh.write(json.dumps(rec) + "\n")
        return 0

    combos, windows = load_combos(a.combos)
    max_age = a.max_age_min * 60.0
    days = a.days.split(",") if a.days else sorted({
        (datetime.fromisoformat(c["created_time"].replace("Z", "+00:00")).date() + timedelta(days=k)).isoformat()
        for c in combos for k in (-1, 0)})
    stores = [SnapshotStore.from_cycle_frames(fetch_cycle_frames(days))]
    if not a.no_raw:
        for sport in ("nfl", "nba", "nhl"):
            snaps = raw_prop_snapshots(sport, days, cache_dir=a.cache_dir)
            print(f"raw {sport}: {len(snaps)} snapshots", file=sys.stderr)
            stores.append(store_from_raw(sport, snaps))
            if sport in ("nba", "nhl"):
                gl = raw_game_line_snapshots(sport, days, cache_dir=a.cache_dir)
                print(f"game lines {sport}: {len(gl)} snapshots", file=sys.stderr)
                stores.append(store_from_game_lines(sport, gl))
    store = CompositeStore(stores)
    if not len(stores[0]):
        raise SystemExit("no sweep cycle snapshots found for " + ",".join(days[:3]) + "...: set GCS_BUCKET (or MLB_GCS_BUCKET) "
                         "to the data bucket, or point MLB_BASE_DATA at a local copy")
    print(f"combos with volume+result: {len(combos)} | windows {len(windows)} (truncated {sum(w['truncated'] for w in windows)}) | "
          f"snapshots {len(store)} over {len(days)} day folders | tickers banked {len(store.tickers)}", file=sys.stderr)
    if a.cmd == "legs":
        n = 0
        with open(a.out, "w") as fh:
            for c in combos:
                if not store.may_price(c["legs"]):
                    continue
                rec = leg_records(c, store, max_age)
                if rec and len(rec["legs"]) >= a.min_priced:
                    fh.write(json.dumps(rec) + "\n")
                    n += 1
        print(f"wrote {n} combos with >= {a.min_priced} priced legs to {a.out}", file=sys.stderr)
        return 0
    if a.cmd == "audit":
        res = audit_legs(combos, store, max_age)
        tot = {f: sum(c.values()) for f, c in res.items()}
        grand = sum(tot.values())
        hit = sum(v for c in res.values() for k, v in c.items() if k.startswith("HIT"))
        print(f"leg occurrences {grand} | hit {hit / grand:.1%}")
        for f, n in sorted(tot.items(), key=lambda x: -x[1])[:a.top]:
            print(f"{f:22s} {n:8d} {n / grand:6.1%}  " + ", ".join(f"{k}:{v / n:.0%}" for k, v in sorted(res[f].items(), key=lambda x: -x[1])[:5]))
        return 0
    recs, skipped = [], 0
    for c in combos:
        if not store.may_price(c["legs"]):
            skipped += 1                                     # no leg can map to a banked game: unpriceable, cheap to skip
            continue
        rec = replay_one(c, store, max_age)
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
