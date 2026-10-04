"""parlay_catalog_probe.py -- list ParlayAPI market keys per sport (catalog endpoints are expected to be
free). Looks for alternate spread/total keys under ANY name. Never prints the key.
Run as a Cloud Run job via GCS (see parlay_probe.py)."""
import json
import os
import requests

BASE = os.environ.get("PARLAY_API_BASE", "https://parlay-api.com/v1").rstrip("/")
KEY = os.environ["PARLAY_API_KEY"]
SPORTS = ["americanfootball_nfl", "icehockey_nhl", "basketball_nba"]


def get(path, **p):
    r = requests.get(f"{BASE}/{path}", params=p, headers={"X-API-Key": KEY}, timeout=60)
    try:
        body = r.json()
    except ValueError:
        body = r.text[:300]
    print(f"GET {path} {p} -> {r.status_code} last={r.headers.get('x-requests-last')} "
          f"rem={r.headers.get('x-requests-remaining')}", flush=True)
    return r.status_code, body


def keys_in(obj, acc):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("key", "market", "market_key") and isinstance(v, str):
                acc.add(v)
            keys_in(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            keys_in(v, acc)
        for v in obj:
            if isinstance(v, str):
                acc.add(v)


for path, params in [("props/markets", {}), ("markets", {})] + [
        (f"sports/{s}/markets", {}) for s in SPORTS] + [("props/markets", {"sport": s}) for s in SPORTS]:
    st, body = get(path, **params)
    acc = set()
    keys_in(body, acc)
    interesting = sorted(k for k in acc if any(t in k.lower() for t in ("alt", "spread", "total", "handicap", "line")))
    print("  type", type(body).__name__, "n_keys", len(acc), "interesting", interesting[:60])
    if st == 200 and not acc:
        print("  raw", json.dumps(body)[:400])
