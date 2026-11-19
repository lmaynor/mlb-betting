"""kalshi_demo_paper_taker.py -- AUT-12: Kalshi DEMO paper-trading loop for the RFQ combo NO-taker.

Luc's direction (2026-11-19): "Why not just paper trade with the kalshi key. Nothing stopping you
from playing around with the fake money in there. Find a way to make more money."

WHAT IT DOES
  Watches live DEMO RFQ flow (WebSocket `communications` channel + REST poll backstop), prices every
  combo with the production pricer (ev.kalshi_rfq_pricer) fed by the same fair book the recorder /
  maker daemon use (banked prop indices + banked game lines + the sweep props file; zero ParlayAPI
  credits), gates each pricer QUOTE through the taker scanner's Blades 1-3
  (ev.combo_taker_scanner.evaluate_combo at the pricer's strike), enforces the Blade 4 caps through
  the RFQ risk gate ($15 per combo / $75 per player / $150 per game), and SUBMITS one-sided NO quotes
  to the DEMO exchange (RFQMaker live lifecycle: create_quote -> quote_accepted -> last-look ->
  confirm inside the 3 s HVM window). Opportunistically, when a priced combo's own market carries a
  resting YES bid, it also tries the resting-book channel: an IOC sell-YES order at that bid (a NO
  buy at 1 - bid), sized off the same $15 cap. Every decision / quote / order / fill / settlement is
  mirrored to the bucket under Kalshi/paper_taker_demo/ with a per-ET-day summary.

SAFETY (hard, tested)
  * DEMO HOST ONLY. The client is constructed for env "demo" and the runner hard-asserts the
    resulting base_url host is external-api.demo.kalshi.co before any request; a stub or override
    pointing anywhere else aborts startup (tests/test_aut12_demo_paper_taker.py). KALSHI_RFQ_ENV /
    KALSHI_ENV set to anything but demo also aborts startup.
  * Credentials come from the environment (run.sh fetches the Secret Manager demo pair in-memory;
    values are never printed, logged or written to disk by this program).
  * Kill switches: the RFQ ledger kill switch (manual-reset, tripped by the gate itself on abuse
    patterns) AND a bucket object Kalshi/paper_taker_demo/KILL whose mere existence halts
    submissions (checked every flush cycle; open quotes are cancelled when it appears).
  * Live quoting additionally requires KALSHI_RFQ_LIVE=1 (RFQMaker's own arm flag).

Modes: --mode live (default; quotes + IOC orders on the demo exchange) | --mode shadow (full
pipeline, nothing submitted; the IOC path only logs would-be orders).

State: sqlite ledger (RFQLedger) + aut12_state.json under --runtime-dir. The sqlite ledger is the
source of truth for quotes/fills; the bucket mirror is append-only chunks + a summary snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ev.combo_taker_scanner import ComboLiabilityGuard, evaluate_combo  # noqa: E402
from ev.kalshi_rfq_pricer import KalshiRFQPricer, PricerConfig, parse_rfq  # noqa: E402
from ev.kalshi_rfq_taker_replay import candidate_from_legs  # noqa: E402
from mlb.execution.kalshi.client import KalshiAPIError, load_private_key  # noqa: E402
from mlb.execution.kalshi.fees import taker_fee  # noqa: E402
from mlb.execution.kalshi.rfq_client import KalshiRFQStream, RFQClient  # noqa: E402
from mlb.execution.kalshi.rfq_maker import RFQConfig, RFQLedger, RFQMaker, RFQRiskGate  # noqa: E402
from mlb_core import storage  # noqa: E402
import kalshi_rfq_daemon as daemon  # noqa: E402  (scripts/ sibling: FeedState + feed loaders)

log = logging.getLogger("aut12_demo_paper_taker")

DEMO_HOST = "external-api.demo.kalshi.co"
BUCKET_PREFIX = "Kalshi/paper_taker_demo"
KILL_OBJECT = f"{BUCKET_PREFIX}/KILL"
ET = ZoneInfo("America/New_York")

CAP_PER_COMBO = 15.00
CAP_PER_PLAYER = 75.00
CAP_PER_GAME = 150.00

MAKER_FEE_MULT = 0.5     # RUL-20261118: maker channel fee multiplier
TAKER_FEE_MULT = 1.0


def timed(fn, timeout_s: float, label: str):
    """Run fn() on a daemon thread with a hard wall-clock timeout.

    mlb_core.storage passes a 600 s per-request timeout to the GCS client and its retry wrapper can
    stretch a stalled write far beyond that through this VM's egress proxy (observed: a summary
    write still retrying minutes later, wedging shutdown). Mirror writes must never wedge the
    trading loop: on timeout we log and abandon the call (the daemon thread may finish it later in
    the background, which is harmless for append-only chunks and idempotent summaries)."""
    box: dict = {}

    def _go():
        try:
            box["value"] = fn()
        except Exception as e:                                   # noqa: BLE001
            box["error"] = e

    th = threading.Thread(target=_go, daemon=True, name=f"timed-{label}")
    th.start()
    th.join(timeout_s)
    if th.is_alive():
        log.warning("%s timed out after %.0fs (abandoned; may complete in background)", label, timeout_s)
        return None
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ------------------------------------------------------------------------------------- safety
def assert_demo_client(client) -> None:
    """Hard-assert the client can only talk to the Kalshi DEMO host. Raises SystemExit otherwise.

    This is the runtime half of the demo-only fence (the client constructors refusing other envs
    are the other half); tests exercise it against stub clients pointed at the production host."""
    host = urlparse(getattr(client, "base_url", "") or "").hostname
    if getattr(client, "env", None) != "demo" or host != DEMO_HOST:
        raise SystemExit(
            f"REFUSING TO START: client env={getattr(client, 'env', None)!r} host={host!r}; "
            f"AUT-12 runs on the Kalshi DEMO host ({DEMO_HOST}) only.")


def build_demo_client(env=None) -> RFQClient:
    """RFQClient for the demo exchange from KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PEM env vars."""
    env = os.environ if env is None else env
    for var in ("KALSHI_RFQ_ENV", "KALSHI_ENV"):
        v = (env.get(var) or "").strip().lower()
        if v and v != "demo":
            raise SystemExit(f"REFUSING TO START: {var}={v!r}; AUT-12 is demo-only.")
    key_id = env.get("KALSHI_API_KEY_ID")
    pem = env.get("KALSHI_PRIVATE_KEY_PEM")
    if not key_id or not pem:
        raise SystemExit("demo credentials not found (KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PEM)")
    client = RFQClient("demo", key_id, pem.encode() if isinstance(pem, str) else pem)
    assert_demo_client(client)
    return client


# ------------------------------------------------------------------------------------- sizing
def contracts_for_cap(cap_dollars: float, price: float) -> int:
    """Whole contracts whose collateral (contracts * price) fits the dollar cap."""
    if price <= 0:
        return 0
    return int(cap_dollars // price)


def no_fill_pnl(contracts: float, price: float, yes_won: bool, fee_mult: float) -> float:
    """Settled P&L of a filled NO position: payoff 1.00 if YES lost, else 0; minus price and fee."""
    payoff = 0.0 if yes_won else 1.0
    return contracts * (payoff - price) - taker_fee(price, contracts, fee_mult)


# ------------------------------------------------------------------------------------- pricer gate
class ScannerGatedPricer:
    """KalshiRFQPricer + the taker scanner's Blades 1-3 as an admission gate on QUOTE decisions.

    The pricer sets the strike (its one-sided no_bid). The scanner then re-evaluates the same combo
    as a taker candidate at that strike (bid_yes = 1 - no_bid): ROI >= 3.0% on the conservative
    joint, true edge >= 2.5c, no hot scratch, games not inside 15 min. A pricer QUOTE the scanner
    would not fire becomes a REJECT with reason SCANNER_GATE (scanner reasons kept in notes).
    Everything else delegates to the inner pricer (book, leg_gate, inventory_fn)."""

    def __init__(self, inner: KalshiRFQPricer):
        self._inner = inner
        self.last_scan: dict = {}

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def price(self, rfq, now=None):
        now = time.time() if now is None else now
        d = self._inner.price(rfq, now)
        if not d.quoted or d.kind != "combo":
            return d
        parsed = rfq if not isinstance(rfq, dict) else parse_rfq(rfq)
        legs = [(l.ticker, l.side) for l in parsed.legs]
        cand, why = candidate_from_legs(
            combo_id=parsed.market_ticker or parsed.rfq_id, venue="kalshi",
            legs_with_sides=legs, book=self._inner.book, t=now, bid_yes=1.0 - d.no_bid)
        if cand is None:
            self.last_scan = {"rfq_id": parsed.rfq_id, "scanner": why}
            return replace(d, action="REJECT", reason="SCANNER_UNPRICED",
                           notes={**d.notes, "scanner": why})
        sig = evaluate_combo(cand)
        self.last_scan = {"rfq_id": parsed.rfq_id, "fires": sig.fires, "reasons": list(sig.reasons),
                          "roi_no_taker": round(sig.roi_no_taker, 4),
                          "p_cons_yes": round(sig.p_cons_yes, 4), "ask_no": round(sig.ask_no, 4),
                          "rotten_leg": sig.has_rotten_leg, "cannibalistic": sig.is_cannibalistic,
                          "timing_approx": bool(getattr(cand, "timing_approx", False))}
        if not sig.fires:
            return replace(d, action="REJECT", reason="SCANNER_GATE",
                           notes={**d.notes, "scanner_reasons": list(sig.reasons),
                                  "scanner_roi": round(sig.roi_no_taker, 4)})
        return replace(d, notes={**d.notes, "scanner_roi": round(sig.roi_no_taker, 4),
                                 "scanner_rotten_leg": sig.has_rotten_leg})


# ------------------------------------------------------------------------------------- runner
class DemoPaperTaker:
    def __init__(self, args):
        self.args = args
        self.mode = args.mode
        self.runtime_dir = args.runtime_dir
        os.makedirs(self.runtime_dir, exist_ok=True)
        self.client = build_demo_client()
        self.key_id = os.environ["KALSHI_API_KEY_ID"]
        self.pem = os.environ["KALSHI_PRIVATE_KEY_PEM"].encode()

        self.book = daemon.InMemoryFairBook()
        game_spec = os.environ.get("KALSHI_RFQ_GAME_SPORTS", "nfl,nba,nhl")
        game_client, game_sports = daemon.make_game_client(game_spec)
        banked_sports = daemon.banked_sports_for("auto", game_sports)
        self.feed = daemon.FeedState(
            self.book, args.feed, None, game_client, game_sports, ("nfl", "nba", "nhl"),
            ("h2h", "spreads", "totals"), banked_sports=banked_sports)
        # ParlayAPI bills 1 credit per market per sport per pull: stretch the live-pull cadence to
        # the daily budget exactly like the recorder job (banked feeds serve first; the live pull
        # only covers sports they cannot serve -- tonight that is NFL, whose banked feed is dead).
        budget = float(os.environ.get("KALSHI_RFQ_GAME_BUDGET", "120"))
        per_pull = max(len(game_sports) * 3, 1)
        self.game_reload_sec = max(3600.0, per_pull * 86400.0 / max(budget, 1.0))
        log.info("game legs: banked first for %s; live pull fallback for %s every %.0fs "
                 "(budget %.0f credits/day)", list(banked_sports), list(game_sports),
                 self.game_reload_sec, budget)
        pcfg = replace(PricerConfig(), max_unpriced_legs=0, max_legs=4)
        self.pricer = ScannerGatedPricer(KalshiRFQPricer(self.book, pcfg))

        self.ledger = RFQLedger(args.db or os.path.join(self.runtime_dir, "ledger.sqlite"))
        rcfg = RFQConfig(max_liability_per_quote_dollars=CAP_PER_COMBO,
                         max_exposure_per_game_dollars=CAP_PER_GAME,
                         max_exposure_per_player_dollars=CAP_PER_PLAYER,
                         max_open_quotes=25)
        self.gate = RFQRiskGate(rcfg, self.ledger, self.mode)
        self.maker = RFQMaker(self.pricer, self.gate, self.ledger, self.client, self.mode)
        self.pricer._inner.inventory_fn = self.maker.inventory

        self.guard = ComboLiabilityGuard(CAP_PER_PLAYER, CAP_PER_GAME, CAP_PER_COMBO)
        self.state = self._load_state()
        self.decided_rfqs: dict[str, float] = self.state.get("decided_rfqs", {})
        self.ioc_fills: list[dict] = self.state.get("ioc_fills", [])
        self.book_probes: list[dict] = []
        self.settlements_done: set[str] = set(self.state.get("settlements_done", []))
        self.mirror = self.state.get("mirror", {"decisions": 0, "fills": 0, "quotes": {}})
        self.settled_pnl_total = float(self.state.get("settled_pnl_total", 0.0))
        self._last_balance: float | None = None
        self._pending_settlements: list[dict] = []
        self._flush_lock = threading.Lock()
        self.seq = int(self.state.get("seq", 0))
        self.started_at = time.time()
        self.stats = {"rfqs_seen": 0, "priced": 0, "quotes_submitted": 0, "ioc_orders": 0,
                      "book_probes": 0, "books_with_bid": 0}
        self.balance_start: float | None = None

    # ---- state ----
    def _state_path(self) -> str:
        return os.path.join(self.runtime_dir, "aut12_state.json")

    def _load_state(self) -> dict:
        try:
            with open(self._state_path()) as fh:
                return json.load(fh)
        except Exception:                                        # noqa: BLE001 -- fresh start is safe
            return {}

    def _save_state(self) -> None:
        self.state = {"decided_rfqs": self.decided_rfqs, "ioc_fills": self.ioc_fills,
                      "settlements_done": sorted(self.settlements_done), "mirror": self.mirror,
                      "seq": self.seq,
                      "settled_pnl_total": getattr(self, "settled_pnl_total", 0.0)}
        tmp = self._state_path() + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.state, fh)
        os.replace(tmp, self._state_path())

    # ---- kill switch (bucket object) ----
    def bucket_killed(self) -> bool:
        try:
            return bool(timed(lambda: storage.exists(KILL_OBJECT), 15.0, "kill-check"))
        except Exception as e:                                   # noqa: BLE001 -- a storage hiccup must not halt trading silently OR block the check forever
            log.warning("kill-object check failed: %r", e)
            return False

    # ---- event handling ----
    def on_event(self, typ: str, msg: dict, received_ts: float):
        if typ == "rfq_created":
            rfq_id = str(msg.get("id") or msg.get("rfq_id") or "")
            seen_at = self.decided_rfqs.get(rfq_id)
            if seen_at and time.time() - seen_at < 1800:
                return None                                      # already decided this RFQ recently
            self.decided_rfqs[rfq_id] = time.time()
            self.stats["rfqs_seen"] += 1
            out = self.maker.on_event(typ, msg, received_ts)
            if out is not None:
                self.stats["priced"] += 1
                if getattr(out, "quoted", False):
                    self.stats["quotes_submitted"] += 1
                self._probe_book(msg)
            return out
        return self.maker.on_event(typ, msg, received_ts)

    # ---- resting-book (Channel 1) probe + IOC path ----
    def _probe_book(self, msg: dict) -> None:
        """One orderbook look per priced combo ticker. Logs the book either way (Channel-1 evidence);
        in live mode, a scanner fire at the resting YES bid becomes an IOC NO buy sized off the cap."""
        ticker = str(msg.get("market_ticker") or "")
        if not ticker:
            return
        try:
            book = self.client.get_orderbook(ticker)
        except Exception as e:                                   # noqa: BLE001
            log.warning("orderbook probe %s failed: %r", ticker, e)
            return
        self.stats["book_probes"] += 1
        asks_no = book.asks("no")
        best_yes_bid = book.best_bid("yes")
        rec = {"kind": "book_probe", "ts": time.time(), "ticker": ticker,
               "best_yes_bid": best_yes_bid,
               "yes_bid_levels": len(book.yes_bids), "no_bid_levels": len(book.no_bids),
               "ask_no": asks_no[0].price if asks_no else None,
               "ask_no_size": asks_no[0].size if asks_no else 0.0}
        self.book_probes.append(rec)
        if not asks_no:
            return
        self.stats["books_with_bid"] += 1
        if self.mode != "live" or self.gate.killed:
            rec["ioc"] = "shadow_or_killed"
            return
        if self.ledger.quote_for_rfq(str(msg.get("id") or ""), "live"):
            rec["ioc"] = "skipped_have_quote"
            return
        parsed = parse_rfq(msg)
        cand, why = candidate_from_legs(
            combo_id=ticker, venue="kalshi", legs_with_sides=[(l.ticker, l.side) for l in parsed.legs],
            book=self.book, t=time.time(), bid_yes=best_yes_bid)
        if cand is None:
            rec["ioc"] = f"unpriced:{why}"
            return
        sig = evaluate_combo(cand)
        rec["scanner_fires"] = sig.fires
        rec["scanner_reasons"] = list(sig.reasons)
        if not sig.fires:
            rec["ioc"] = "no_fire"
            return
        ask_no, depth = asks_no[0].price, asks_no[0].size
        contracts = min(int(depth), contracts_for_cap(CAP_PER_COMBO, ask_no))
        if contracts < 1:
            rec["ioc"] = "below_one_contract"
            return
        cand.stake = contracts * ask_no
        if not self.guard.can_admit(cand):
            rec["ioc"] = "blade4_cap"
            return
        games_exp, players_exp = self.gate.exposure()
        over = [g for g in {l.game_id for l in cand.legs}
                if games_exp.get(g, 0.0) + cand.stake > CAP_PER_GAME + 1e-9]
        over += [p for p in {l.player_norm for l in cand.legs if l.player_norm}
                 if players_exp.get(p, 0.0) + cand.stake > CAP_PER_PLAYER + 1e-9]
        if over:
            rec["ioc"] = f"combined_cap:{over}"
            return
        try:
            mkt = self.client.get_market(ticker)
            resp = self.client.create_order(
                ticker=ticker, client_order_id=f"aut12-{uuid.uuid4().hex[:20]}",
                book_side="ask", count=float(contracts), price=best_yes_bid,
                exchange_index=mkt.get("exchange_index"))
        except KalshiAPIError as e:
            rec["ioc"] = f"order_error:{e.status}"
            log.warning("IOC order %s failed: %s", ticker, str(e)[:200])
            return
        except Exception as e:                                   # noqa: BLE001 -- ambiguous: reconcile via fills below
            rec["ioc"] = f"order_error:{type(e).__name__}"
            log.warning("IOC order %s ambiguous failure: %r", ticker, e)
            return
        self.guard.admit(cand)
        self.stats["ioc_orders"] += 1
        order_id = str((resp or {}).get("order_id") or (resp or {}).get("order", {}).get("order_id") or "")
        rec["ioc"] = "submitted"
        rec["order_id"] = order_id
        rec["contracts"] = contracts
        rec["ask_no"] = ask_no
        log.info("IOC NO buy %s: %d @ %.4f (order %s)", ticker, contracts, ask_no, order_id)
        time.sleep(1.5)                                          # read-after-write lag (client docstring)
        try:
            for f in self.client.get_fills(ticker=ticker):
                if order_id and str(f.get("order_id")) != order_id:
                    continue
                self.ioc_fills.append({
                    "fill_id": str(f.get("fill_id") or f.get("id") or order_id), "order_id": order_id,
                    "ticker": ticker, "contracts": float(f.get("count_fp") or contracts),
                    "price_no": round(1.0 - float(f.get("yes_price_dollars") or best_yes_bid), 4),
                    "ts": time.time(), "legs": [l.ticker for l in parsed.legs],
                    "players": sorted({l.player_norm for l in cand.legs if l.player_norm}),
                    "games": sorted({l.game_id for l in cand.legs})})
        except Exception as e:                                   # noqa: BLE001
            log.warning("IOC fill reconcile %s failed: %r", ticker, e)

    # ---- settlement grading ----
    def grade_settlements(self) -> list[dict]:
        out = []
        fills = []
        with self.ledger._lock:
            rows = self.ledger._c.execute(
                "SELECT fill_id, ticker, side, count, price FROM fills WHERE mode='rfq_live'").fetchall()
        fills += [{"fill_id": r[0], "ticker": r[1], "side": r[2], "count": r[3], "price": r[4],
                   "fee_mult": MAKER_FEE_MULT, "src": "rfq_quote"} for r in rows]
        fills += [{"fill_id": f["fill_id"], "ticker": f["ticker"], "side": "no",
                   "count": f["contracts"], "price": f["price_no"], "fee_mult": TAKER_FEE_MULT,
                   "src": "ioc_order"} for f in self.ioc_fills]
        for f in fills:
            key = f"{f['src']}:{f['fill_id']}"
            if key in self.settlements_done or f["side"] != "no":
                continue
            try:
                mkt = self.client.get_market(f["ticker"])
            except Exception as e:                               # noqa: BLE001
                log.warning("settlement check %s failed: %r", f["ticker"], e)
                continue
            result = (mkt.get("result") or "").lower()
            if result not in ("yes", "no"):
                continue
            pnl = no_fill_pnl(float(f["count"]), float(f["price"]), result == "yes", f["fee_mult"])
            self.settlements_done.add(key)
            rec = {"kind": "settlement", "ts": time.time(), **f, "result": result,
                   "pnl": round(pnl, 4)}
            out.append(rec)
            log.info("settled %s %s: pnl %+.2f", f["ticker"], result, pnl)
        return out

    # ---- bucket mirror + summary ----
    def _summary(self, settlements: list[dict]) -> dict:
        day = datetime.now(ET).date().isoformat()
        with self.ledger._lock:
            dq = self.ledger._c.execute(
                "SELECT COUNT(*) FROM decisions WHERE mode='rfq_live' AND day=date('now')").fetchone()[0]
            fills_n = self.ledger._c.execute(
                "SELECT COUNT(*) FROM fills WHERE mode='rfq_live'").fetchone()[0]
        # quotes_in acquires the ledger lock itself -- calling it inside the with block above
        # deadlocks on the non-reentrant lock (this wedged every flush after its chunk write).
        quotes = self.ledger.quotes_in("live", ("open", "accepted", "confirmed", "filled"))
        open_exp = sum((q["collateral"] or 0.0) for q in quotes if q["state"] != "filled")
        self.settled_pnl_total = float(self.state.get("settled_pnl_total", 0.0)) + \
            sum(s["pnl"] for s in settlements)
        settled_pnl = self.settled_pnl_total
        balance = getattr(self, "_last_balance", None)   # refreshed by the flusher under timed();
        # a live balance call does NOT belong in the flush path (client retries can stall for minutes
        # through the egress proxy and would wedge the mirror with it).
        if self.balance_start is None and balance is not None:
            self.balance_start = balance
        return {"day_et": day, "generated_at": datetime.now(timezone.utc).isoformat(),
                "mode": self.mode, "balance_now": balance, "balance_at_process_start": self.balance_start,
                "rfqs_seen": self.stats["rfqs_seen"], "priced": self.stats["priced"],
                "decisions_today_ledger": dq, "quotes_submitted": self.stats["quotes_submitted"],
                "quote_fills_total": fills_n, "ioc_orders": self.stats["ioc_orders"],
                "ioc_fills": len(self.ioc_fills), "book_probes": self.stats["book_probes"],
                "books_with_resting_yes_bid": self.stats["books_with_bid"],
                "open_exposure": round(open_exp, 2), "settled_pnl_total": round(settled_pnl, 4),
                "settlements_graded_total": len(self.settlements_done),
                "killed": self.gate.killed, "kill_reason": self.gate.kill_reason,
                "caps": {"per_combo": CAP_PER_COMBO, "per_player": CAP_PER_PLAYER,
                         "per_game": CAP_PER_GAME}}

    def flush(self) -> None:
        if not self._flush_lock.acquire(timeout=60.0):
            log.warning("flush skipped: another flush is wedged in storage I/O")
            return
        try:
            settlements, self._pending_settlements = self._pending_settlements, []
            self._flush_inner(settlements)
        finally:
            self._flush_lock.release()

    def _flush_inner(self, settlements: list[dict]) -> None:
        records: list[dict] = []
        with self.ledger._lock:
            cur = self.ledger._c
            for row in cur.execute("SELECT * FROM decisions WHERE id > ? ORDER BY id",
                                   (self.mirror["decisions"],)):
                d = dict(row)
                self.mirror["decisions"] = d["id"]
                records.append({"kind": "decision", **{k: d[k] for k in
                                ("day", "mode", "outcome", "reason", "intent_id", "ticker", "side",
                                 "p_cons", "p_hat", "ev", "contracts", "limit_price", "ts") if k in d}})
            for row in cur.execute("SELECT rowid, * FROM fills WHERE rowid > ? ORDER BY rowid",
                                   (self.mirror["fills"],)):
                d = dict(row)
                self.mirror["fills"] = d["rowid"] if "rowid" in d else self.mirror["fills"]
                records.append({"kind": "fill", **d})
        for q in self.ledger.quotes_in("live", ("open", "accepted", "confirmed", "filled",
                                                 "aborted", "expired", "cancelled", "confirm_failed")):
            state = q["state"]
            if self.mirror["quotes"].get(q["quote_id"]) != state:
                self.mirror["quotes"][q["quote_id"]] = state
                records.append({"kind": "quote", "quote_id": q["quote_id"], "rfq_id": q["rfq_id"],
                                "state": state, "ticker": q["market_ticker"], "held_side": q["held_side"],
                                "no_bid": q["no_bid"], "contracts": q["contracts"],
                                "collateral": q["collateral"], "ts_epoch": q["ts_epoch"]})
        records += self.book_probes
        self.book_probes = []
        records += settlements
        records.append({"kind": "heartbeat", "ts": time.time(), **self.stats,
                        "book_legs": len(self.book), "killed": self.gate.killed})
        self.seq += 1
        day = datetime.now(ET).date().isoformat()
        stamp = datetime.now(timezone.utc).strftime("%H%M%S")
        chunk = "\n".join(json.dumps(r, default=str) for r in records) + "\n"
        timed(lambda: storage.write_bytes(chunk.encode(),
                                          f"{BUCKET_PREFIX}/ledger/{day}/{stamp}_{self.seq:06d}.jsonl"),
              45.0, "ledger-chunk-write")
        summary = self._summary(settlements)
        payload = json.dumps(summary, indent=2).encode()
        timed(lambda: storage.write_bytes(payload, f"{BUCKET_PREFIX}/summary/{day}.json"),
              30.0, "summary-write")
        timed(lambda: storage.write_bytes(payload, f"{BUCKET_PREFIX}/summary/latest.json"),
              30.0, "summary-latest-write")
        self._save_state()
        log.info("flush %d: %d records mirrored", self.seq, len(records))

    # ---- async plumbing (mirrors kalshi_rfq_daemon) ----
    async def run(self) -> int:
        if self.gate.killed:
            log.error("RFQ kill switch is SET (%s); no quotes until reset", self.gate.kill_reason)
        if self.bucket_killed():
            self.gate.trip("bucket KILL object present at startup")
            log.error("bucket KILL object present at startup: submissions halted")
        for what, fn in (("props", self.feed.refresh_props), ("banked games", self.feed.refresh_banked),
                         ("games", self.feed.refresh_games)):
            try:
                log.info("fair book after %s refresh: %d legs", what, fn())
            except Exception as e:                               # noqa: BLE001
                if self.mode == "live":
                    log.warning("%s feed unavailable at start (%r): starting with what loaded", what, e)
                else:
                    raise
        loop = asyncio.get_running_loop()
        stream = KalshiRFQStream("demo", self.key_id, load_private_key(self.pem), self._on_event)
        self._stream = stream

        async def poll():
            while True:
                try:
                    for rfq in await asyncio.to_thread(self.client.get_rfqs, "open"):
                        await self._on_event("rfq_created", rfq, time.time())
                except Exception as e:                           # noqa: BLE001
                    log.warning("rfq poll failed: %r", e)
                await asyncio.sleep(self.args.poll_sec)

        async def reload_feeds():
            while True:
                await asyncio.sleep(self.args.reload_sec)
                for fn in (self.feed.refresh_props, self.feed.refresh_banked):
                    try:
                        await asyncio.to_thread(fn)
                    except Exception as e:                       # noqa: BLE001
                        log.warning("feed reload failed (keeping previous): %r", e)

        async def reload_games():
            while True:
                await asyncio.sleep(self.game_reload_sec)
                try:
                    await asyncio.to_thread(self.feed.refresh_games)
                except Exception as e:                           # noqa: BLE001
                    log.warning("game legs reload failed (keeping previous): %r", e)

        async def flusher():
            while True:
                await asyncio.sleep(self.args.flush_sec)
                try:
                    if self.bucket_killed() and not self.gate.killed:
                        self.gate.trip("bucket KILL object present")
                        n = self.maker.cancel_all_open_quotes()
                        log.error("bucket KILL object appeared: tripped gate, cancelled %d quotes", n)
                    bal = timed(lambda: self.client.get_balance(), 30.0, "balance-read")
                    if bal is not None:
                        self._last_balance = float(bal.get("balance_dollars") or 0.0)
                    settlements = await asyncio.to_thread(self.grade_settlements)
                    self._pending_settlements += settlements
                    await asyncio.to_thread(self.flush)
                except Exception as e:                           # noqa: BLE001
                    log.warning("flush failed: %r", e)

        async def watchdog():
            await asyncio.sleep(120)
            if stream.connects == 0:
                raise RuntimeError("RFQ stream never connected in 120 s: egress blocked or credentials rejected")
            while True:
                log.info("heartbeat: connects=%d stats=%s book_legs=%d",
                         stream.connects, json.dumps(self.stats), len(self.book))
                await asyncio.sleep(300)

        async def deadline():
            if self.args.max_runtime_sec:
                await asyncio.sleep(self.args.max_runtime_sec)
                log.info("max runtime %.0fs reached: exiting cleanly", self.args.max_runtime_sec)
            else:
                await asyncio.Event().wait()

        main_task = asyncio.ensure_future(asyncio.gather(stream.run(), poll(), reload_feeds(),
                                                         reload_games(), flusher(), watchdog()))
        stop_task = asyncio.ensure_future(deadline())
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop_task.cancel)
            except (NotImplementedError, RuntimeError):
                pass
        try:
            done, _ = await asyncio.wait({main_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            if main_task in done:
                main_task.result()
        finally:
            stream.stop()
            for t in (main_task, stop_task):
                t.cancel()
            await asyncio.gather(main_task, stop_task, return_exceptions=True)
            if self.mode == "live":
                log.info("shutdown: cancelled %d open quotes", self.maker.cancel_all_open_quotes())
            try:
                self._pending_settlements += self.grade_settlements()
                timed(lambda: self.flush(), 120.0, "final-flush")
            except Exception as e:                               # noqa: BLE001
                log.warning("final flush failed: %r", e)
        return 0

    async def _on_event(self, typ, msg, ts):
        await asyncio.to_thread(self.on_event, typ, msg, ts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AUT-12 Kalshi DEMO paper taker (RFQ combo NO)")
    ap.add_argument("--mode", choices=["live", "shadow"], default="live")
    ap.add_argument("--runtime-dir", default=os.path.expanduser("~/workspace/kalshi-demo-paper-runtime"))
    ap.add_argument("--db", default=None)
    ap.add_argument("--feed", default="EVSweep/nfl_props/latest.json")
    ap.add_argument("--max-runtime-sec", type=float, default=0.0)
    ap.add_argument("--poll-sec", type=float, default=15.0)
    ap.add_argument("--reload-sec", type=float, default=300.0)
    ap.add_argument("--flush-sec", type=float, default=60.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.mode == "live" and os.environ.get("KALSHI_RFQ_LIVE") != "1":
        raise SystemExit("--mode live requires KALSHI_RFQ_LIVE=1")
    taker = DemoPaperTaker(args)
    assert_demo_client(taker.client)
    log.info("AUT-12 demo paper taker: mode=%s host=%s caps=$%.0f/$%.0f/$%.0f",
             args.mode, urlparse(taker.client.base_url).hostname,
             CAP_PER_COMBO, CAP_PER_PLAYER, CAP_PER_GAME)
    try:
        return asyncio.run(taker.run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
