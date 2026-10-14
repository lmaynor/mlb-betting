"""novig_kalshi_scan.py -- read-only cross-venue scan: Novig (public catalog, no key) vs Kalshi (public) on the SAME
NFL player prop (game, player, stat, line). Prices: Novig pre-game taker fee is 0 (WHEN_LIVE); Kalshi fee modelled.
  riskless gap A: buy Novig Over + Kalshi NO   | B: buy Novig Under + Kalshi YES  (margin = 1 - total cost)
Hedged cross-venue scalp (docs/analysis/market_making_2026-10-12.md s9 shadow test #1) -- one leg RESTS at the best
bid (joins the queue; no fill is assumed), the other is taken from the opposite venue's ask when it fills:
  mm_kalyes_novunder = 1 - (kal_bid_yes + nov_ask_under)   Kalshi maker YES + Novig taker Under   (no fee either leg)
  mm_kalno_novover   = 1 - (kal_bid_no  + nov_ask_over)    Kalshi maker NO  + Novig taker Over    (no fee either leg)
  mm_novover_kalno   = 1 - (nov_bid_over  + kal_ask_no  + kalshi taker fee)   Novig maker Over  + Kalshi taker NO
  mm_novunder_kalyes = 1 - (nov_bid_under + kal_ask_yes + kalshi taker fee)   Novig maker Under + Kalshi taker YES
Margins are GROSS OF ANY KALSHI MAKER FEE (not modelled) and assume the resting leg fills; they measure the quoted
opportunity, not realised P&L (adverse selection / queue position are what the shadow test must estimate).
Never places an order. Paced to respect Novig's public rate limit."""
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

from ev.novig.client import NovigClient
from ev.novig.map import NovigLeg, event_index, map_prop_market
from ev.sweep_nfl_props import fetch_kalshi_legs
from mlb.execution.kalshi.client import PUBLIC_DATA_ENV, KalshiClient
from mlb.execution.kalshi.fees import taker_fee

OUT = sys.argv[1] if len(sys.argv) > 1 else "novig_kalshi_scan.json"
nc = NovigClient("public-data")
kc = KalshiClient(PUBLIC_DATA_ENV, None, None)
now = datetime.now(timezone.utc)

events = event_index(nc.list_events("NFL"))
markets = nc.list_markets("NFL")
nov = {}
for m in markets:
    leg = map_prop_market(m, events)
    if isinstance(leg, NovigLeg) and leg.start_utc > now:
        nov[(leg.game_key, leg.stat, leg.player_norm, leg.strike)] = leg
print(f"novig prop legs (future games): {len(nov)}", flush=True)

klegs, mult, _ = fetch_kalshi_legs(kc)
kal = {}
for gk, lst in klegs.items():
    for leg, mk in lst:
        kal[(gk, leg.stat, leg.player_norm, leg.strike)] = (leg, mk)
print(f"kalshi prop legs: {len(kal)} across {len(klegs)} games | fee mult {mult}", flush=True)

keys = sorted(set(nov) & set(kal))
print(f"matched on (game, player, stat, line): {len(keys)}", flush=True)
f = lambda x: float(x) if x not in (None, "") else None
rows = []
for i, k in enumerate(keys):
    nl = nov[k]
    leg, mk = kal[k]
    try:
        b = nc.get_book(nl.market_id, depth=5)
    except Exception as e:
        print("book fail", nl.description, str(e)[:80], flush=True)
        time.sleep(2)
        continue
    time.sleep(0.7)
    a_over = b.asks(nl.over_id, nl.under_id)
    a_under = b.asks(nl.under_id, nl.over_id)
    n_over = float(a_over[0][0]) if a_over else None
    n_under = float(a_under[0][0]) if a_under else None
    n_over_sz = a_over[0][1] if a_over else 0
    n_under_sz = a_under[0][1] if a_under else 0
    ky, kn = f(mk.get("yes_ask_dollars")), f(mk.get("no_ask_dollars"))
    kby = f(mk.get("yes_bid_dollars")) or (None if kn is None else round(1 - kn, 4))     # bid YES = 1 - ask NO
    kbn = f(mk.get("no_bid_dollars")) or (None if ky is None else round(1 - ky, 4))
    nbo, nbu = b.best_bid(nl.over_id), b.best_bid(nl.under_id)
    nbo = None if nbo is None else float(nbo)
    nbu = None if nbu is None else float(nbu)
    ky_sz, kn_sz = f(mk.get("yes_ask_size_fp")), f(mk.get("no_ask_size_fp"))
    def kfee(p):
        return taker_fee(p, 100, mult) / 100 if p else None
    A = None if n_over is None or kn is None else 1 - (n_over + kn + kfee(kn))        # Novig Over + Kalshi NO
    B = None if n_under is None or ky is None else 1 - (n_under + ky + kfee(ky))      # Novig Under + Kalshi YES
    gA = None if n_over is None or kn is None else 1 - (n_over + kn)                   # gross of Kalshi fee
    gB = None if n_under is None or ky is None else 1 - (n_under + ky)
    def mm(cost):
        return None if cost is None else round(1 - cost, 4)
    def plus(*xs):
        return None if any(x is None for x in xs) else sum(xs)
    rows.append({"game": k[0], "stat": k[1], "player": nl.player, "line": k[3], "nov_ask_over": n_over,
                 "nov_ask_under": n_under, "nov_sz_over": n_over_sz, "nov_sz_under": n_under_sz,
                 "kal_ask_yes": ky, "kal_ask_no": kn, "arbA_net": A, "arbB_net": B, "arbA_gross": gA, "arbB_gross": gB,
                 "kal_bid_yes": kby, "kal_bid_no": kbn, "nov_bid_over": nbo, "nov_bid_under": nbu,
                 "mm_kalyes_novunder": mm(plus(kby, n_under)), "mm_kalno_novover": mm(plus(kbn, n_over)),
                 "mm_novover_kalno": mm(plus(nbo, kn, kfee(kn))), "mm_novunder_kalyes": mm(plus(nbu, ky, kfee(ky))),
                 "kal_ticker": leg.ticker, "nov_market": nl.market_id})
    if (i + 1) % 25 == 0:
        print(f"  {i+1}/{len(keys)} books", flush=True)
json.dump(rows, open(OUT, "w"), default=str)
print("done", len(rows), flush=True)
