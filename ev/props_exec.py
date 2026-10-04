"""
ev.props_exec -- NFL-prop executor: shadow (paper fills vs PRODUCTION Kalshi books) + optional DEMO orders.

Consumes the sweep's latest.json (consensus + Kalshi quotes, written every ~15 min), then for each
candidate re-checks against a FRESH production order book with the SAME gates, shrinkage, fractional Kelly,
risk caps and kill switch as the MLB executor (mlb.execution.kalshi.*), and:
  shadow  paper-fills against the real fetched ladder (the evidence ledger; graded on Kalshi's settlement)
  demo    (--demo-orders) sends the identical IOC order to the Kalshi DEMO exchange. NOTE (probed 2026-10-04):
          demo NFL-prop books are essentially empty (<3% two-sided), so expect NO_FILL there -- it exercises
          mechanics only. Production Kalshi is refused in code (client.ProductionNotEnabled).

  python -m ev.props_exec run  [--demo-orders] [--loop-seconds 120] [--until ISO]
  python -m ev.props_exec pnl            # grade shadow fills on Kalshi's own settled result
  python -m ev.props_exec status
One position per (game, player, stat) per day: strikes on the same player/stat are the same underlying bet.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ev.props_config import props_config
from mlb.execution.kalshi import gates
from mlb.execution.kalshi.client import PUBLIC_DATA_ENV, KalshiClient
from mlb.execution.kalshi.consensus import ConsPoint
from mlb.execution.kalshi.ledger import Ledger
from mlb.execution.kalshi.price_grid import PriceRange
from mlb.execution.kalshi.risk import RiskGate
from mlb.execution.kalshi.venue import KalshiVenue, OrderIntent, PaperVenue

log = logging.getLogger("ev.props_exec")
_ET = ZoneInfo("America/New_York")
LATEST_KEY = "EVSweep/nfl_props/latest.json"
DEFAULT_DB = os.path.expanduser("~/.kalshi_exec/ledger_nfl_props.sqlite")
_PREFILTER_SLACK = 0.02


@dataclass(frozen=True)
class ExecLeg:
    """Duck-typed for gates.evaluate / gates.intent_id (reads market, game_pk, line, start_utc, ...)."""
    ticker: str
    market: str
    game_pk: str
    line: float
    start_utc: datetime
    game_date: str
    ranges: tuple
    exchange_index: int | None
    selection_yes: str = "OVER"
    selection_no: str = "UNDER"
    group: str = ""


def et_day(now: datetime) -> str:
    return now.astimezone(_ET).date().isoformat()


def leg_from_row(r: dict) -> ExecLeg:
    ranges = tuple(PriceRange(float(a), float(b), float(c)) for a, b, c in json.loads(r.get("ranges") or "[]"))
    ex = r.get("exchange_index")
    ex = None if ex is None or ex != ex else int(ex)                 # NaN-safe
    return ExecLeg(
        ticker=r["ticker"], market=f"nfl_{r['stat']}:{r['player_norm']}", game_pk=r["game_key"], line=float(r["strike"]),
        start_utc=datetime.fromisoformat(r["commence"]), game_date=r["game_key"].split(":")[1], ranges=ranges,
        exchange_index=ex, group=f"{r['game_key']}|{r['player_norm']}|{r['stat']}")


def load_latest() -> dict | None:
    bucket = os.environ.get("MLB_GCS_BUCKET") or "concrete-crow-445205-m4-mlb-data"
    try:
        out = subprocess.run(["gcloud", "storage", "cat", f"gs://{bucket}/{LATEST_KEY}"],
                             capture_output=True, text=True, timeout=60, check=True).stdout
        return json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError) as e:
        log.warning("could not load %s: %r", LATEST_KEY, e)
        return None


def _prefilter(r: dict) -> bool:
    evs = [r.get("ev_yes"), r.get("ev_no")]
    return any(e is not None and e == e and e > -_PREFILTER_SLACK for e in evs)


def run_pass(*, latest: dict, cfg, data_client, paper: PaperVenue, ledger: Ledger, risk: RiskGate,
             demo_venue=None, demo_risk: RiskGate | None = None, clock=None) -> dict:
    clock = clock or (lambda: datetime.now(timezone.utc))
    day = et_day(clock())
    hist: Counter = Counter()
    executed = []
    cons_ts = datetime.fromisoformat(latest["ts"])
    rows = [r for r in latest.get("rows", []) if _prefilter(r)]
    rows.sort(key=lambda r: -max(x for x in (r.get("ev_yes"), r.get("ev_no")) if x is not None and x == x))
    for r in rows:
        if risk.killed:
            hist["REJ_KILLED"] += 1
            break
        leg = leg_from_row(r)
        if ledger.kv_get(f"grp:{day}:{leg.group}") is not None:
            hist["REJ_GROUP_HELD"] += 1                              # already hold this player/stat today
            continue
        cons = ConsPoint(p=float(r["p_over"]), n_books=int(r["n_books"]), disp=float(r["disp"]),
                         ts=cons_ts, prev_p=None)
        try:
            book = data_client.get_orderbook(leg.ticker)
        except Exception as e:                                                       # noqa: BLE001
            log.error("orderbook %s failed: %r", leg.ticker, e)
            hist["ERR_FETCH"] += 1
            continue
        now_eval = clock()
        cash = cfg.bankroll
        budget = risk.budget_cap(leg.game_pk, day, cash)
        d = gates.evaluate(leg, book, cons, None, now_eval, cfg, cfg.bankroll, budget)
        if d.outcome == "PLAN":
            if ledger.has_intent(d.intent_id):
                d.outcome, d.reason = "REJ_DUP", "intent already ordered today"
            else:
                ok, why = risk.check_order(d.plan.stake, leg.game_pk, day, cash)
                if not ok:
                    d.outcome, d.reason = why, "risk gate"
        ack = None
        if d.outcome == "PLAN":
            intent = OrderIntent(d.intent_id, leg.ticker, d.side, d.plan.contracts, d.plan.limit_price,
                                 leg.ranges, leg.exchange_index, fee_multiplier=float(r.get("fee_mult") or 1.0))
            ack = paper.submit(intent)
            risk.note_submit_ok()
            d.outcome = {"FILLED": "EXECUTED", "PARTIAL": "PARTIAL", "NO_FILL": "NO_FILL"}[ack.status]
            ledger.record_order(day=day, mode="shadow", intent_id=d.intent_id, client_order_id=ack.client_order_id,
                                order_id=ack.order_id, ticker=leg.ticker, game_pk=leg.game_pk, side=d.side,
                                contracts=d.plan.contracts, limit_price=d.plan.limit_price, status=ack.status,
                                fill_count=ack.fill_count, filled_cost=ack.filled_cost, fee=ack.fee, raw=ack.raw)
            for f in ack.fills:
                ledger.record_fill(fill_id=f.fill_id, order_id=f.order_id, ticker=f.ticker, side=f.side,
                                   count=f.count, price=f.price, fee=f.fee, ts=f.ts, mode="shadow")
            if ack.fill_count > 0:
                ledger.kv_set(f"grp:{day}:{leg.group}", d.intent_id)
            if demo_venue is not None and demo_risk is not None and not demo_risk.killed:
                _demo_attempt(demo_venue, demo_risk, ledger, day, leg, d, float(r.get("fee_mult") or 1.0))
        ledger.record_decision(
            day=day, mode="shadow", outcome=d.outcome, reason=d.reason, intent_id=d.intent_id, ticker=leg.ticker,
            series="KXNFLPROP", market=leg.market, game_pk=leg.game_pk, side=d.side, p_cons=d.p_cons,
            p_hat=d.p_hat, n_books=d.n_books, ask=d.ask, ev=d.plan.ev if d.plan else None,
            contracts=d.plan.contracts if d.plan else None, limit_price=d.plan.limit_price if d.plan else None,
            lag_class=d.lag_class, inputs=dict(d.inputs, player=r["player"], stat=r["stat"]))
        hist[d.outcome] += 1
        if d.plan is not None and d.outcome in ("EXECUTED", "PARTIAL", "NO_FILL"):
            executed.append({"player": r["player"], "stat": r["stat"], "strike": r["strike"], "side": d.side,
                             "ask": d.ask, "fair": d.p_cons, "ev": d.plan.ev, "n": d.plan.contracts,
                             "filled": ack.fill_count if ack else 0.0, "cost": ack.filled_cost if ack else 0.0,
                             "outcome": d.outcome, "ticker": leg.ticker})
    return {"day": day, "considered": len(rows), "hist": dict(hist), "executed": executed,
            "killed": risk.killed}


def _demo_attempt(demo_venue, demo_risk, ledger, day, leg, d, mult) -> None:
    did = "d-" + d.intent_id
    try:
        ack = demo_venue.submit(OrderIntent(did, leg.ticker, d.side, d.plan.contracts, d.plan.limit_price,
                                            leg.ranges, leg.exchange_index, fee_multiplier=mult))
    except Exception as e:                                                           # noqa: BLE001
        demo_risk.note_submit_error()
        log.warning("demo submit raised: %r", e)
        return
    (demo_risk.note_submit_error if ack.status == "ERROR" else demo_risk.note_submit_ok)()
    ledger.record_order(day=day, mode="demo", intent_id=did, client_order_id=ack.client_order_id, order_id=ack.order_id,
                        ticker=leg.ticker, game_pk=leg.game_pk, side=d.side, contracts=d.plan.contracts,
                        limit_price=d.plan.limit_price, status=ack.status, fill_count=ack.fill_count,
                        filled_cost=ack.filled_cost, fee=ack.fee, raw=ack.raw)
    log.info("DEMO %s %s x%s @<=%s -> %s %s", leg.ticker, d.side, d.plan.contracts, d.plan.limit_price, ack.status,
             ack.error or "")


def grade(ledger: Ledger, client: KalshiClient, mode: str = "shadow") -> dict:
    """Shadow P&L on Kalshi's OWN settled result. open = not yet finalized."""
    with ledger._lock:
        rows = ledger._c.execute(
            "SELECT ticker, side, fill_count, filled_cost, day FROM orders WHERE mode=? AND fill_count>0",
            (mode,)).fetchall()
    tot = {"orders": len(rows), "settled": 0, "open": 0, "wins": 0, "staked": 0.0, "pnl": 0.0, "open_cost": 0.0}
    for ticker, side, n, cost, _day in map(tuple, rows):
        m = client.get_market(ticker)
        res = (m.get("result") or "").lower()
        if res not in ("yes", "no"):
            tot["open"] += 1
            tot["open_cost"] += cost
            continue
        won = res == side
        tot["settled"] += 1
        tot["wins"] += int(won)
        tot["staked"] += cost
        tot["pnl"] += (n if won else 0.0) - cost
    tot["roi"] = round(tot["pnl"] / tot["staked"], 4) if tot["staked"] else None
    return tot


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NFL props executor (shadow + demo)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--demo-orders", action="store_true")
    r.add_argument("--env-file", default=None)
    r.add_argument("--db", default=DEFAULT_DB)
    r.add_argument("--loop-seconds", type=int, default=0)
    r.add_argument("--until", default="")
    for name in ("pnl", "status"):
        p = sub.add_parser(name)
        p.add_argument("--db", default=DEFAULT_DB)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = props_config()
    os.makedirs(os.path.dirname(a.db), exist_ok=True)
    ledger = Ledger(a.db)
    data_client = KalshiClient(PUBLIC_DATA_ENV, None, None)

    if a.cmd == "pnl":
        print(json.dumps(grade(ledger, data_client), indent=1))
        return 0
    if a.cmd == "status":
        day = et_day(datetime.now(timezone.utc))
        g = RiskGate(cfg, ledger, "shadow")
        print(f"killed={g.killed} reason={g.kill_reason} | notional bankroll ${cfg.bankroll:.0f} caps: per_bet "
              f"${cfg.per_bet_cap:.0f} per_game ${cfg.per_game_cap:.0f} daily ${cfg.daily_deploy_cap:.0f}")
        for m in ("shadow", "demo"):
            print(f"{m}: orders={ledger.orders_today(day, m)} deployed=${ledger.deployed_today(day, m):.2f} "
                  f"hist={ledger.histogram(day, m)}")
        return 0

    paper = PaperVenue(data_client.get_orderbook, cfg.taker_multiplier)
    risk = RiskGate(cfg, ledger, "shadow")
    demo_venue = demo_risk = None
    if a.demo_orders:
        from mlb.execution.kalshi.runner import load_demo_client
        demo_venue = KalshiVenue(load_demo_client(a.env_file))
        demo_risk = RiskGate(cfg, ledger, "demo")
    until = datetime.fromisoformat(a.until.replace("Z", "+00:00")) if a.until else None
    while True:
        latest = load_latest()
        if latest:
            try:
                s = run_pass(latest=latest, cfg=cfg, data_client=data_client, paper=paper, ledger=ledger, risk=risk,
                             demo_venue=demo_venue, demo_risk=demo_risk)
                log.info("PASS considered=%s hist=%s executed=%s", s["considered"], s["hist"], len(s["executed"]))
                for e in s["executed"]:
                    log.info("  %s", e)
            except Exception:                                                        # noqa: BLE001
                log.exception("pass failed; retrying next cycle")
        if not a.loop_seconds or (until and datetime.now(timezone.utc) >= until) or risk.killed:
            return 1 if risk.killed else 0
        sys.stdout.flush()
        time.sleep(a.loop_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
