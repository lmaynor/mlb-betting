#!/usr/bin/env python3
"""Probe Kalshi's historical-price endpoints on ONE settled MLB market.

Answers the two questions we need before building a throttled backfill:
  1. Are candlesticks / trades PUBLIC (200) or do they need the RSA-signed key (401)?
  2. What is the response shape (field names) of each?

Uses requests directly (NOT mlb_core.odds.kalshi.get) so HTTP status codes are
visible -- a 401 on candlesticks means we must add RSA request signing.

Run (Cloud Shell):  python3 scripts/kalshi_hist_probe.py [SERIES_TICKER]
Default series KXMLBGAME (liquid game moneyline -> real candles/trades).
"""
import datetime as dt
import json
import sys

import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"
S = requests.Session()
S.headers["User-Agent"] = "beezy-kalshi-histprobe/1.0"
SERIES = sys.argv[1] if len(sys.argv) > 1 else "KXMLBGAME"


def g(path, **params):
    r = S.get(BASE + path, params=params, timeout=30)
    ct = r.headers.get("content-type", "")
    body = r.json() if ct.startswith("application/json") else r.text[:300]
    return r.status_code, body


def to_ts(iso):
    if not iso:
        return None
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def main():
    print(f"BASE={BASE}  series={SERIES}\n")

    sc, data = g("/markets", series_ticker=SERIES, status="settled", limit=25)
    print(f"GET /markets?status=settled -> {sc}")
    mkts = data.get("markets", []) if isinstance(data, dict) else []
    # prefer one that actually traded
    traded = [m for m in mkts if (m.get("volume_fp") or m.get("volume") or 0)]
    pool = traded or mkts
    if not pool:
        print(f"no settled markets for {SERIES}: {str(data)[:200]}")
        return
    m = pool[0]
    tk = m.get("ticker")
    print(f"probe market: {tk}")
    print(f"  open={m.get('open_time')} close={m.get('close_time')} "
          f"result={m.get('result')!r} vol={m.get('volume_fp') or m.get('volume')}")

    sc, one = g(f"/markets/{tk}")
    mk = one.get("market", {}) if isinstance(one, dict) else {}
    start = to_ts(mk.get("open_time") or m.get("open_time"))
    end = to_ts(mk.get("close_time") or m.get("close_time"))
    print(f"  start_ts={start} end_ts={end}")

    # --- candlesticks (the price track we want) ---
    sc, cs = g(f"/series/{SERIES}/markets/{tk}/candlesticks",
               start_ts=start, end_ts=end, period_interval=60)
    print(f"\nGET candlesticks -> {sc}   (401 => needs RSA-signed auth)")
    if isinstance(cs, dict):
        arr = cs.get("candlesticks", [])
        print(f"  n={len(arr)}")
        if arr:
            print("  first:", json.dumps(arr[0])[:600])
            print("  last :", json.dumps(arr[-1])[:600])
    else:
        print("  body:", cs)

    # --- trades (public tape, fallback) ---
    sc, tr = g("/markets/trades", ticker=tk, limit=5)
    print(f"\nGET /markets/trades -> {sc}")
    if isinstance(tr, dict):
        arr = tr.get("trades", [])
        print(f"  n={len(arr)}")
        if arr:
            print("  first:", json.dumps(arr[0])[:500])
    else:
        print("  body:", tr)


if __name__ == "__main__":
    main()
