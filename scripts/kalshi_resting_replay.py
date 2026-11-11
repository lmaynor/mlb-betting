#!/usr/bin/env python
"""Tape-delayed resting simulator runner (Argon Prong 2).

Prices the Oct 16 - Nov 1, 2026 settled combo corpus through the snapshot store (the same
plumbing as the Candidate 1 replay: ev.kalshi_rfq_replay CompositeStore + MEASURE pass),
admits Tier-1 / Tier-2 / long-k combos per ev.kalshi_resting_replay.admit_combo, posts the
resting NO bid 10 minutes after creation, and grades every subsequent public tape trade that
crosses it. Records are appended incrementally with ticker-skip resume.

Usage:
  python scripts/kalshi_resting_replay.py \
      --combos ~/workspace/argon-evidence/combos_full.jsonl \
      --tapes ~/workspace/argon-evidence/audit_trade_tapes.json \
      --cache-dir ~/workspace/argon-evidence/cache \
      --out-dir ~/workspace/argon-evidence/resting_sim \
      [--storage-module /path/to/curl_storage.py]

Tapes JSON shape: {combo_ticker: [{created_time, yes_price_dollars, ...}, ...]} (the Nov 10
audit's audit_trade_tapes.json). Combos without a tape entry are priced and counted in the
admitted universe but cannot produce fills; the summary reports tape coverage explicitly.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--combos", required=True)
    ap.add_argument("--tapes", required=True)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--storage-module", default=None,
                    help="optional python file exposing the mlb_core.storage interface (e.g. a curl-backed GCS shim)")
    ap.add_argument("--start", default="2026-10-16")
    ap.add_argument("--end", default="2026-11-01")
    args = ap.parse_args()

    os.environ.setdefault("GCS_BUCKET", "concrete-crow-445205-m4-mlb-data")
    storage = None
    if args.storage_module:
        spec = importlib.util.spec_from_file_location("rest_storage_shim", args.storage_module)
        storage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(storage)

    from ev.kalshi_rfq_pricer import KalshiRFQPricer
    from ev.kalshi_rfq_replay import (CompositeStore, SnapshotStore, fetch_cycle_frames,
                                      load_combos, raw_game_line_snapshots, raw_prop_snapshots,
                                      store_from_game_lines, store_from_raw, epoch,
                                      MEASURE_CFG, MAX_SNAPSHOT_AGE_SEC)
    from ev.kalshi_resting_replay import admit_combo, grade_combo_fills, grade_fills, resting_no_bid, EDGE_BY_CLASS

    os.makedirs(args.out_dir, exist_ok=True)
    recs_path = os.path.join(args.out_dir, "resting_recs.jsonl")
    t0 = time.time()
    done = set()
    if os.path.exists(recs_path):
        for line in open(recs_path):
            done.add(json.loads(line)["ticker"])
    print(f"resume: {len(done)} tickers already recorded", flush=True)

    combos_all, _ = load_combos(args.combos)
    combos = [c for c in combos_all if args.start <= c["close_time"][:10] <= args.end]
    print(f"combos in window: {len(combos)} ({time.time()-t0:.0f}s)", flush=True)
    days = sorted({(datetime.fromisoformat(c["created_time"].replace("Z", "+00:00")).date() + timedelta(days=k)).isoformat()
                   for c in combos for k in (-1, 0)})
    stores = [SnapshotStore.from_cycle_frames(fetch_cycle_frames(days, storage=storage))]
    print(f"cycle store built: {len(stores[0])} snaps ({time.time()-t0:.0f}s)", flush=True)
    for sport in ("nfl", "nba", "nhl"):
        snaps = raw_prop_snapshots(sport, days, cache_dir=args.cache_dir, storage=storage)
        stores.append(store_from_raw(sport, snaps))
        print(f"raw {sport}: {len(snaps)} snaps ({time.time()-t0:.0f}s)", flush=True)
        if sport in ("nba", "nhl"):
            gl = raw_game_line_snapshots(sport, days, cache_dir=args.cache_dir, storage=storage)
            stores.append(store_from_game_lines(sport, gl))
            print(f"gl {sport}: {len(gl)} snaps ({time.time()-t0:.0f}s)", flush=True)
    store = CompositeStore(stores)
    print(f"composite store: {len(store)} snaps, {len(store.tickers)} tickers ({time.time()-t0:.0f}s)", flush=True)

    tapes = json.load(open(args.tapes))
    print(f"tape tickers available: {len(tapes)}", flush=True)

    measure_cfg = replace(MEASURE_CFG, max_staleness_sec=MAX_SNAPSHOT_AGE_SEC)

    def _rfq(combo):
        return {"id": combo["ticker"], "market_ticker": combo["ticker"], "contracts_fp": "1.00",
                "mve_selected_legs": [{"event_ticker": "", "market_ticker": t, "side": s} for t, s in combo["legs"]]}

    cands = [c for c in combos if store.may_price(c["legs"])]
    print(f"replay candidates (may_price): {len(cands)}", flush=True)
    out = open(recs_path, "a")
    n_rec = n_skip = n_nosnap = 0
    for i, c in enumerate(cands):
        if c["ticker"] in done:
            n_skip += 1
            continue
        t = epoch(c["created_time"])
        book = store.book_at(t, MAX_SNAPSHOT_AGE_SEC)
        y = float(c.get("last_price_dollars") or 0.0)
        if book is None or c.get("result") not in ("yes", "no") or not (0.0 < y < 1.0):
            n_nosnap += 1
            continue
        m = KalshiRFQPricer(book, measure_cfg).price(_rfq(c), t)
        legs = []
        for tk, side in c["legs"]:
            fv = book.get(tk, t)
            if fv is not None:
                legs.append({"ticker": tk, "side": side, "sport": fv.sport, "stat": fv.stat,
                             "game_key": fv.game_key, "player": fv.player_norm,
                             "p_cons_yes": round(float(fv.p_cons_yes), 5),
                             "p_model_yes": (round(float(fv.p_model_yes), 5) if fv.p_model_yes is not None else None),
                             "n_books": int(fv.n_books), "pooled": bool(fv.pooled)})
        adm = admit_combo(n_legs=len(c["legs"]), n_unpriced=int(m.notes.get("n_unpriced", len(c["legs"]))),
                          p_cons_yes=m.p_yes_cons, legs=legs)
        bid = None
        fills = []
        if adm.admitted:
            bid = resting_no_bid(m.p_yes_cons, EDGE_BY_CLASS[adm.edge_class])
            raw = tapes.get(c["ticker"]) or []
            trades = [{"ts": epoch(tr["created_time"]), "yes_price": float(tr["yes_price_dollars"])} for tr in raw]
            fills = grade_combo_fills(created_epoch=t, p_no_bid=bid, p_true_yes=m.p_yes_true,
                                      yes_won=c["result"] == "yes", trades=trades)
        rec = {"ticker": c["ticker"], "day": c["close_time"][:10], "created": c["created_time"],
               "n_legs": len(c["legs"]), "yes_price": y, "yes_won": c["result"] == "yes",
               "n_unpriced": int(m.notes.get("n_unpriced", len(c["legs"]))),
               "p_true": m.p_yes_true, "p_cons": m.p_yes_cons, "measure_reason": m.reason,
               "admitted": adm.admitted, "tier": adm.tier, "edge_class": adm.edge_class,
               "admit_reason": adm.reason, "no_bid": bid,
               "has_tape": c["ticker"] in tapes, "fills": fills}
        out.write(json.dumps(rec) + "\n")
        n_rec += 1
        if i % 1000 == 0:
            out.flush()
            print(f"replay {i}/{len(cands)} new={n_rec} skip={n_skip} ({time.time()-t0:.0f}s)", flush=True)
    out.close()
    print(f"DONE new_recs={n_rec} skipped_done={n_skip} skipped_no_snapshot_or_price={n_nosnap} ({time.time()-t0:.0f}s)", flush=True)

    # ---- grade ---------------------------------------------------------------------
    recs = [json.loads(l) for l in open(recs_path)]
    fills_by_day = {}
    for r in recs:
        if r["fills"]:
            fills_by_day.setdefault(r["day"], []).extend(r["fills"])
    g = grade_fills(fills_by_day)
    admitted = [r for r in recs if r["admitted"]]
    summary = {"window": [args.start, args.end], "combos_in_window": len(combos),
               "candidates_may_price": len(cands), "records": len(recs),
               "admitted": len(admitted),
               "admitted_with_tape": sum(1 for r in admitted if r["has_tape"]),
               "tape_tickers_available": len(tapes),
               "grade": g}
    with open(os.path.join(args.out_dir, "resting_summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
