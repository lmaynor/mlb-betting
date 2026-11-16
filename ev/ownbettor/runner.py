"""
ev.ownbettor.runner -- the own-account bettor pipeline + CLI.

Per signal, in order, every step audited:
    signal_received (Kelly stake computed and recorded FIRST, gate outcome or not)
    -> duplicate-intent guard (a signal can never intend two orders)
    -> rule gate (ev.ownbettor.rules)
    -> risk gate (kill switch, daily caps, loss stop)
    -> ticker resolution (verified table only; unmapped is an audited skip)
    -> executable re-check at the venue's current ask (net EV after the taker fee,
       contracts sized from the planned stake, per-bet cap enforced)
    -> order_intended -> venue submit -> order_result (what the venue reported)
Venue errors feed the error breaker; an auth failure trips the kill switch at once.

Modes: dry-run (paper adapter, simulated fills) unless OWNBETTOR_LIVE_ARMED is set,
in which case the CLI constructs the live adapter -- whose constructor refuses until
every provisioning step Luc owns has happened (see venues.kalshi).  This module never
arms anything itself.

CLI:
    python -m ev.ownbettor.runner run --signals LEDGER.jsonl [--mapping TABLE.json]
                                      [--books BOOKS.json] [--limit N]
    python -m ev.ownbettor.runner settle --settlements SETTLEMENTS.jsonl
    python -m ev.ownbettor.runner trip-kill --reason "..."
    python -m ev.ownbettor.runner reset-kill
    python -m ev.ownbettor.runner status
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

from mlb.execution.kalshi.fees import taker_fee

from ev.ownbettor import rules, sizing
from ev.ownbettor.audit import AuditLog, et_today
from ev.ownbettor.config import BettorConfig, load_config
from ev.ownbettor.resolver import RecordedTableResolver, TickerResolver
from ev.ownbettor.risk import RiskGate
from ev.ownbettor.signals import Signal, load_signal_rows, signals_from_rows
from ev.ownbettor.venues.base import BettorIntent, VenueAdapter, intent_id_for
from ev.ownbettor.venues.kalshi import (KalshiLiveAdapter, KalshiPaperAdapter,
                                         PublicBookSource, RecordedBookSource)


def _looks_auth(error) -> bool:
    if error is None:
        return False
    status = getattr(error, "status", None)
    if status in (401, 403):
        return True
    text = str(error)
    return "401" in text or "403" in text or "Unauthorized" in text or "Forbidden" in text


class Bettor:
    def __init__(self, cfg: BettorConfig, adapter: VenueAdapter, resolver: TickerResolver,
                 audit: AuditLog | None = None):
        self.cfg = cfg
        self.adapter = adapter
        self.resolver = resolver
        self.audit = audit or AuditLog(cfg.audit_path,
                                       mode="dry_run" if adapter.simulated else "live")
        self.risk = RiskGate(cfg, self.audit)

    # ------------------------------------------------------------------ pipeline
    def process_signal(self, signal: Signal) -> dict:
        cfg, audit = self.cfg, self.audit
        day = signal.game_day or et_today()
        stake = sizing.planned_stake(cfg, signal.p_hat, signal.signal_price)
        audit.append("signal_received", day, signal_id=signal.signal_id,
                     system=signal.system, league=signal.league, stat=signal.stat,
                     player=signal.player, selection=signal.selection, side=signal.side,
                     line=signal.line, event_id=signal.event_id, p_hat=signal.p_hat,
                     signal_price=signal.signal_price,
                     limit_price_ceiling=signal.limit_price_ceiling, stake_usd=stake)

        def fail(stage: str, reason: str, **extra) -> dict:
            audit.append(stage, day, signal_id=signal.signal_id, allowed=False,
                         reason=reason, stake_usd=stake, **extra)
            return {"signal_id": signal.signal_id, "outcome": reason, "stake_usd": stake}

        if audit.has_intent(signal.signal_id):
            return fail("rule_decision", "REJ_DUP_INTENT")
        gate = rules.evaluate(signal, cfg)
        audit.append("rule_decision", day, signal_id=signal.signal_id,
                     allowed=gate.allowed, reason=gate.reason, stake_usd=stake,
                     ev_net_pct=(round(gate.ev_net_pct, 4) if gate.ev_net_pct is not None else None))
        if not gate.allowed:
            return {"signal_id": signal.signal_id, "outcome": gate.reason, "stake_usd": stake}

        ok, reason = self.risk.check(day, stake)
        audit.append("risk_decision", day, signal_id=signal.signal_id, allowed=ok,
                     reason=reason, stake_usd=stake)
        if not ok:
            return {"signal_id": signal.signal_id, "outcome": reason, "stake_usd": stake}

        resolved = self.resolver.resolve(signal)
        if resolved is None:
            return fail("intent_failed", "REJ_UNMAPPED")
        ask = self.adapter.best_ask(resolved.ticker, signal.side)
        if ask is None:
            return fail("intent_failed", "REJ_NO_BOOK", ticker=resolved.ticker)
        mult = resolved.fee_multiplier if resolved.fee_multiplier is not None else cfg.taker_multiplier
        fee_per = taker_fee(ask, 1, mult)
        ev_exec = ((signal.p_hat - fee_per) / ask - 1.0) * 100.0 if ask > 0 else None
        if ev_exec is None or ev_exec < cfg.ev_floor * 100.0:
            return fail("intent_failed", "SKIPPED_PRICE_MOVED", ticker=resolved.ticker,
                        ask=ask, ev_exec_pct=(round(ev_exec, 4) if ev_exec is not None else None))
        eff_cfg = replace(cfg, taker_multiplier=mult) if mult != cfg.taker_multiplier else cfg
        contracts, est_cost = sizing.contracts_for(stake, ask, eff_cfg)
        if contracts < 1:
            return fail("intent_failed", "REJ_STAKE_SMALL", ticker=resolved.ticker, ask=ask)

        intent = BettorIntent(intent_id=intent_id_for(signal.signal_id),
                              signal_id=signal.signal_id, ticker=resolved.ticker,
                              side=signal.side, contracts=contracts,
                              limit_price=signal.limit_price_ceiling,
                              planned_stake_usd=stake, ranges=resolved.ranges,
                              exchange_index=resolved.exchange_index,
                              fee_multiplier=resolved.fee_multiplier)
        audit.append("order_intended", day, signal_id=signal.signal_id,
                     intent_id=intent.intent_id, ticker=intent.ticker, side=intent.side,
                     contracts=contracts, limit_price=intent.limit_price,
                     ask=ask, est_cost=est_cost, stake_usd=stake)
        try:
            ack = self.adapter.submit(intent)
        except Exception as e:                                        # noqa: BLE001
            self.risk.note_submit_error(auth=_looks_auth(e))
            audit.append("order_result", day, signal_id=signal.signal_id,
                         intent_id=intent.intent_id, status="ERROR",
                         simulated=self.adapter.simulated, error=repr(e)[:400])
            return {"signal_id": signal.signal_id, "outcome": "ERROR", "stake_usd": stake}
        if ack.status == "ERROR":
            self.risk.note_submit_error(auth=_looks_auth(ack.error))
        else:
            self.risk.note_submit_ok()
        audit.append("order_result", day, signal_id=signal.signal_id,
                     intent_id=intent.intent_id, venue_order_id=ack.order_id,
                     client_order_id=ack.client_order_id, status=ack.status,
                     fill_count=ack.fill_count, filled_cost=ack.filled_cost, fee=ack.fee,
                     simulated=ack.simulated, error=ack.error)
        return {"signal_id": signal.signal_id, "outcome": ack.status, "stake_usd": stake,
                "filled_cost": ack.filled_cost}

    def process_rows(self, rows: list) -> dict:
        summary: dict = {"signals": 0, "outcomes": {}}
        for signal in signals_from_rows(rows):
            summary["signals"] += 1
            out = self.process_signal(signal)
            summary["outcomes"][out["outcome"]] = summary["outcomes"].get(out["outcome"], 0) + 1
        return summary

    # ------------------------------------------------------------------ settlements
    def record_settlement(self, signal_id: str, pnl_usd: float, day: str, note: str = "") -> None:
        self.audit.append("settlement", day, signal_id=signal_id,
                          pnl_usd=round(float(pnl_usd), 4), note=note)
        self.risk.check_loss_stop(day)


# ---------------------------------------------------------------------- CLI
def _build_bettor(cfg: BettorConfig, args) -> Bettor:
    resolver = RecordedTableResolver.from_file(args.mapping or cfg.mapping_path)
    if cfg.live_armed:
        adapter = KalshiLiveAdapter(cfg)          # refuses until provisioned; see venues.kalshi
    else:
        source = RecordedBookSource.from_file(args.books) if getattr(args, "books", None) \
            else PublicBookSource()
        adapter = KalshiPaperAdapter(source, cfg)
    return Bettor(cfg, adapter, resolver)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AUT-09 own-account bettor (dry-run unless "
                                             "OWNBETTOR_LIVE_ARMED is set by the owner)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="process an AUT-01 signal ledger")
    r.add_argument("--signals", required=True, help="AUT-01 shadow ledger JSONL")
    r.add_argument("--mapping", default="", help="recorded signal->ticker table (JSON)")
    r.add_argument("--books", default="", help="recorded order books (JSON); default: public data host")
    r.add_argument("--limit", type=int, default=0, help="process at most N signals")
    s = sub.add_parser("settle", help="book realized P&L from a settlements JSONL")
    s.add_argument("--settlements", required=True,
                   help="JSONL rows: {signal_id, pnl_usd, game_day?}")
    t = sub.add_parser("trip-kill", help="trip the kill switch manually")
    t.add_argument("--reason", required=True)
    sub.add_parser("reset-kill", help="manual kill-switch reset (the ONLY reset path)")
    sub.add_parser("status", help="kill state + today's aggregates")
    args = ap.parse_args(argv)

    cfg = load_config()
    audit = AuditLog(cfg.audit_path, mode="live" if cfg.live_armed else "dry_run")
    risk = RiskGate(cfg, audit)

    if args.cmd == "trip-kill":
        risk.trip(f"manual: {args.reason}")
        print(json.dumps({"killed": risk.killed, "reason": risk.kill_reason}))
        return 0
    if args.cmd == "reset-kill":
        risk.reset()
        print(json.dumps({"killed": risk.killed}))
        return 0
    if args.cmd == "status":
        day = et_today()
        print(json.dumps({"killed": risk.killed, "kill_reason": risk.kill_reason,
                          "day": day, "orders_placed": audit.orders_placed(day),
                          "spent_today": audit.spent_today(day),
                          "realized_pnl_today": audit.realized_pnl_today(day),
                          "consecutive_errors": audit.consecutive_errors,
                          "live_armed": cfg.live_armed}, indent=1, sort_keys=True))
        return 0
    if args.cmd == "settle":
        bettor = Bettor(cfg, _NullAdapter(), RecordedTableResolver({}), audit=audit)
        n = 0
        with open(args.settlements, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                bettor.record_settlement(str(row["signal_id"]), float(row["pnl_usd"]),
                                         str(row.get("game_day") or et_today()),
                                         str(row.get("note") or ""))
                n += 1
        print(json.dumps({"settlements": n, "killed": risk.killed,
                          "kill_reason": risk.kill_reason}, indent=1, sort_keys=True))
        return 0
    # run
    try:
        bettor = _build_bettor(cfg, args)
    except RuntimeError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    rows = load_signal_rows(args.signals)
    signals = signals_from_rows(rows)
    if args.limit:
        signals = signals[:args.limit]
    summary = {"signals": 0, "outcomes": {}}
    for signal in signals:
        summary["signals"] += 1
        out = bettor.process_signal(signal)
        summary["outcomes"][out["outcome"]] = summary["outcomes"].get(out["outcome"], 0) + 1
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


class _NullAdapter:
    """Settlement-only adapter placeholder: the settle path never submits orders."""
    name = "none"
    simulated = True

    def best_ask(self, ticker, side):
        return None

    def submit(self, intent):
        raise RuntimeError("settlement mode has no order path")

    def cancel(self, order_id, ticker):
        return {"status": "not_resting", "simulated": True}


if __name__ == "__main__":
    raise SystemExit(main())
