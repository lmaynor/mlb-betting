"""
ev.props_exec -- NFL-prop executor: shadow (paper fills vs PRODUCTION Kalshi books) + optional DEMO orders.

Consumes the sweep's latest.json (consensus + Kalshi quotes, written every ~15 min), then for each
candidate re-checks against a FRESH production order book with the SAME gates, shrinkage, fractional Kelly,
risk caps and kill switch as the MLB executor (mlb.execution.kalshi.*), and:
  shadow  paper-fills against the real fetched ladder (the evidence ledger; graded on Kalshi's settlement)
  demo    (--demo-orders) sends the identical IOC order to the Kalshi DEMO exchange. NOTE (probed 2026-10-04):
          demo NFL-prop books are essentially empty (<3% two-sided), so expect NO_FILL there -- it exercises
          mechanics only. Production Kalshi is refused in code (client.ProductionNotEnabled).

  python -m ev.props_exec run  [--demo-orders] [--novig [--novig-paper-orders]] [--loop-seconds 120] [--until ISO]
  --novig-paper-orders mirrors each Novig PLAN (decided on the PRODUCTION public book) as a signed IOC order on the
  Novig PAPER sandbox with the trading key in ~/.novig/paper.env -- plumbing validation, never edge (paper books
  are synthetic). Requires --novig; production Novig is refused in code.
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
PREFIX = "EVSweep/nfl_props"
LATEST_KEY = f"{PREFIX}/latest.json"
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


def _bucket() -> str:
    return os.environ.get("MLB_GCS_BUCKET") or "concrete-crow-445205-m4-mlb-data"


def gcs_cat(key: str) -> bytes | None:
    try:
        return subprocess.run(["gcloud", "storage", "cat", f"gs://{_bucket()}/{key}"], capture_output=True,
                              timeout=90, check=True).stdout
    except subprocess.SubprocessError:
        return None


def gcs_put(key: str, data: bytes) -> bool:
    try:
        subprocess.run(["gcloud", "storage", "cp", "-", f"gs://{_bucket()}/{key}"], input=data,
                       capture_output=True, timeout=90, check=True)
        return True
    except subprocess.SubprocessError as e:
        log.warning("gcs put %s failed: %r", key, e)
        return False


def load_latest() -> dict | None:
    raw = gcs_cat(LATEST_KEY)
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


def build_latest_local(kc: KalshiClient, cfg) -> dict | None:
    """Join the cloud's published book consensus to Kalshi from THIS machine (Kalshi's CDN blocks some Cloud Run
    egress IPs; the laptop reaches it reliably). -> {'ts', 'rows', 'stats'} or None if no consensus yet."""
    from ev.sweep_nfl_props import fetch_kalshi_legs, rows_from_snapshot
    raw = gcs_cat(f"{PREFIX}/consensus.json")
    if not raw:
        return None
    snap = json.loads(raw)
    legs, mult, _ = fetch_kalshi_legs(kc)
    now = datetime.now(timezone.utc)
    rows, stats = rows_from_snapshot(snap["events"], legs, now, cfg, mult)
    # Pooled (adjacent-half-point) consensus is EVIDENCE ONLY, and the cloud sweep skips it whenever Kalshi blocks its
    # egress IP (kalshi_ok=False) -- this local join is then the only place it can be collected. Never fatal.
    pooled, pstats = [], {}
    try:
        from ev.sweep_nfl_props import pooled_rows_from_snapshot
        pooled, pstats = pooled_rows_from_snapshot(snap["events"], legs, now, cfg, mult)
    except Exception:                                                                # noqa: BLE001
        log.exception("local pooled-consensus evidence failed; continuing without it")
    return {"ts": snap["ts"], "rows": rows, "stats": {k: v for k, v in stats.items() if k != "books_per_match"},
            "books_hist": dict(Counter(stats["books_per_match"])), "snap": snap["events"],
            "pooled": pooled, "pool_stats": pstats}


def publish_local(latest: dict, day: str, extra_alerts: list | None = None) -> int:
    """Evidence + alerts: (1) gz CSV of every comparable row this pass (Kalshi quote vs consensus time series);
    (2) flagged candidates not yet notified -> alerts_pending.json (the cloud job holds the Discord webhook and
    posts them). Returns the number queued."""
    import gzip
    import io

    import pandas as pd

    def _put_gz(rows_, name):
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(pd.DataFrame(rows_).to_csv(index=False).encode())
        gcs_put(f"{PREFIX}/{day}/{name}_{datetime.now(timezone.utc):%H%M%S}.csv.gz", buf.getvalue())

    rows = latest["rows"]
    if rows:
        _put_gz(rows, "local")
    if latest.get("pooled"):
        _put_gz(latest["pooled"], "local_pooled")
    flagged = [r for r in rows if r.get("flag")] + list(extra_alerts or [])
    if not flagged:
        return 0
    raw = gcs_cat(f"{PREFIX}/{day}/notified.json")
    done = set(json.loads(raw)) if raw else set()
    new = [r for r in flagged if f"{r['ticker']}|{r['side']}" not in done]
    if new:
        gcs_put(f"{PREFIX}/alerts_pending.json", json.dumps(new, default=str).encode())
    return len(new)


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


def load_novig_paper_venue(env_file: str | None = None):
    """Paper-sandbox venue from the trading key in ~/.novig/paper.env. Preflights with a signed GET (positions) so a
    bad key/scope fails at startup, not mid-pass. Exits with a clear message instead of a traceback."""
    from ev.novig import setup as nsetup
    from ev.novig.client import NovigAPIError
    from ev.novig.paper_orders import NovigPaperVenue
    client = nsetup.trading_client(nsetup.read_env(env_file))
    try:
        client.positions()
    except NovigAPIError as e:
        raise SystemExit(f"Novig paper trading key preflight failed: {e}")
    return NovigPaperVenue(client)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NFL props executor (shadow + demo)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--demo-orders", action="store_true")
    r.add_argument("--novig", action="store_true", help="also run Novig as a second SHADOW venue (public books, paper fills)")
    r.add_argument("--novig-paper-orders", action="store_true",
                   help="with --novig: also send each Novig PLAN as a signed IOC order to the Novig PAPER sandbox "
                        "(trading key from --novig-env-file / ~/.novig/paper.env); plumbing validation only")
    r.add_argument("--novig-env-file", default=None, help="Novig paper env file (default ~/.novig/paper.env)")
    r.add_argument("--env-file", default=None)
    r.add_argument("--db", default=DEFAULT_DB)
    r.add_argument("--loop-seconds", type=int, default=0)
    r.add_argument("--until", default="")
    for name in ("pnl", "status"):
        p = sub.add_parser(name)
        p.add_argument("--db", default=DEFAULT_DB)
    a = ap.parse_args(argv)
    if a.cmd == "run" and a.novig_paper_orders and not a.novig:
        ap.error("--novig-paper-orders requires --novig (decisions are made on the production Novig book)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = props_config()
    os.makedirs(os.path.dirname(a.db), exist_ok=True)
    ledger = Ledger(a.db)
    data_client = KalshiClient(PUBLIC_DATA_ENV, None, None)

    if a.cmd == "pnl":
        print("kalshi shadow:", json.dumps(grade(ledger, data_client), indent=1))
        from ev.novig import exec as nx
        from ev.novig.client import NovigClient
        print("novig shadow:", json.dumps(nx.grade(ledger, NovigClient("public-data")), indent=1))
        # sandbox orders carry PAPER market ids, graded on the paper catalog's own settled statuses (unsigned GET)
        print("novig paper orders:", json.dumps(nx.grade(ledger, NovigClient("paper"), mode=nx.PAPER_MODE), indent=1))
        return 0
    if a.cmd == "status":
        day = et_day(datetime.now(timezone.utc))
        g = RiskGate(cfg, ledger, "shadow")
        print(f"killed={g.killed} reason={g.kill_reason} | notional bankroll ${cfg.bankroll:.0f} caps: per_bet "
              f"${cfg.per_bet_cap:.0f} per_game ${cfg.per_game_cap:.0f} daily ${cfg.daily_deploy_cap:.0f}")
        for m in ("shadow", "demo", "novig", "novig_paper"):
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
    nc = nrisk = novig_legs = None
    paper_venue = paper_risk = paper_legs = None
    legs_loaded = 0.0
    if a.novig:
        from ev.novig.client import NovigClient
        nc = NovigClient("public-data")
        nrisk = RiskGate(cfg, ledger, "novig")
    if a.novig_paper_orders:
        from ev.novig.paper_orders import PaperRiskGate
        paper_venue, paper_risk = load_novig_paper_venue(a.novig_env_file), PaperRiskGate(cfg, ledger, "novig_paper")
    while True:
        try:
            latest = build_latest_local(data_client, cfg)
        except Exception:                                                            # noqa: BLE001
            log.exception("local join failed; retrying next cycle")
            latest = None
        if latest:
            try:
                novig_alerts = []
                if nc is not None:
                    try:
                        from ev.novig import exec as nx
                        refresh = novig_legs is None or time.time() - legs_loaded > 600
                        if refresh:
                            novig_legs, legs_loaded = nx.load_novig_legs(nc, datetime.now(timezone.utc)), time.time()
                        if paper_venue is not None and (refresh or paper_legs is None):
                            # paper catalog refreshed on the production legs' cadence; a failure keeps the previous
                            # map (unmatched legs just come back NO_MARKET) and must not stop the shadow pass
                            try:
                                paper_legs = nx.load_novig_legs(paper_venue.client, datetime.now(timezone.utc))
                                paper_venue.set_legs(paper_legs)
                            except Exception:                                        # noqa: BLE001
                                log.exception("paper catalog load failed; keeping the previous map")
                                paper_legs = paper_legs or {}
                        cands = nx.candidates(latest["snap"], novig_legs, cfg, datetime.now(timezone.utc))
                        ns = nx.run_pass(snap_ts=latest["ts"], cands=cands, cfg=cfg, nc=nc, ledger=ledger, risk=nrisk,
                                         paper_venue=paper_venue, paper_risk=paper_risk)
                        novig_alerts = ns["alerts"]
                        log.info("NOVIG legs=%s candidates=%s hist=%s executed=%s paper_orders=%s", len(novig_legs),
                                 ns["considered"], ns["hist"], len(ns["executed"]), ns["paper_orders"])
                        for e in ns["executed"]:
                            log.info("  NOVIG %s", e)
                    except Exception:                                                # noqa: BLE001
                        log.exception("novig pass failed; continuing with Kalshi")
                queued = publish_local(latest, et_day(datetime.now(timezone.utc)), novig_alerts)
                log.info("JOIN rows=%s flagged=%s queued_alerts=%s books_hist=%s pooled_rows=%s pool_stats=%s",
                         len(latest["rows"]), sum(1 for r in latest["rows"] if r.get("flag")), queued,
                         latest.get("books_hist"), len(latest.get("pooled") or []), latest.get("pool_stats"))
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
