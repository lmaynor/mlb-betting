"""
ev.novig.exec -- Novig as a second SHADOW venue for NFL props (public books, paper fills, no key needed).

Reuses the Kalshi executor's gates / Kelly sizing / risk gate / ledger / PaperVenue unchanged by presenting a Novig
book in Kalshi's shape:  Over == 'yes', Under == 'no'; one Kalshi-style contract == 100 Novig contracts ($1 of payout),
so book sizes are divided by 100 and an order's contracts are multiplied by 100 when it would be sent. The taker
multiplier is 0 (pre-game fee is free on WHEN_LIVE markets), and the grid is Novig's three-band price grid.
Ledger mode 'novig' (separate caps/kill switch from 'shadow' Kalshi). Never places a real or paper-exchange order.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from ev.novig.client import NovigClient
from ev.novig.map import NovigLeg, event_index, map_prop_market
from mlb.execution.kalshi import gates
from mlb.execution.kalshi.book import Book as KBook
from mlb.execution.kalshi.consensus import ConsPoint
from mlb.execution.kalshi.price_grid import PriceRange
from mlb.execution.kalshi.venue import OrderIntent, PaperVenue

log = logging.getLogger("ev.novig.exec")
MODE = "novig"
NOVIG_RANGES = (PriceRange(0.001, 0.050, 0.001), PriceRange(0.055, 0.945, 0.005), PriceRange(0.950, 0.999, 0.001))
UNIT = 100                                   # Novig contracts per Kalshi-style $1 contract


@dataclass(frozen=True)
class NovigExecLeg:
    ticker: str                              # Novig marketId
    market: str
    game_pk: str
    line: float
    start_utc: datetime
    game_date: str
    ranges: tuple = NOVIG_RANGES
    exchange_index: int | None = None
    selection_yes: str = "OVER"
    selection_no: str = "UNDER"
    group: str = ""


def to_kbook(book, leg: NovigLeg, fetched_at: float | None = None) -> KBook:
    """Novig Book -> Kalshi-shaped Book (Over=yes, Under=no, sizes in $1-payout units)."""
    def lv(oid):
        return [(float(p), float(q // UNIT)) for p, q in book.bids.get(oid, []) if q >= UNIT]
    return KBook(leg.market_id, lv(leg.over_id), lv(leg.under_id), fetched_at if fetched_at is not None else book.fetched_at)


def novig_config(cfg):
    """Zero fee pre-game; a looser EV floor is NOT assumed (same gates), only the fee drag differs."""
    return replace(cfg, taker_multiplier=0.0)


def load_novig_legs(nc: NovigClient, now: datetime) -> dict:
    """{(game_key, stat, player_norm, strike): NovigLeg} for future games."""
    events = event_index(nc.list_events("NFL"))
    out = {}
    for m in nc.list_markets("NFL"):
        leg = map_prop_market(m, events)
        if isinstance(leg, NovigLeg) and leg.start_utc > now:
            out[(leg.game_key, leg.stat, leg.player_norm, leg.strike)] = leg
    return out


def leave_out(point: dict, exclude=frozenset({"novig"})) -> dict | None:
    """Consensus WITHOUT the traded venue. ParlayAPI lists Novig itself as a book (full ladders), so the plain
    consensus is partly Novig's own price -- circular for an edge-vs-consensus test. Needs the per-book fair prices
    (snapshot `book_fair`); an older snapshot without them is skipped, never guessed."""
    bf = point.get("book_fair")
    if not bf:
        return None
    vals = [v for b, v in bf.items() if b not in exclude]
    if not vals:
        return None
    med = sorted(vals)[len(vals) // 2] if len(vals) % 2 else (sorted(vals)[len(vals) // 2 - 1] + sorted(vals)[len(vals) // 2]) / 2
    mad = sorted(abs(v - med) for v in vals)
    mad = mad[len(mad) // 2] if len(mad) % 2 else (mad[len(mad) // 2 - 1] + mad[len(mad) // 2]) / 2
    return dict(point, p_over=med, n_books=len(vals), disp=mad)


def candidates(snap: list, legs: dict, cfg, now: datetime) -> list:
    """Leave-Novig-out consensus points with >= n_min books that have a Novig market at the same line and are not
    near kick."""
    out = []
    for ev in snap:
        start = datetime.fromisoformat(ev["commence"])
        mins = (start - now).total_seconds() / 60.0
        if mins < cfg.close_buffer_min:
            continue
        for p in ev["points"]:
            p = leave_out(p)
            if p is None or p["n_books"] < cfg.n_min_books or p["disp"] > cfg.disp_max:
                continue
            leg = legs.get((ev["game_key"], p["stat"], p["player_norm"], p["point"]))
            if leg is not None:
                out.append((leg, p, mins))
    return out


def run_pass(*, snap_ts: str, cands: list, cfg, nc: NovigClient, ledger, risk, clock=None, pace_s: float = 0.7) -> dict:
    clock = clock or (lambda: datetime.now(timezone.utc))
    ncfg = novig_config(cfg)
    day = clock().astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).date().isoformat()
    cons_ts = datetime.fromisoformat(snap_ts)
    hist: dict = {}
    executed, alerts = [], []
    fresh: dict = {}

    def book_fn(ticker):                       # PaperVenue fetches a FRESH snapshot at fill time
        leg = fresh[ticker]
        return to_kbook(nc.get_book(ticker, depth=10), leg)
    paper = PaperVenue(book_fn, 0.0)
    for leg, p, _mins in cands:
        if risk.killed:
            hist["REJ_KILLED"] = hist.get("REJ_KILLED", 0) + 1
            break
        group = f"novig|{leg.game_key}|{leg.player_norm}|{leg.stat}"
        if ledger.kv_get(f"grp:{day}:{group}") is not None:
            hist["REJ_GROUP_HELD"] = hist.get("REJ_GROUP_HELD", 0) + 1
            continue
        fresh[leg.market_id] = leg
        try:
            kb = to_kbook(nc.get_book(leg.market_id, depth=10), leg)
        except Exception as e:                                                       # noqa: BLE001
            log.warning("novig book %s failed: %r", leg.description, str(e)[:100])
            hist["ERR_FETCH"] = hist.get("ERR_FETCH", 0) + 1
            continue
        time.sleep(pace_s)
        xl = NovigExecLeg(ticker=leg.market_id, market=f"novig:{leg.stat}:{leg.player_norm}", game_pk=leg.game_key,
                          line=leg.strike, start_utc=leg.start_utc, game_date=leg.game_key.split(":")[1], group=group)
        cons = ConsPoint(p=float(p["p_over"]), n_books=int(p["n_books"]), disp=float(p["disp"]), ts=cons_ts, prev_p=None)
        cash = ncfg.bankroll
        d = gates.evaluate(xl, kb, cons, None, clock(), ncfg, ncfg.bankroll, risk.budget_cap(xl.game_pk, day, cash))
        if d.outcome == "PLAN":
            if ledger.has_intent(d.intent_id):
                d.outcome, d.reason = "REJ_DUP", "intent already ordered"
            else:
                ok, why = risk.check_order(d.plan.stake, xl.game_pk, day, cash)
                if not ok:
                    d.outcome, d.reason = why, "risk gate"
        ack = None
        if d.outcome == "PLAN":
            ack = paper.submit(OrderIntent(d.intent_id, leg.market_id, d.side, d.plan.contracts, d.plan.limit_price,
                                           NOVIG_RANGES, None, fee_multiplier=0.0))
            d.outcome = {"FILLED": "EXECUTED", "PARTIAL": "PARTIAL", "NO_FILL": "NO_FILL"}[ack.status]
            ledger.record_order(day=day, mode=MODE, intent_id=d.intent_id, client_order_id=ack.client_order_id,
                                order_id=ack.order_id, ticker=leg.market_id, game_pk=xl.game_pk, side=d.side,
                                contracts=d.plan.contracts, limit_price=d.plan.limit_price, status=ack.status,
                                fill_count=ack.fill_count, filled_cost=ack.filled_cost, fee=ack.fee, raw=ack.raw)
            for f in ack.fills:
                ledger.record_fill(fill_id=f.fill_id, order_id=f.order_id, ticker=f.ticker, side=f.side, count=f.count,
                                   price=f.price, fee=f.fee, ts=f.ts, mode=MODE)
            if ack.fill_count > 0:
                ledger.kv_set(f"grp:{day}:{group}", d.intent_id)
        ledger.record_decision(day=day, mode=MODE, outcome=d.outcome, reason=d.reason, intent_id=d.intent_id,
                               ticker=leg.market_id, series="NOVIGPROP", market=xl.market, game_pk=xl.game_pk,
                               side=d.side, p_cons=d.p_cons, p_hat=d.p_hat, n_books=d.n_books, ask=d.ask,
                               ev=d.plan.ev if d.plan else None, contracts=d.plan.contracts if d.plan else None,
                               limit_price=d.plan.limit_price if d.plan else None, lag_class=d.lag_class,
                               inputs=dict(d.inputs, player=leg.player, stat=leg.stat, venue="novig"))
        hist[d.outcome] = hist.get(d.outcome, 0) + 1
        if d.plan is not None and d.outcome in ("EXECUTED", "PARTIAL"):
            side = d.side
            row = {"venue": "novig", "ticker": leg.market_id, "game_key": leg.game_key, "stat": leg.stat,
                   "player": leg.player, "strike": leg.strike, "side": side, "p_over": p["p_over"],
                   "n_books": p["n_books"], "spread": kb.yes_spread(), "oi": None,
                   f"ask_{side}": d.ask, f"ev_{side}": d.plan.ev, "flag": True}
            alerts.append(row)
            executed.append({"player": leg.player, "stat": leg.stat, "strike": leg.strike, "side": side, "ask": d.ask,
                             "ev": d.plan.ev, "units": d.plan.contracts, "filled": ack.fill_count if ack else 0,
                             "cost": ack.filled_cost if ack else 0, "outcome": d.outcome})
    return {"day": day, "considered": len(cands), "hist": hist, "executed": executed, "alerts": alerts,
            "killed": risk.killed}


def grade(ledger, nc: NovigClient) -> dict:
    """Paper P&L on Novig's own settled outcome statuses (Winner/Loser). Unsettled/void/FMV markets stay 'open'."""
    with ledger._lock:
        rows = [tuple(r) for r in ledger._c.execute(
            "SELECT ticker, side, fill_count, filled_cost FROM orders WHERE mode=? AND fill_count>0", (MODE,))]
    tot = {"orders": len(rows), "settled": 0, "open": 0, "wins": 0, "staked": 0.0, "pnl": 0.0}
    for mid, side, n, cost in rows:
        try:
            m = nc.request("GET", f"/v3/public/catalog/markets/{mid}")
        except Exception:                                                            # noqa: BLE001
            tot["open"] += 1
            continue
        want = "Over" if side == "yes" else "Under"
        st = {o["name"].split(" ")[0]: str(o.get("status")) for o in m.get("outcomes", [])}
        mine = st.get(want, "TBD")
        if m.get("status") != "SETTLED" or mine not in ("Winner", "Loser", "WINNER", "LOSER"):
            tot["open"] += 1
            continue
        won = mine.lower() == "winner"
        tot["settled"] += 1
        tot["wins"] += int(won)
        tot["staked"] += cost
        tot["pnl"] += (n if won else 0.0) - cost
    tot["roi"] = round(tot["pnl"] / tot["staked"], 4) if tot["staked"] else None
    return tot
