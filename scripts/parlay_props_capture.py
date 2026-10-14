"""parlay_props_capture.py -- capture ONE NFL event's raw prop payload (all planned markets) to GCS and
print the per-market shape (books, outcome fields, alternate ladders). ~12 credits. Never prints the key.
Run via the job + GCS pattern (see parlay_probe.py)."""
import json
import os
from datetime import datetime, timezone

import requests

BASE = os.environ.get("PARLAY_API_BASE", "https://parlay-api.com/v1").rstrip("/")
KEY = os.environ["PARLAY_API_KEY"]
SPORT = "americanfootball_nfl"
MARKETS = ["player_pass_yds", "player_pass_yds_alternate", "player_rush_yds", "player_rush_yds_alternate",
           "player_reception_yds", "player_reception_yds_alternate", "player_receptions",
           "player_receptions_alternate", "player_pass_tds", "player_anytime_td", "player_1st_td"]


def get(path, **p):
    r = requests.get(f"{BASE}/{path}", params=p, headers={"X-API-Key": KEY}, timeout=60)
    print(f"GET {path} -> {r.status_code} last={r.headers.get('x-requests-last')} rem={r.headers.get('x-requests-remaining')}", flush=True)
    return r.json() if r.status_code == 200 else None


slate = get(f"sports/{SPORT}/odds", regions="us", markets="h2h", oddsFormat="american") or []
now = datetime.now(timezone.utc).isoformat()
up = sorted((e for e in slate if e.get("commence_time", "") > now), key=lambda e: e["commence_time"])
print("upcoming events:", [(e["commence_time"], e["away_team"], e["home_team"]) for e in up[:20]])
ev = up[0]
print("capturing", ev["id"], ev["away_team"], "@", ev["home_team"], ev["commence_time"])
obj = get(f"sports/{SPORT}/events/{ev['id']}/odds", regions="us", markets=",".join(MARKETS), oddsFormat="american")
blob = json.dumps({"slate_event": ev, "props": obj}, indent=1)
try:
    from mlb_core.storage import write_bytes
    write_bytes(blob.encode(), f"probes/props_capture_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json")
except Exception as exc:
    print("GCS write skipped:", exc)
if obj:
    for b in obj.get("bookmakers", []):
        for m in b.get("markets", []):
            outs = m.get("outcomes", [])
            pts = sorted({o.get("point") for o in outs if o.get("point") is not None})
            print(f"{b['key']:12s} {m['key']:34s} n_out={len(outs):4d} n_points={len(pts):3d} "
                  f"names={sorted({o.get('name') for o in outs})[:4]} sample={json.dumps(outs[:2])[:230]}")
