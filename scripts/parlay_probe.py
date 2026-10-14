"""
parlay_probe.py -- one-off ParlayAPI coverage + credit-cost probe for NFL / NBA / NHL.

Answers (docs/designs/multisport_ev_sweep.md s7 Q1): sport keys, which books per sport, shape of
spreads/totals, whether ALTERNATE spread/total markets exist and what they cost, and the plan's
credit headroom. Spends only a handful of credits and logs the cost of every call. Never prints the key.

Run as a Cloud Run job (office LAN blocks gambling sites); the script is pulled from GCS so the
production image does not need a rebuild:
  python3 -c 'from mlb_core.storage import read_bytes; exec(read_bytes("probes/parlay_probe.py").decode())'
Results are also written to GCS probes/parlay_probe_<ts>.json.
"""
import json
import os
import time
from datetime import datetime, timezone

import requests

BASE = os.environ.get("PARLAY_API_BASE", "https://parlay-api.com/v1").rstrip("/")
KEY = os.environ["PARLAY_API_KEY"]
SPORTS = ["americanfootball_nfl", "icehockey_nhl", "basketball_nba"]
LOG = []


def call(path, **params):
    """GET with the shared header scheme. Returns (status, json|None, credits_last, credits_remaining)."""
    r = requests.get(f"{BASE}/{path.lstrip('/')}", params=params, headers={"X-API-Key": KEY}, timeout=60)
    last, rem = r.headers.get("x-requests-last"), r.headers.get("x-requests-remaining")
    try:
        body = r.json()
    except ValueError:
        body = None
    LOG.append({"path": path, "params": params, "status": r.status_code, "credits_last": last, "remaining": rem})
    print(f"GET {path} {params} -> {r.status_code} credits_last={last} remaining={rem}", flush=True)
    time.sleep(0.3)
    return r.status_code, body, last, rem


def books_of(event):
    return sorted({b.get("key") for b in event.get("bookmakers", [])})


def market_keys(event):
    return sorted({m.get("key") for b in event.get("bookmakers", []) for m in b.get("markets", [])})


def main():
    out = {"ts": datetime.now(timezone.utc).isoformat(), "base": BASE, "sports": {}}

    # 1) catalog (should be free); look for the three sports' keys
    st, body, last, rem = call("sports")
    out["start_remaining"] = rem
    if isinstance(body, list):
        out["catalog_matches"] = [s for s in body if any(
            k in json.dumps(s).lower() for k in ("nfl", "nhl", "nba", "football", "hockey", "basketball"))][:30]
    else:
        out["catalog_raw"] = str(body)[:500]

    for sport in SPORTS:
        res = {}
        # 2) moneyline slate (1 credit expected)
        st, ev, last, rem = call(f"sports/{sport}/odds", regions="us", markets="h2h", oddsFormat="american")
        res["h2h"] = {"status": st, "credits_last": last, "n_events": len(ev) if isinstance(ev, list) else None}
        if not (isinstance(ev, list) and ev):
            out["sports"][sport] = res
            continue
        res["h2h"]["sample_event"] = {k: ev[0].get(k) for k in ("id", "commence_time", "home_team", "away_team")}
        res["h2h"]["books_union"] = sorted({b for e in ev for b in books_of(e)})
        res["h2h"]["books_per_event_min_max"] = [min(len(books_of(e)) for e in ev), max(len(books_of(e)) for e in ev)]
        res["h2h"]["sample_teams"] = sorted({e.get("home_team") for e in ev} | {e.get("away_team") for e in ev})[:40]

        # 3) main spreads + totals (2 credits expected)
        st, ev2, last, rem = call(f"sports/{sport}/odds", regions="us", markets="spreads,totals", oddsFormat="american")
        res["spreads_totals"] = {"status": st, "credits_last": last,
                                 "n_events": len(ev2) if isinstance(ev2, list) else None}
        if isinstance(ev2, list) and ev2:
            e0 = ev2[0]
            res["spreads_totals"]["markets_in_event0"] = market_keys(e0)
            res["spreads_totals"]["books_event0"] = books_of(e0)
            # one book's raw outcomes so the line/side shape is documented
            for b in e0.get("bookmakers", []):
                if b.get("key") in ("pinnacle", "draftkings", "fanduel"):
                    res["spreads_totals"]["sample_book"] = {"book": b["key"], "markets": b.get("markets")}
                    break

        # 4) alternate lines on ONE event (cost + availability)
        eid = ev[0]["id"]
        st, alt, last, rem = call(f"sports/{sport}/events/{eid}/odds", regions="us",
                                  markets="alternate_spreads,alternate_totals", oddsFormat="american")
        alt_res = {"status": st, "credits_last": last}
        if isinstance(alt, dict):
            alt_res["markets"] = market_keys(alt)
            alt_res["books"] = books_of(alt)
            per = {}
            for b in alt.get("bookmakers", []):
                for m in b.get("markets", []):
                    per[f"{b['key']}:{m['key']}"] = len(m.get("outcomes", []))
            alt_res["outcomes_per_book_market"] = per
        res["alt_event_endpoint"] = alt_res

        # 5) does the whole-slate endpoint serve alternates too, and at what price?
        st, alt2, last, rem = call(f"sports/{sport}/odds", regions="us", markets="alternate_spreads",
                                   oddsFormat="american")
        res["alt_slate_endpoint"] = {"status": st, "credits_last": last,
                                     "n_events": len(alt2) if isinstance(alt2, list) else None}
        out["sports"][sport] = res

    out["end_remaining"] = LOG[-1]["remaining"] if LOG else None
    out["calls"] = LOG
    blob = json.dumps(out, indent=1, default=str)
    print("=====RESULT_JSON_BEGIN=====")
    print(blob)
    print("=====RESULT_JSON_END=====")
    try:
        from mlb_core.storage import write_bytes
        write_bytes(blob.encode(), f"probes/parlay_probe_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json")
    except Exception as exc:  # the log copy is enough
        print("GCS write skipped:", exc)


main()
