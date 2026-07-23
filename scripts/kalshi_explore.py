#!/usr/bin/env python3
"""One-off Kalshi market discovery.

Enumerates Kalshi's baseball/MLB series -> events -> markets and reports, per
market group: count, liquidity, volume, open interest, and sample titles. Then
maps each group onto our beezy MLB systems (HR, NRFI, F5, K, OUTS, GAME, ...)
so we can see where Kalshi coverage overlaps what we already model.

Public market-data endpoints only -- NO auth required, NO api key used here.
(Trading/portfolio calls need the API-key-ID + RSA private key; not needed to look.)

Run from Cloud Shell (sandbox is firewalled):
    cd ~/mlb-betting && python3 scripts/kalshi_explore.py

Writes a raw dump to /tmp/kalshi_explore_<UTC-day>.json for follow-up work.
"""
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    sys.exit("pip install requests  (Cloud Shell has it by default)")

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "beezy-kalshi-explore/1.0"

# Keywords that flag a series/market as baseball-relevant.
MLB_HINTS = ("mlb", "baseball", "home run", "homerun", "strikeout", "strike out",
             "total bases", " hits", "runs", "inning", "first to score",
             "world series", "pennant", "al ", "nl ")

# Map Kalshi market text -> our system(s). First match wins per (kw, system).
SYSTEM_KEYWORDS = {
    "HR":          ("home run", "homerun", "to hit a hr", "hits a home run"),
    "NRFI/1I":     ("first inning", "1st inning", "no runs first", "run in the 1st"),
    "F5":          ("first 5 innings", "first five innings", "5 inning", "f5"),
    "K":           ("strikeout", "strike out", "ks ", "pitcher k"),
    "OUTS":        ("outs recorded", "innings pitched"),
    "GAME_ML":     ("to win", "moneyline", "beat the", "winner", "wins the game"),
    "BATTER_TB":   ("total bases",),
    "BATTER_HITS": ("hits ", "record a hit", "get a hit"),
    "PITCHER_ER":  ("earned runs", "runs allowed"),
    "TOTALS":      ("total runs", "over/under", "combined runs"),
}


def get(path, **params):
    for attempt in range(4):
        r = SESSION.get(f"{BASE}{path}", params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code >= 400:
            print(f"  ! {r.status_code} {path} {params} -> {r.text[:200]}")
            return None
        return r.json()
    return None


def paginate(path, key, cap_pages=60, **params):
    """Yield every item under `key`, following Kalshi's `cursor`."""
    cursor = None
    pages = 0
    while pages < cap_pages:
        p = dict(params)
        if cursor:
            p["cursor"] = cursor
        data = get(path, **p)
        if not data:
            break
        items = data.get(key, [])
        for it in items:
            yield it
        cursor = data.get("cursor")
        pages += 1
        if not cursor or not items:
            break


def is_mlb(*texts):
    blob = " ".join(t for t in texts if t).lower()
    return any(h in blob for h in MLB_HINTS)


def classify(title, subtitle=""):
    blob = f"{title} {subtitle}".lower()
    hits = [sys_ for sys_, kws in SYSTEM_KEYWORDS.items()
            if any(k in blob for k in kws)]
    return hits or ["(unmapped)"]


def main():
    print(f"BASE={BASE}")
    status = get("/exchange/status")
    print(f"exchange/status -> {status}\n")

    # --- 1. Find baseball series -----------------------------------------
    # Try category filter first; fall back to unfiltered listing.
    series = []
    for cat in ("Sports", None):
        params = {"category": cat} if cat else {}
        got = list(paginate("/series", "series", **params))
        if got:
            print(f"/series category={cat!r}: {len(got)} series")
            series = got
            break

    mlb_series = [s for s in series
                  if is_mlb(s.get("ticker", ""), s.get("title", ""),
                            s.get("category", ""), s.get("sub_title", ""))]
    print(f"\nMLB-relevant series: {len(mlb_series)}")
    for s in mlb_series:
        print(f"  {s.get('ticker'):20} {s.get('title','')}")

    if not mlb_series:
        print("\nNo series matched via /series. Falling back to open-events scan...")
        ev_by_series = defaultdict(list)
        for ev in paginate("/events", "events", status="open", cap_pages=80):
            if is_mlb(ev.get("series_ticker", ""), ev.get("title", ""),
                      ev.get("category", "")):
                ev_by_series[ev.get("series_ticker", "?")].append(ev)
        for st, evs in sorted(ev_by_series.items()):
            print(f"  {st:20} {len(evs):4} open events  e.g. {evs[0].get('title','')}")
        mlb_series = [{"ticker": st} for st in ev_by_series]

    # --- 2. Pull markets per MLB series, tally liquidity -----------------
    dump = {"generated_utc": datetime.now(timezone.utc).isoformat(),
            "base": BASE, "series": [], "system_rollup": {}}
    sys_rollup = defaultdict(lambda: {"markets": 0, "volume": 0,
                                      "open_interest": 0, "series": set()})

    for s in mlb_series:
        st = s.get("ticker")
        markets = list(paginate("/markets", "markets", series_ticker=st,
                                 status="open", limit=1000))
        if not markets:
            # some deployments key markets by event, not series
            markets = list(paginate("/markets", "markets", series_ticker=st,
                                     limit=1000))
        n = len(markets)
        vol = sum(m.get("volume", 0) or 0 for m in markets)
        oi = sum(m.get("open_interest", 0) or 0 for m in markets)
        liqs = [m.get("liquidity", 0) or 0 for m in markets]
        avg_liq = round(sum(liqs) / n, 1) if n else 0

        by_system = defaultdict(int)
        samples = []
        for m in markets:
            for sys_ in classify(m.get("title", ""), m.get("subtitle", "")):
                by_system[sys_] += 1
                r = sys_rollup[sys_]
                r["markets"] += 1
                r["volume"] += m.get("volume", 0) or 0
                r["open_interest"] += m.get("open_interest", 0) or 0
                r["series"].add(st)
            if len(samples) < 6:
                samples.append({
                    "ticker": m.get("ticker"),
                    "title": m.get("title"),
                    "subtitle": m.get("subtitle"),
                    "yes_bid": m.get("yes_bid"), "yes_ask": m.get("yes_ask"),
                    "volume": m.get("volume"), "open_interest": m.get("open_interest"),
                    "liquidity": m.get("liquidity"),
                    "close_time": m.get("close_time"),
                })

        print(f"\n=== {st} : {n} open markets | vol={vol} oi={oi} avg_liq={avg_liq}")
        for sys_, c in sorted(by_system.items(), key=lambda x: -x[1]):
            print(f"    {sys_:14} {c}")
        for ex in samples[:4]:
            print(f"    e.g. {ex['ticker']}: {ex['title']} / {ex['subtitle']} "
                  f"[{ex['yes_bid']}/{ex['yes_ask']} vol={ex['volume']}]")

        dump["series"].append({"ticker": st, "title": s.get("title"),
                               "n_markets": n, "volume": vol, "open_interest": oi,
                               "avg_liquidity": avg_liq,
                               "by_system": dict(by_system), "samples": samples})

    # --- 3. System coverage rollup ---------------------------------------
    print("\n" + "=" * 60)
    print("COVERAGE ROLLUP  (Kalshi markets mapped to beezy systems)")
    print("=" * 60)
    for sys_, r in sorted(sys_rollup.items(), key=lambda x: -x[1]["markets"]):
        print(f"  {sys_:14} markets={r['markets']:5}  vol={r['volume']:8}  "
              f"oi={r['open_interest']:8}  series={sorted(r['series'])}")
        dump["system_rollup"][sys_] = {"markets": r["markets"], "volume": r["volume"],
                                       "open_interest": r["open_interest"],
                                       "series": sorted(r["series"])}

    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = f"/tmp/kalshi_explore_{day}.json"
    with open(out, "w") as f:
        json.dump(dump, f, indent=2, default=str)
    print(f"\nraw dump -> {out}")


if __name__ == "__main__":
    main()
