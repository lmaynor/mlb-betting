"""mm_tape_fetch.py -- download Kalshi's PUBLIC settled-market list + trade tape for NFL series (read-only), for the
market-making analysis (docs/analysis/market_making_2026-10-12.md). Output: one CSV.gz per series.
Usage: python scripts/mm_tape_fetch.py OUTDIR SERIES:min_volume[,SERIES:min_volume...]"""
import csv
import gzip
import os
import sys
import time

import threading
from concurrent.futures import ThreadPoolExecutor

import requests

B = "https://api.elections.kalshi.com/trade-api/v2"
S = requests.Session()
S.headers["User-Agent"] = "beezy-mm-research/0.1"
_last = [0.0]
_lock = threading.Lock()
GAP = 0.11                      # shared limiter: ~9 requests/second across all workers


def get(path, **q):
    for a in range(8):
        with _lock:
            wait = GAP - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            r = S.get(B + path, params=q, timeout=30)
        except requests.RequestException:                       # connection resets etc.: back off and retry
            time.sleep(min(20, 2.0 * (a + 1)))
            continue
        if r.status_code == 200:
            return r.json()
        time.sleep(min(20, 1.5 * (a + 1)))
    return {}


def settled(series):
    out, cur = [], None
    while True:
        q = {"series_ticker": series, "status": "settled", "limit": 1000}
        if cur:
            q["cursor"] = cur
        d = get("/markets", **q)
        out += d.get("markets", [])
        cur = d.get("cursor")
        if not cur:
            return out


outdir, spec = sys.argv[1], sys.argv[2]
for item in spec.split(","):
    parts = item.split(":")
    series, vmin = parts[0], float(parts[1])
    months = parts[2].split("+") if len(parts) > 2 else ["SEP", "OCT"]          # e.g. KXNFLRECYDS:3000:OCT
    suffix = "" if len(parts) <= 2 else "_" + "_".join(months)
    ms = [m for m in settled(series) if m.get("result") in ("yes", "no")
          and m["event_ticker"].split("-")[1][2:5] in months and m["event_ticker"].split("-")[1][:2] == "26"
          and float(m.get("volume_fp") or 0) >= vmin]
    print(f"{series}: {len(ms)} settled markets with volume>={vmin:.0f}", flush=True)
    path = os.path.join(outdir, f"{series}{suffix}.csv.gz")

    def one(m):
        rows, cur = [], None
        while True:
            q = {"ticker": m["ticker"], "limit": 1000}
            if cur:
                q["cursor"] = cur
            d = get("/markets/trades", **q)
            for t in d.get("trades", []):
                rows.append([series, m["ticker"], m["event_ticker"], m["result"], m.get("occurrence_datetime"),
                             m.get("open_time"), m.get("close_time"), m.get("volume_fp"), t["created_time"],
                             t["yes_price_dollars"], t["count_fp"], t["taker_side"], m.get("floor_strike"),
                             m.get("title")])
            cur = d.get("cursor")
            if not cur or not d.get("trades"):
                return rows

    n = 0
    with gzip.open(path, "wt", newline="") as fh, ThreadPoolExecutor(max_workers=8) as ex:
        w = csv.writer(fh)
        w.writerow(["series", "ticker", "event", "result", "occurrence", "open_time", "close_time", "mkt_volume",
                    "created_time", "yes_price", "count", "taker_side", "strike", "title"])
        for i, rows in enumerate(ex.map(one, ms)):
            w.writerows(rows)
            n += len(rows)
            if (i + 1) % 200 == 0:
                print(f"  {series} {i+1}/{len(ms)} markets, {n} trades", flush=True)
    print(f"{series}: done {len(ms)} markets, {n} trades -> {path}", flush=True)
