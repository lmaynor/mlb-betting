"""
mlb.execution.kalshi.runner -- orchestrates one pass of the Kalshi lag-trading executor.

    python -m mlb.execution.kalshi.runner run    --mode shadow          # live data, PAPER fills, zero orders
    python -m mlb.execution.kalshi.runner run    --mode demo [--enable] # real signed orders, Kalshi DEMO (fake money)
    python -m mlb.execution.kalshi.runner probe                         # demo mechanics check (tiny orders)
    python -m mlb.execution.kalshi.runner status | reset-kill

Modes
  shadow  decisions use PRODUCTION public order books (no credentials); fills are simulated by
          PaperVenue against a fresh real book. This is the evidence-gathering mode.
  demo    decisions use the DEMO order books; orders go to the Kalshi demo exchange and are only
          SENT when --enable (or KEXEC_ENABLED=1) is given. Without it a PLAN is logged, not sent.

There is no production mode. See client.KalshiClient (refuses non-demo) and the design doc
docs/designs/kalshi_auto_execution.md.

Credentials (demo): KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH from the environment, or from
--env-file / KEXEC_ENV_FILE (only those two keys are read; nothing else in the file is touched).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import dataclasses
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from mlb.execution.kalshi import gates
from mlb.execution.kalshi.fees import taker_fee
from mlb.execution.kalshi.client import KalshiClient, PUBLIC_DATA_ENV, read_env_keys
from mlb.execution.kalshi.config import ExecConfig, load_config
from mlb.execution.kalshi.consensus import ConsensusStore
from mlb.execution.kalshi.ledger import Ledger
from mlb.execution.kalshi.mapping import MapFail, SERIES, map_market
from mlb.execution.kalshi.risk import RiskGate
from mlb.execution.kalshi.venue import KalshiVenue, OrderIntent, PaperVenue

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kalshi_exec")

_ET = ZoneInfo("America/New_York")
_PREFILTER_SLACK = 0.01     # skip the orderbook fetch unless a top-of-book ask is within 1pt of consensus


def _ff(x) -> float | None:
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def et_day(now: datetime) -> str:
    return now.astimezone(_ET).date().isoformat()


def load_demo_client(env_file: str | None) -> KalshiClient:
    keys = ("KALSHI_ENV", "KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PATH")
    vals = {k: os.environ.get(k) for k in keys if os.environ.get(k)}
    path = env_file or os.environ.get("KEXEC_ENV_FILE")
    if path:
        for k, v in read_env_keys(path, keys).items():
            vals.setdefault(k, v)
    if vals.get("KALSHI_ENV", "demo") != "demo":
        raise SystemExit(f"refusing: KALSHI_ENV={vals.get('KALSHI_ENV')!r} (this executor is demo-only)")
    if not (vals.get("KALSHI_API_KEY_ID") and vals.get("KALSHI_PRIVATE_KEY_PATH")):
        raise SystemExit("demo credentials not found (KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PATH)")
    with open(os.path.expanduser(vals["KALSHI_PRIVATE_KEY_PATH"]), "rb") as fh:
        return KalshiClient("demo", vals["KALSHI_API_KEY_ID"], fh.read())


def run_once(*, cfg: ExecConfig, mode: str, data_client, venue, consensus: ConsensusStore,
             ledger: Ledger, risk: RiskGate, clock=None, resolve_game_pk=None,
             cash_fn=None, send: bool = True, pnl_fn=None) -> dict:
    """One pass over every enabled series. Returns a summary dict (also logged)."""
    if resolve_game_pk is None:
        from mlb_core.data import id_resolver
        resolve_game_pk = id_resolver.resolve_game_pk
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = clock()
    day = et_day(now)
    hist: Counter = Counter()
    executed: list[dict] = []
    cash_by: dict = {}
    n_markets = 0

    if pnl_fn is not None:                       # daily-loss kill switch fed with the venue's own numbers
        try:
            pnl = float(pnl_fn())
            ledger.kv_set("last_pnl", f"{pnl:.4f}")
            risk.check_pnl(pnl)
        except Exception as e:                                                  # noqa: BLE001
            log.warning("pnl feed failed (%r); continuing without the loss check this pass", e)

    def cash_for(shard) -> float:
        """Spendable cash for the shard this market trades on (cash_fn(shard) -> dollars)."""
        if shard not in cash_by:
            cash_by[shard] = cash_fn(shard) if cash_fn else cfg.bankroll
        return cash_by[shard]

    for series in cfg.series:
        if series not in SERIES:
            log.warning("series %s not in mapping allowlist, skipped", series)
            continue
        market_name = SERIES[series][0]
        scfg = _series_cfg(cfg, data_client, series)
        try:
            markets = data_client.get_markets(series)
        except Exception as e:                                                  # noqa: BLE001
            log.error("fetch %s failed: %r", series, e)
            hist["ERR_FETCH"] += 1
            continue

        for mk in markets:
            n_markets += 1
            if risk.killed:
                hist["REJ_KILLED"] += 1
                log.error("KILL SWITCH ACTIVE: %s -- stopping pass", risk.kill_reason)
                return _summary(hist, executed, n_markets, mode, day, ledger, killed=True)

            leg = map_market(series, mk, resolve_game_pk)
            if isinstance(leg, MapFail):
                hist[leg.reason] += 1
                continue
            if now >= leg.start_utc - timedelta(minutes=cfg.close_buffer_min):
                hist["REJ_GAME_STARTED"] += 1
                continue
            cons = consensus.point(leg.market, leg.game_pk, leg.line, leg.selection_yes, cfg.lag_lookback_min)
            if cons is None:
                hist["REJ_CONS_THIN"] += 1
                continue

            # cheap prefilter on the market object's top of book: no edge anywhere -> no orderbook fetch
            ya, na = _ff(mk.get("yes_ask_dollars")), _ff(mk.get("no_ask_dollars"))
            near = (ya is not None and cons.p - ya > -_PREFILTER_SLACK) or \
                   (na is not None and (1 - cons.p) - na > -_PREFILTER_SLACK)
            if not near:
                hist["PREFILTER_NO_EDGE"] += 1
                continue

            try:
                book = data_client.get_orderbook(leg.ticker)
            except Exception as e:                                              # noqa: BLE001
                log.error("orderbook %s failed: %r", leg.ticker, e)
                hist["ERR_FETCH"] += 1
                continue

            now_eval = clock()
            k_prev = consensus.kalshi_prev(leg.market, leg.game_pk, leg.line, leg.selection_yes, now_eval,
                                           cfg.lag_lookback_min)
            cash = cash_for(leg.exchange_index)
            budget = risk.budget_cap(leg.game_pk, day, cash)
            d = gates.evaluate(leg, book, cons, k_prev, now_eval, scfg, scfg.bankroll, budget)

            if d.outcome == "PLAN":
                if ledger.has_intent(d.intent_id):
                    d.outcome, d.reason = "REJ_DUP", "intent already ordered today"
                else:
                    ok, why = risk.check_order(d.plan.stake, leg.game_pk, day, cash)
                    if not ok:
                        d.outcome, d.reason = why, "risk gate"

            ack = None
            if d.outcome == "PLAN":
                if not send:
                    d.outcome, d.reason = "PLAN_NOT_SENT", "send disabled (use --enable for demo orders)"
                else:
                    intent = OrderIntent(d.intent_id, leg.ticker, d.side, d.plan.contracts,
                                         d.plan.limit_price, leg.ranges, leg.exchange_index,
                                         fee_multiplier=scfg.taker_multiplier)
                    ack = venue.submit(intent)
                    if ack.status == "ERROR":
                        risk.note_submit_error()
                        d.outcome, d.reason = "ERR_SUBMIT", ack.error or ""
                    else:
                        risk.note_submit_ok()
                        d.outcome = {"FILLED": "EXECUTED", "PARTIAL": "PARTIAL", "NO_FILL": "NO_FILL"}[ack.status]
                    ledger.record_order(
                        day=day, mode=mode, intent_id=d.intent_id, client_order_id=ack.client_order_id,
                        order_id=ack.order_id, ticker=leg.ticker, game_pk=leg.game_pk, side=d.side,
                        contracts=d.plan.contracts, limit_price=d.plan.limit_price, status=ack.status,
                        fill_count=ack.fill_count, filled_cost=ack.filled_cost, fee=ack.fee, raw=ack.raw)
                    _check_fee(ack, scfg.taker_multiplier, risk, hist)
                    for f in ack.fills:
                        ledger.record_fill(fill_id=f.fill_id, order_id=f.order_id, ticker=f.ticker, side=f.side,
                                           count=f.count, price=f.price, fee=f.fee, ts=f.ts, mode=mode)
                    cash_by[leg.exchange_index] = cash - ack.filled_cost

            ledger.record_decision(
                day=day, mode=mode, outcome=d.outcome, reason=d.reason, intent_id=d.intent_id,
                ticker=leg.ticker, series=series, market=market_name, game_pk=leg.game_pk, side=d.side,
                p_cons=d.p_cons, p_hat=d.p_hat, n_books=d.n_books, ask=d.ask,
                ev=d.plan.ev if d.plan else None, contracts=d.plan.contracts if d.plan else None,
                limit_price=d.plan.limit_price if d.plan else None, lag_class=d.lag_class, inputs=d.inputs)
            hist[d.outcome] += 1
            if d.plan is not None and d.outcome in ("EXECUTED", "PARTIAL", "NO_FILL", "PLAN_NOT_SENT", "ERR_SUBMIT"):
                executed.append({
                    "ticker": leg.ticker, "bet": d.selection_bought, "side": d.side, "ask": d.ask,
                    "p_cons": d.p_cons, "p_hat": d.p_hat, "ev": d.plan.ev, "contracts": d.plan.contracts,
                    "limit": d.plan.limit_price, "lag": d.lag_class, "outcome": d.outcome,
                    "filled": ack.fill_count if ack else 0.0, "cost": ack.filled_cost if ack else 0.0,
                    "error": ack.error if ack and ack.error else None})

    return _summary(hist, executed, n_markets, mode, day, ledger)


def _series_cfg(cfg: ExecConfig, data_client, series: str) -> ExecConfig:
    """cfg with taker_multiplier read from the live series object (never hardcoded: KXMLBGAME is 0.5)."""
    getter = getattr(data_client, "get_series", None)
    if getter is None:
        return cfg
    try:
        m = float(getter(series).get("fee_multiplier"))
        if m > 0:
            return dataclasses.replace(cfg, taker_multiplier=m)
    except Exception as e:                                                      # noqa: BLE001
        log.warning("series fee multiplier unavailable for %s (%r); using %.2f", series, e, cfg.taker_multiplier)
    return cfg


def _check_fee(ack, multiplier: float, risk: RiskGate, hist: Counter) -> None:
    """Compare the venue's ACTUAL fee to our model. Fees above the model mean EV was overstated, so
    a material excess trips the kill switch (manual reset); below-model is the safe direction."""
    if not ack.fills:
        return
    model = sum(taker_fee(f.price, f.count, multiplier) for f in ack.fills)
    excess = ack.fee - model
    if excess > max(0.05, 0.25 * model):
        hist["WARN_FEE_ABOVE_MODEL"] += 1
        risk.trip(f"venue fee ${ack.fee:.4f} exceeds model ${model:.4f} (x{multiplier}) -- EV overstated")
    elif excess < -max(0.05, 0.25 * model):
        hist["INFO_FEE_BELOW_MODEL"] += 1
        log.info("venue fee $%.4f below model $%.4f (conservative side)", ack.fee, model)


def make_pnl_fn(client, ledger: Ledger, mode: str):
    """Net P&L (realized + mark-to-bid - fees) over every ticker this executor has filled."""
    from mlb.execution.kalshi.risk import position_pnl

    def fn() -> float:
        tickers = ledger.tickers(mode)
        if not tickers:
            return 0.0
        pos = client.market_positions()
        marks = {}
        for p in pos:
            t = p.get("ticker")
            if t in tickers and float(p.get("position_fp") or 0.0) != 0.0:
                m = client.get_market(t)
                marks[t] = (float(m.get("yes_bid_dollars") or 0.0), float(m.get("no_bid_dollars") or 0.0))
        realized, unrealized, fees = position_pnl(pos, tickers, marks)
        return realized + unrealized - fees
    return fn


def _summary(hist, executed, n_markets, mode, day, ledger, killed=False) -> dict:
    s = {"mode": mode, "day": day, "markets_seen": n_markets, "killed": killed,
         "histogram": dict(hist.most_common()), "orders": executed}
    ledger.kv_set("last_run", f"{datetime.now(timezone.utc).isoformat()} {dict(hist)}")
    return s


def print_summary(s: dict) -> None:
    print(f"\n=== kalshi_exec pass: mode={s['mode']} day={s['day']} markets_seen={s['markets_seen']}"
          f"{'  *** KILLED ***' if s['killed'] else ''}")
    print("rejection / outcome histogram:")
    for k, v in s["histogram"].items():
        print(f"  {k:24} {v}")
    if s["orders"]:
        print("orders:")
        for o in s["orders"]:
            print(f"  {o['outcome']:13} {o['ticker']}  buy {o['side'].upper()} ({o['bet']})  x{o['contracts']} "
                  f"@<= {o['limit']:.2f} ask={o['ask']:.2f}  p_cons={o['p_cons']:.3f} p_hat={o['p_hat']:.3f} "
                  f"ev={o['ev']*100:.1f}%  lag={o['lag']}  filled={o['filled']:.0f} cost=${o['cost']:.2f}"
                  + (f"  ERR {o['error']}" if o['error'] else ""))
    else:
        print("orders: none")


# --------------------------------------------------------------------------------------
# demo mechanics probe
# --------------------------------------------------------------------------------------
def probe(client: KalshiClient, ledger: Ledger, cfg: ExecConfig) -> int:
    """Exercise the REAL order mechanics on the Kalshi demo exchange with a few 1-contract orders.

    Deliberately bypasses the EV gates: this tests plumbing (signing, IOC, price grid, buy-NO
    conversion, fills/fees, idempotency, no-resting-orders), not strategy. Demo money only.
    """
    from mlb.execution.kalshi.fees import taker_fee
    from mlb.execution.kalshi.price_grid import parse_price_ranges
    from mlb.execution.kalshi.venue import client_order_id

    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))

    print("== demo mechanics probe (fake money)")
    check("exchange trading_active", client.exchange_status().get("trading_active") is True)
    bal0 = float(client.get_balance()["balance_dollars"])
    print(f"  demo cash before: ${bal0:.4f}  by shard: {client.shard_balances()}")

    markets = [m for m in client.get_markets("KXMLBGAME") if float(m.get("open_interest_fp") or 0) > 1000]
    markets.sort(key=lambda m: -float(m.get("open_interest_fp") or 0))
    if not markets:
        print("  no liquid demo market found"); return 2
    mk = markets[0]
    ticker = mk["ticker"]
    ranges = tuple(parse_price_ranges(mk.get("price_ranges")))
    shard = int(mk.get("exchange_index") or 0)
    if client.shard_balances().get(shard, 0.0) < 5.0:
        print(f"  market is on shard {shard} but that shard holds ${client.shard_balances().get(shard, 0.0):.2f} "
              f"of demo cash -- fund it first:  runner fund-shard --shard {shard} --amount 60")
        return 2
    venue = KalshiVenue(client)
    run_id = int(time.time())
    print(f"  market: {ticker}  (exchange_index={mk.get('exchange_index')})")

    # 1. marketable IOC buy YES x1 at the best ask
    book = client.get_orderbook(ticker)
    ya = book.best_ask("yes")
    i1 = OrderIntent(f"probe-{run_id}-yes", ticker, "yes", 1, ya, ranges, mk.get("exchange_index"))
    a1 = venue.submit(i1)
    check("buy YES x1 @ best ask -> filled", a1.status == "FILLED", f"status={a1.status} err={a1.error}")
    if a1.fills:
        f = a1.fills[0]
        check("fill is on the YES side at <= our limit", f.side == "yes" and f.price <= ya + 1e-9,
              f"side={f.side} price={f.price}")
        model_fee = taker_fee(f.price, f.count)
        check("real fee_cost matches fee model", abs(f.fee - model_fee) < 0.0006,
              f"venue fee={f.fee:.4f} model={model_fee:.4f}")

    # 2. non-marketable IOC must NOT fill and must NOT rest
    book = client.get_orderbook(ticker)
    low = max(0.02, round(book.best_ask("yes") - 0.06, 2))
    a2 = venue.submit(OrderIntent(f"probe-{run_id}-low", ticker, "yes", 1, low, ranges, mk.get("exchange_index")))
    check("non-marketable IOC -> NO_FILL", a2.status == "NO_FILL", f"status={a2.status} err={a2.error}")
    resting = [o for o in client.get_orders(ticker=ticker, status="resting")]
    check("no resting orders left behind (taker only)", len(resting) == 0, f"resting={len(resting)}")

    # 3. idempotency: same intent_id -> same client_order_id -> must not double-fill
    a3 = venue.submit(i1)
    check("duplicate intent rejected / no second fill", a3.status in ("ERROR", "NO_FILL") or a3.fill_count == 0,
          f"status={a3.status} err={(a3.error or '')[:80]}")
    check("client_order_id deterministic", a3.client_order_id == a1.client_order_id == client_order_id(i1.intent_id))

    # 4. buy NO via the ask/1-price conversion
    book = client.get_orderbook(ticker)
    na = book.best_ask("no")
    i4 = OrderIntent(f"probe-{run_id}-no", ticker, "no", 1, na, ranges, mk.get("exchange_index"))
    a4 = venue.submit(i4)
    check("buy NO x1 @ best ask -> filled", a4.status == "FILLED", f"status={a4.status} err={a4.error}")
    if a4.fills:
        f = a4.fills[0]
        check("fill positioned on the NO side at <= our NO limit", f.side == "no" and f.price <= na + 1e-9,
              f"side={f.side} price={f.price} limit={na}")

    # 5. state reconciles
    bal1 = float(client.get_balance()["balance_dollars"])
    spent = sum(a.filled_cost for a in (a1, a2, a3, a4))
    # Kalshi NETS offsetting positions on one market: 1 YES + 1 NO is a guaranteed $1, returned at
    # once. So expected cash change = all-in cost of the fills minus $1 per matched YES/NO pair.
    yes_n = sum(f.count for a in (a1, a2, a3, a4) for f in a.fills if f.side == "yes")
    no_n = sum(f.count for a in (a1, a2, a3, a4) for f in a.fills if f.side == "no")
    expected = spent - 1.0 * min(yes_n, no_n)
    check("balance moved by fills' all-in cost net of YES/NO pair netting", abs((bal0 - bal1) - expected) < 0.01,
          f"balance delta=${bal0 - bal1:.4f} vs expected=${expected:.4f} (gross fills ${spent:.4f})")
    print(f"  demo cash after: ${bal1:.4f}")

    for tag, a, it in (("yes", a1, i1), ("low", a2, None), ("dup", a3, i1), ("no", a4, i4)):
        if it is not None and a.status in ("FILLED", "PARTIAL"):
            ledger.record_order(day=et_day(datetime.now(timezone.utc)), mode="probe", intent_id=it.intent_id,
                                client_order_id=a.client_order_id, order_id=a.order_id, ticker=ticker,
                                game_pk=None, side=it.side, contracts=it.contracts, limit_price=it.limit_price,
                                status=a.status, fill_count=a.fill_count, filled_cost=a.filled_cost, fee=a.fee,
                                raw=a.raw)
    print("PROBE RESULT:", "ALL PASS" if ok else "FAILURES -- see above")
    return 0 if ok else 1


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def _load_consensus(cfg: ExecConfig, now: datetime) -> ConsensusStore:
    markets = sorted({SERIES[s][0] for s in cfg.series if s in SERIES})
    dates = sorted({et_day(now), et_day(now + timedelta(days=1))})
    t0 = time.time()
    store = ConsensusStore.load(markets, dates)
    log.info("consensus loaded for %s dates=%s in %.1fs", markets, dates, time.time() - t0)
    return store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Kalshi lag-trading executor (DEMO only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--mode", choices=["shadow", "demo"], default="shadow")
    r.add_argument("--enable", action="store_true", help="demo: actually SEND orders to the demo exchange")
    r.add_argument("--env-file", default=None)
    r.add_argument("--db", default=None)
    r.add_argument("--loop-seconds", type=int, default=0, help="repeat every N seconds (0 = once)")
    r.add_argument("--cons-reload-min", type=float, default=10.0)
    f = sub.add_parser("fund-shard", help="demo only: move mock cash between exchange shards")
    f.add_argument("--shard", type=int, required=True)
    f.add_argument("--amount", type=float, required=True)
    f.add_argument("--from-shard", type=int, default=0)
    f.add_argument("--env-file", default=None)
    f.add_argument("--db", default=None)
    for name in ("probe", "status", "reset-kill"):
        p = sub.add_parser(name)
        p.add_argument("--env-file", default=None)
        p.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    cfg = load_config()
    ledger = Ledger(args.db)

    if args.cmd == "reset-kill":
        RiskGate(cfg, ledger, "demo").reset()
        print("kill switch reset")
        return 0

    if args.cmd == "status":
        now = datetime.now(timezone.utc); day = et_day(now)
        g = RiskGate(cfg, ledger, "demo")
        print(f"killed={g.killed} reason={g.kill_reason}  bankroll(notional)=${cfg.bankroll:.0f}")
        print(f"caps: per_bet=${cfg.per_bet_cap:.0f} per_game=${cfg.per_game_cap:.0f} "
              f"daily_deploy=${cfg.daily_deploy_cap:.0f} daily_loss=${cfg.daily_loss_cap:.0f}")
        for m in ("shadow", "demo"):
            print(f"{m}: orders_today={ledger.orders_today(day, m)} deployed=${ledger.deployed_today(day, m):.2f} "
                  f"hist={ledger.histogram(day, m)}")
        print("last_run:", ledger.kv_get("last_run"), "| last_pnl:", ledger.kv_get("last_pnl"))
        return 0

    if args.cmd == "fund-shard":
        c = load_demo_client(args.env_file)
        print("before:", c.shard_balances())
        print("transfer:", c.transfer_between_shards(args.amount, args.from_shard, args.shard))
        print("after: ", c.shard_balances())
        return 0

    if args.cmd == "probe":
        return probe(load_demo_client(args.env_file), ledger, cfg)

    # ---- run ----
    mode = args.mode
    send = True
    if mode == "shadow":
        data_client = KalshiClient(PUBLIC_DATA_ENV, None, None)
        venue = PaperVenue(data_client.get_orderbook, cfg.taker_multiplier)
        cash_fn = None
    else:
        data_client = load_demo_client(args.env_file)
        venue = KalshiVenue(data_client)
        send = bool(args.enable or cfg.enabled)
        def cash_fn(shard):
            bal = data_client.shard_balances()
            return bal.get(0 if shard is None else shard, 0.0)
        print(f"demo mode: orders {'WILL be sent' if send else 'NOT sent (dry run; add --enable)'} "
              f"| notional bankroll ${cfg.bankroll:.0f} | demo cash by shard {data_client.shard_balances()}")
    risk = RiskGate(cfg, ledger, mode)
    pnl_fn = make_pnl_fn(data_client, ledger, mode) if mode == "demo" else None

    store, loaded_at = None, 0.0
    while True:
        now = datetime.now(timezone.utc)
        if store is None or (time.time() - loaded_at) > args.cons_reload_min * 60:
            store, loaded_at = _load_consensus(cfg, now), time.time()
        try:
            summary = run_once(cfg=cfg, mode=mode, data_client=data_client, venue=venue, consensus=store,
                               ledger=ledger, risk=risk, cash_fn=cash_fn, send=send, pnl_fn=pnl_fn)
        except Exception:                                                       # noqa: BLE001
            if not args.loop_seconds:
                raise
            log.exception("pass failed; will retry next cycle")                 # transient network etc.
            time.sleep(args.loop_seconds)
            continue
        print_summary(summary)
        sys.stdout.flush()
        if not args.loop_seconds or summary["killed"]:
            return 1 if summary["killed"] else 0
        time.sleep(args.loop_seconds)


if __name__ == "__main__":
    sys.exit(main())
