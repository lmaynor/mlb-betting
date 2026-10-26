"""
mlb.execution.kalshi.rfq_maker -- stateful RFQ maker loop, RFQ risk gate and the last-look auto-confirmer.

Flow per event (all handlers are plain sync functions so they can be driven from the asyncio stream or from tests):
  rfq_created    -> price (ev.kalshi_rfq_pricer) -> RFQRiskGate -> [live] POST quote | [shadow] record only
  quote_accepted -> LAST LOOK: re-read the in-memory fair book, abort on a hot scratch / adverse move / exposure
                    breach / wrong accepted side / blown deadline; else PUT confirm inside the HVM window
  quote_executed -> mark filled
  rfq_deleted    -> expire our quote on it

Why aborting is not free: Kalshi counts a failure to confirm toward the same penalties as any other missed
confirmation and can temporarily rate-limit new quotes. The shield is therefore a last resort, and
`max_aborts_per_window` trips the kill switch if it is firing repeatedly (a sign the book is stale, not that
we are being sniped).

Safety (unchanged from the executor): demo only (RFQClient refuses everything else), `mode='live'` additionally
needs KALSHI_RFQ_LIVE=1, and the kill switch lives under its OWN key `kill_reason:kalshi_rfq` so it never halts the
other venues sharing a ledger. Like RiskGate, it is MANUAL-RESET-ONLY.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from mlb.execution.kalshi.client import KalshiAPIError
from mlb.execution.kalshi.ledger import Ledger, now_iso
from mlb.execution.kalshi.risk import KILL_KEY, RiskGate

log = logging.getLogger("kalshi_exec.rfq_maker")

LIVE_ENV_FLAG = "KALSHI_RFQ_LIVE"
ACTIVE_STATES = ("open", "accepted", "confirmed", "filled")
RFQ_KILL_KEY = KILL_KEY + ":kalshi_rfq"          # 'kill_reason:kalshi_rfq'


@dataclass(frozen=True)
class RFQConfig:
    max_liability_per_quote_dollars: float = 50.0     # contracts * bid, the collateral a fill locks
    max_exposure_per_game_dollars: float = 150.0
    max_exposure_per_player_dollars: float = 75.0     # across every open quote AND filled combo the player is in
    max_open_quotes: int = 25                         # Kalshi's own cap is 100
    last_look_max_adverse_move: float = 0.015
    game_cooldown_sec: float = 120.0
    confirm_budget_sec: float = 2.0                   # leave >= 1 s of the 3 s HVM window for the PUT itself
    max_aborts_per_window: int = 3
    abort_window_sec: float = 600.0


_EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS rfq_quotes (
    quote_id TEXT PRIMARY KEY, rfq_id TEXT NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL,
    ts TEXT NOT NULL, ts_epoch REAL NOT NULL, kind TEXT, market_ticker TEXT, held_side TEXT,
    yes_bid REAL, no_bid REAL, contracts REAL, collateral REAL, ev REAL, p_yes_true REAL,
    games_json TEXT, players_json TEXT, legs_json TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS ix_rfq_state ON rfq_quotes(mode, state);
CREATE INDEX IF NOT EXISTS ix_rfq_rfq ON rfq_quotes(rfq_id);
"""


class RFQLedger(Ledger):
    """The executor ledger plus the RFQ quote lifecycle table (same sqlite file, same kv kill-switch store)."""

    def __init__(self, path: str | None = None):
        super().__init__(path)
        with self._lock:
            self._c.executescript(_EXTRA_SCHEMA)
            self._c.commit()

    def add_quote(self, *, quote_id: str, rfq_id: str, mode: str, kind: str, market_ticker: str, held_side: str,
                  yes_bid: float, no_bid: float, contracts: float, collateral: float, ev, p_yes_true, legs: tuple,
                  ts_epoch: float, state: str = "open") -> None:
        games = sorted({l["game_key"] for l in legs})
        players = sorted({l["player_norm"] for l in legs if l.get("player_norm")})
        with self._lock:
            self._c.execute(
                "INSERT INTO rfq_quotes (quote_id, rfq_id, mode, state, ts, ts_epoch, kind, market_ticker, held_side,"
                " yes_bid, no_bid, contracts, collateral, ev, p_yes_true, games_json, players_json, legs_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (quote_id, rfq_id, mode, state, now_iso(), ts_epoch, kind, market_ticker, held_side, yes_bid, no_bid,
                 contracts, collateral, ev, p_yes_true, json.dumps(games), json.dumps(players),
                 json.dumps(list(legs), default=str)))
            self._c.commit()

    def set_state(self, quote_id: str, state: str, note: str | None = None) -> None:
        with self._lock:
            self._c.execute("UPDATE rfq_quotes SET state=?, note=COALESCE(?, note) WHERE quote_id=?",
                            (state, note, quote_id))
            self._c.commit()

    def set_held_side(self, quote_id: str, side: str) -> None:
        with self._lock:
            self._c.execute("UPDATE rfq_quotes SET held_side=? WHERE quote_id=?", (side, quote_id))
            self._c.commit()

    def get_quote(self, quote_id: str) -> dict | None:
        with self._lock:
            r = self._c.execute("SELECT * FROM rfq_quotes WHERE quote_id=?", (quote_id,)).fetchone()
        return self._row(r)

    def quote_for_rfq(self, rfq_id: str, mode: str) -> dict | None:
        with self._lock:
            r = self._c.execute("SELECT * FROM rfq_quotes WHERE rfq_id=? AND mode=? AND state IN (%s)"
                                % ",".join("?" * len(ACTIVE_STATES)), (rfq_id, mode, *ACTIVE_STATES)).fetchone()
        return self._row(r)

    def quotes_in(self, mode: str, states) -> list[dict]:
        states = tuple(states)
        with self._lock:
            rows = self._c.execute("SELECT * FROM rfq_quotes WHERE mode=? AND state IN (%s)" % ",".join("?" * len(states)),
                                   (mode, *states)).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for k in ("games_json", "players_json", "legs_json"):
            d[k[:-5]] = json.loads(d.pop(k) or "[]")
        return d

    def net_contracts(self, ticker: str, mode: str) -> float:
        """Signed YES contracts on a single-leg market from confirmed/filled quotes (+ long YES, - long NO)."""
        n = 0.0
        for q in self.quotes_in(mode, ("confirmed", "filled")):
            if q["kind"] == "single" and q["market_ticker"] == ticker:
                n += (q["contracts"] or 0.0) * (1.0 if q["held_side"] == "yes" else -1.0)
        return n


class RFQRiskGate(RiskGate):
    """Quote-level limits + an isolated kill switch (own key, like ev.novig.paper_orders.PaperRiskGate)."""
    _KEY = RFQ_KILL_KEY

    def __init__(self, cfg: RFQConfig, ledger: RFQLedger, mode: str):
        self.cfg, self.ledger, self.mode = cfg, ledger, mode
        self._consecutive_errors = 0

    @property
    def killed(self) -> bool:
        return self.ledger.kv_get(self._KEY) is not None

    @property
    def kill_reason(self) -> str | None:
        return self.ledger.kv_get(self._KEY)

    def trip(self, reason: str) -> None:
        if not self.killed:
            self.ledger.kv_set(self._KEY, reason)
            log.error("RFQ kill switch tripped: %s", reason)

    def reset(self) -> None:
        self.ledger.kv_del(self._KEY)
        self._consecutive_errors = 0

    def note_submit_error(self) -> None:
        self._consecutive_errors += 1
        if self._consecutive_errors >= 5:
            self.trip(f"{self._consecutive_errors} consecutive RFQ submit errors")

    def exposure(self) -> tuple[dict, dict]:
        """({game_key: dollars}, {player_norm: dollars}) over every active quote. A combo's whole collateral is
        charged to EACH of its games and players (conservative: one player cannot hide in five combos)."""
        games: dict = {}
        players: dict = {}
        for q in self.ledger.quotes_in(self.mode, ACTIVE_STATES):
            c = q["collateral"] or 0.0
            for g in q["games"]:
                games[g] = games.get(g, 0.0) + c
            for p in q["players"]:
                players[p] = players.get(p, 0.0) + c
        return games, players

    def _breach(self, games_g: dict, players_p: dict, add_games, add_players, add: float):
        cfg = self.cfg
        for g in add_games:
            if games_g.get(g, 0.0) + add > cfg.max_exposure_per_game_dollars + 1e-9:
                return "REJ_RISK_GAME"
        for p in add_players:
            if players_p.get(p, 0.0) + add > cfg.max_exposure_per_player_dollars + 1e-9:
                return "REJ_RISK_PLAYER"
        return None

    def check_quote(self, collateral: float, legs) -> tuple[bool, str]:
        """-> (allowed, reason). Reason is a REJ_* code when not allowed."""
        cfg = self.cfg
        if self.killed:
            return False, "REJ_KILLED"
        if collateral <= 0:
            return False, "REJ_RISK_SIZE_UNKNOWN"
        if collateral > cfg.max_liability_per_quote_dollars + 1e-9:
            return False, "REJ_RISK_SIZE"           # quotes are for the FULL RFQ size: cannot shrink to fit
        if len(self.ledger.quotes_in(self.mode, ("open",))) >= cfg.max_open_quotes:
            return False, "REJ_RISK_OPEN_QUOTES"
        g, p = self.exposure()
        bad = self._breach(g, p, {l["game_key"] for l in legs}, {l["player_norm"] for l in legs if l.get("player_norm")},
                           collateral)
        return (False, bad) if bad else (True, "")

    def check_confirm(self, q: dict) -> tuple[bool, str]:
        """Re-check the caps at confirm time. The quote's own collateral is already counted in `exposure()`, so
        the test is `total <= cap` with nothing added."""
        if self.killed:
            return False, "REJ_KILLED"
        g, p = self.exposure()
        bad = self._breach(g, p, q["games"], q["players"], 0.0)
        return (False, bad) if bad else (True, "")


class RFQMaker:
    def __init__(self, pricer, gate: RFQRiskGate, ledger: RFQLedger, client=None, mode: str = "shadow",
                 cfg: RFQConfig | None = None, clock=time.time, env=None):
        if mode not in ("shadow", "live"):
            raise ValueError(f"RFQMaker mode must be shadow|live, got {mode!r}")
        if mode == "live":
            if (os.environ if env is None else env).get(LIVE_ENV_FLAG) != "1":
                raise RuntimeError(f"live RFQ quoting needs {LIVE_ENV_FLAG}=1 in the environment")
            if client is None:
                raise RuntimeError("live RFQ quoting needs an RFQClient")
        self.pricer, self.gate, self.ledger, self.client = pricer, gate, ledger, client
        self.mode, self.cfg, self._clock = mode, cfg or gate.cfg, clock
        self._cooldown: dict[str, float] = {}
        self._aborts: list[float] = []
        self._lock = threading.RLock()          # handlers may run on worker threads; exposure checks must serialize

    # ---- helpers ----
    def _day(self) -> str:
        return datetime.fromtimestamp(self._clock(), timezone.utc).date().isoformat()

    def _record(self, outcome: str, rfq, decision=None, **inputs) -> None:
        d = decision
        self.ledger.record_decision(
            day=self._day(), mode=f"rfq_{self.mode}", outcome=outcome, reason=getattr(d, "reason", "") or "",
            intent_id=getattr(rfq, "rfq_id", None), ticker=getattr(rfq, "market_ticker", None),
            series=getattr(rfq, "collection", None), market=getattr(d, "kind", None),
            side=None if d is None else ("no" if d.yes_bid <= 0 < d.no_bid else "yes" if d.no_bid <= 0 < d.yes_bid else "both"),
            p_cons=getattr(d, "p_yes_cons", None), p_hat=getattr(d, "p_yes_true", None),
            n_books=len(getattr(d, "legs", ()) or ()), ev=getattr(d, "ev_per_contract", None),
            contracts=getattr(d, "contracts", None), limit_price=None if d is None else max(d.yes_bid, d.no_bid),
            inputs=dict(inputs, legs=list(getattr(d, "legs", ()) or ()), notes=getattr(d, "notes", {})))

    def inventory(self, ticker: str) -> float:
        return self.ledger.net_contracts(ticker, self.mode)

    def _cooling(self, game_keys) -> str | None:
        now = self._clock()
        return next((g for g in game_keys if self._cooldown.get(g, 0.0) > now), None)

    # ---- dispatch ----
    def on_event(self, typ: str, msg: dict, received_ts: float | None = None):
        received_ts = self._clock() if received_ts is None else received_ts
        with self._lock:
            if typ == "rfq_created":
                return self.on_rfq_created(msg, received_ts)
            if typ == "quote_accepted":
                return self.on_quote_accepted(msg, received_ts)
            if typ == "quote_executed":
                return self.on_quote_executed(msg)
            if typ == "rfq_deleted":
                return self.on_rfq_deleted(msg)
        return None

    # ---- rfq_created ----
    def on_rfq_created(self, msg: dict, received_ts: float | None = None):
        from ev.kalshi_rfq_pricer import parse_rfq
        now = self._clock()
        rfq = parse_rfq(msg)
        if not rfq.rfq_id:
            return None
        if self.gate.killed:
            self._record("REJ_KILLED", rfq)
            return None
        if self.ledger.quote_for_rfq(rfq.rfq_id, self.mode):
            return None                                                     # already quoted this RFQ
        d = self.pricer.price(rfq, now)
        if not d.quoted:
            self._record(d.reason, rfq, d)
            return d
        cool = self._cooling({l["game_key"] for l in d.legs})
        if cool:
            self._record("REJ_GAME_COOLDOWN", rfq, d, game_key=cool)
            return replace(d, action="REJECT", reason="REJ_GAME_COOLDOWN")
        ok, why = self.gate.check_quote(d.collateral, d.legs)
        if not ok:
            self._record(why, rfq, d)
            return replace(d, action="REJECT", reason=why)
        held = "no" if d.yes_bid <= 0 else "yes" if d.no_bid <= 0 else "both"
        if self.mode == "live":
            try:
                resp = self.client.create_quote(rfq.rfq_id, d.yes_bid_fp, d.no_bid_fp)
            except KalshiAPIError as e:
                if e.status == 429:
                    self._rate_limited("create_quote 429")
                else:
                    self.gate.note_submit_error()
                self._record("REJ_SUBMIT_ERROR", rfq, d, error=str(e)[:200])
                return replace(d, action="REJECT", reason="REJ_SUBMIT_ERROR")
            except Exception as e:                                          # noqa: BLE001 -- ambiguous: never resend
                self.gate.note_submit_error()
                self._record("REJ_SUBMIT_ERROR", rfq, d, error=repr(e)[:200])
                return replace(d, action="REJECT", reason="REJ_SUBMIT_ERROR")
            self.gate.note_submit_ok()
            quote_id = str((resp or {}).get("id") or "")
            if not quote_id:
                self._record("REJ_SUBMIT_ERROR", rfq, d, error="no quote id in response")
                return replace(d, action="REJECT", reason="REJ_SUBMIT_ERROR")
        else:
            quote_id = f"shadow-{rfq.rfq_id}"
        self.ledger.add_quote(quote_id=quote_id, rfq_id=rfq.rfq_id, mode=self.mode, kind=d.kind,
                              market_ticker=rfq.market_ticker, held_side=held, yes_bid=d.yes_bid, no_bid=d.no_bid,
                              contracts=d.contracts, collateral=d.collateral, ev=d.ev_per_contract,
                              p_yes_true=d.p_yes_true, legs=d.legs, ts_epoch=now)
        self._record("PLAN", rfq, d, quote_id=quote_id)
        return d

    # ---- last look ----
    def on_quote_accepted(self, msg: dict, received_ts: float | None = None) -> str:
        t0 = self._clock() if received_ts is None else received_ts
        quote_id = str(msg.get("quote_id") or "")
        q = self.ledger.get_quote(quote_id)
        if q is None or q["state"] != "open":
            return "IGNORED"
        self.ledger.set_state(quote_id, "accepted")
        reason, side = self._last_look(q, msg, t0)
        if reason:
            self._abort(q, reason)
            return reason
        self.ledger.set_held_side(quote_id, side)           # a two-sided single quote only learns its side now
        if self.mode == "shadow":
            self.ledger.set_state(quote_id, "confirmed", "shadow")
            self._record("CONFIRMED", _Stub(q), None, quote_id=quote_id, latency_ms=0.0, shadow=True)
            return "CONFIRMED"
        try:
            self.client.confirm_quote(quote_id, q["rfq_id"])
        except KalshiAPIError as e:
            if e.status == 429:
                self._rate_limited("confirm 429")
            self.ledger.set_state(quote_id, "confirm_failed", str(e)[:200])
            self.gate.trip(f"missed confirmation on {quote_id}: HTTP {e.status}")
            self._record("CONFIRM_FAILED", _Stub(q), None, quote_id=quote_id, error=str(e)[:200])
            return "CONFIRM_FAILED"
        except Exception as e:                                              # noqa: BLE001 -- timeout / connection
            self.ledger.set_state(quote_id, "confirm_failed", repr(e)[:200])
            self.gate.trip(f"missed confirmation on {quote_id}: {e!r}")
            self._record("CONFIRM_FAILED", _Stub(q), None, quote_id=quote_id, error=repr(e)[:200])
            return "CONFIRM_FAILED"
        latency_ms = round((self._clock() - t0) * 1000.0, 1)
        self.ledger.set_state(quote_id, "confirmed", f"latency_ms={latency_ms}")
        self._record("CONFIRMED", _Stub(q), None, quote_id=quote_id, latency_ms=latency_ms)
        log.info("RFQ confirmed %s latency_ms=%s", quote_id, latency_ms)
        return "CONFIRMED"

    def _last_look(self, q: dict, msg: dict, t0: float) -> tuple[str | None, str]:
        """-> (ABORT_* code | None, accepted side). Pure in-memory work (no network)."""
        cfg, now = self.cfg, self._clock()
        side = ""
        if self.gate.killed:
            return "ABORT_KILLED", side
        bids = {"yes": q["yes_bid"] or 0.0, "no": q["no_bid"] or 0.0}
        side = str(msg.get("accepted_side") or "").lower()
        if side not in ("yes", "no"):
            live_sides = [s for s, v in bids.items() if v > 0]
            if len(live_sides) != 1:
                return "ABORT_SIDE_UNKNOWN", side
            side = live_sides[0]
        if bids[side] <= 0:
            return "ABORT_SIDE_MISMATCH", side                  # accepted a side we never bid on: our reading of the field is wrong
        if now - t0 > cfg.confirm_budget_sec:
            return "ABORT_TOO_LATE", side
        short_combo = q["kind"] == "combo"                # we hold NO of the combo: any leg getting likelier hurts
        for snap in q["legs"]:
            fv = self.pricer.book.get(snap["ticker"], now)
            bad = self.pricer.leg_gate(fv, now)
            if bad == "REJ_HOT_LINEUP_SCRATCH":
                return "ABORT_HOT_SCRATCH", side
            if bad:
                return "ABORT_LEG_INVALID", side
            if short_combo:
                p_now = fv.p_true_yes if snap["side"] == "yes" else 1.0 - fv.p_true_yes
                adverse = p_now - snap["p_leg"]
            else:                                         # single leg: adverse = moves against the side we now hold
                adverse = (snap["p_true_yes"] - fv.p_true_yes) if side == "yes" else (fv.p_true_yes - snap["p_true_yes"])
            if adverse > cfg.last_look_max_adverse_move + 1e-12:
                return "ABORT_ADVERSE_MOVE", side
        ok, _ = self.gate.check_confirm(q)
        return (None if ok else "ABORT_RISK"), side

    def _abort(self, q: dict, reason: str) -> None:
        now = self._clock()
        self.ledger.set_state(q["quote_id"], "aborted", reason)
        for g in q["games"]:
            self._cooldown[g] = now + self.cfg.game_cooldown_sec
        for other in self.ledger.quotes_in(self.mode, ("open",)):
            if set(other["games"]) & set(q["games"]):
                self._cancel(other)
        self._aborts = [t for t in self._aborts if now - t < self.cfg.abort_window_sec] + [now]
        if len(self._aborts) >= self.cfg.max_aborts_per_window:
            self.gate.trip(f"{len(self._aborts)} last-look aborts in {self.cfg.abort_window_sec:.0f}s (book stale?)")
        self._record(reason, _Stub(q), None, quote_id=q["quote_id"])
        log.warning("RFQ last-look ABORT %s %s", q["quote_id"], reason)

    def _cancel(self, q: dict) -> bool:
        if self.mode == "live" and self.client is not None:
            try:
                self.client.delete_quote(q["quote_id"])
            except Exception as e:                                          # noqa: BLE001 -- best effort; expiry covers it
                log.warning("delete_quote %s failed: %r", q["quote_id"], e)
        self.ledger.set_state(q["quote_id"], "cancelled")
        return True

    def cancel_all_open_quotes(self) -> int:
        n = 0
        for q in self.ledger.quotes_in(self.mode, ("open",)):
            n += bool(self._cancel(q))
        return n

    def _rate_limited(self, why: str) -> None:
        self.gate.trip(f"HTTP 429: {why}")
        self.cancel_all_open_quotes()

    # ---- remaining events ----
    def on_quote_executed(self, msg: dict) -> str:
        q = self.ledger.get_quote(str(msg.get("quote_id") or ""))
        if q is None or q["state"] not in ("confirmed", "accepted", "open"):
            return "IGNORED"
        self.ledger.set_state(q["quote_id"], "filled")
        held = q["held_side"]
        price = q["no_bid"] if held == "no" else q["yes_bid"]
        self.ledger.record_fill(fill_id=str(msg.get("order_id") or q["quote_id"]), order_id=str(msg.get("order_id") or ""),
                                ticker=q["market_ticker"], side=held, count=q["contracts"] or 0.0, price=price or 0.0,
                                fee=0.0, ts=now_iso(), mode=f"rfq_{self.mode}")
        return "FILLED"

    def on_rfq_deleted(self, msg: dict) -> str:
        q = self.ledger.quote_for_rfq(str(msg.get("id") or msg.get("rfq_id") or ""), self.mode)
        if q is not None and q["state"] == "open":
            self.ledger.set_state(q["quote_id"], "expired")
            return "EXPIRED"
        return "IGNORED"


@dataclass(frozen=True)
class _Stub:
    """Adapts a stored quote row to the (rfq_id, market_ticker, collection) shape _record reads."""
    q: dict

    @property
    def rfq_id(self):
        return self.q["rfq_id"]

    @property
    def market_ticker(self):
        return self.q["market_ticker"]

    collection = None


# ---------------------------------------------------------------------------------------------- tape
class RFQTape:
    """Append-only JSONL tape (data/kalshi_rfq/rfq_tape_{day}.jsonl): every RFQ seen, our would-be quote, and -- once
    the follow-up runs -- what actually traded on that market afterwards. Costs $0 at risk and measures our
    theoretical market share and mark-out before any live quote exists."""

    def __init__(self, directory: str = "data/kalshi_rfq", clock=time.time, *, storage_prefix: str | None = None,
                 writer=None, flush_sec: float = 30.0):
        """Local JSONL by default. With `storage_prefix` (e.g. 'Kalshi/rfq_tape/production') records are buffered and
        written as immutable chunk objects {prefix}/{day}/{HHMMSS}_{n}.jsonl through `writer(bytes, key)` (default
        mlb_core.storage.write_bytes): GCS objects are not appendable and a Cloud Run Job's disk dies with it, so the
        buffer is flushed every `flush_sec` and by flush() at shutdown. A failed write keeps the buffer for the next try."""
        self.dir, self._clock = directory, clock
        self.prefix, self._writer, self.flush_sec = storage_prefix, writer, flush_sec
        self._buf: list[str] = []
        self._seq = 0
        self._last_flush = clock()
        self._lock = threading.Lock()

    def path(self) -> str:
        day = datetime.fromtimestamp(self._clock(), timezone.utc).date().isoformat()
        return os.path.join(self.dir, f"rfq_tape_{day}.jsonl")

    def append(self, record: dict) -> None:
        line = json.dumps(record, default=str, sort_keys=True, ensure_ascii=True) + "\n"
        if self.prefix is None:
            os.makedirs(self.dir, exist_ok=True)
            with open(self.path(), "a", encoding="ascii") as fh:
                fh.write(line)
            return
        with self._lock:
            self._buf.append(line)
            due = self._clock() - self._last_flush >= self.flush_sec
        if due:
            self.flush()

    def flush(self) -> int:
        """Write the buffered records as one chunk object. -> records written (0 when empty / local mode / failed)."""
        if self.prefix is None:
            return 0
        with self._lock:
            if not self._buf:
                self._last_flush = self._clock()
                return 0
            lines, self._buf = self._buf, []
            self._seq += 1
            seq = self._seq
        ts = datetime.fromtimestamp(self._clock(), timezone.utc)
        key = f"{self.prefix}/{ts.date().isoformat()}/{ts.strftime('%H%M%S')}_{seq:05d}.jsonl"
        try:
            writer = self._writer
            if writer is None:
                from mlb_core.storage import write_bytes as writer
            writer("".join(lines).encode("ascii"), key)
        except Exception as e:                                          # noqa: BLE001 -- keep the records, retry next flush
            log.warning("tape flush to %s failed (%d records kept): %r", key, len(lines), e)
            with self._lock:
                self._buf = lines + self._buf
            return 0
        self._last_flush = self._clock()
        return len(lines)

    def record_rfq(self, rfq_msg: dict, decision) -> None:
        self.append({"kind": "rfq", "ts": self._clock(), "rfq": rfq_msg, "plan": decision.action,
                     "reason": decision.reason, "yes_bid": decision.yes_bid, "no_bid": decision.no_bid,
                     "p_yes_true": decision.p_yes_true, "p_yes_cons": decision.p_yes_cons,
                     "ev_per_contract": decision.ev_per_contract, "roi": decision.roi_on_collateral,
                     "legs": list(decision.legs), "notes": decision.notes})

    def record_trades(self, rfq_id: str, market_ticker: str, trades: list[dict], since_ts: float) -> None:
        self.append({"kind": "followup", "ts": self._clock(), "rfq_id": rfq_id, "market_ticker": market_ticker,
                     "since_ts": since_ts, "trades": trades})


def fetch_trades_after(client, market_ticker: str, since_ts: float) -> list[dict]:
    """Public trade tape for one market since `since_ts` (unsigned GET). Used 15 s after an RFQ is seen."""
    data = client.request("GET", "/markets/trades", params={"ticker": market_ticker, "min_ts": int(since_ts),
                                                            "limit": 100}, auth=False)
    return data.get("trades", []) or []


class RFQRecorder:
    """--mode record: price every RFQ against the fair book, write it to the tape, and 15 s later pull the public
    trade tape for that market. Never quotes, never touches the ledger, never needs a risk gate."""
    FOLLOWUP_SEC = 15.0

    def __init__(self, pricer, tape: RFQTape, client=None, schedule=None, clock=time.time):
        self.pricer, self.tape, self.client, self._clock = pricer, tape, client, clock
        self._schedule = schedule            # schedule(delay_sec, fn); the daemon injects loop.call_later
        self._seen: set[str] = set()

    def on_event(self, typ: str, msg: dict, received_ts: float | None = None):
        if typ != "rfq_created":
            return None
        from ev.kalshi_rfq_pricer import parse_rfq
        rfq = parse_rfq(msg)
        if not rfq.rfq_id or rfq.rfq_id in self._seen:
            return None
        self._seen.add(rfq.rfq_id)
        now = self._clock()
        d = self.pricer.price(rfq, now)
        self.tape.record_rfq(msg, d)
        if self.client is not None and self._schedule is not None:
            self._schedule(self.FOLLOWUP_SEC, lambda: self._followup(rfq, now))
        return d

    def _followup(self, rfq, since_ts: float) -> None:
        try:
            trades = fetch_trades_after(self.client, rfq.market_ticker, since_ts)
        except Exception as e:                                          # noqa: BLE001 -- evidence only, never fatal
            trades = [{"error": repr(e)[:200]}]
        self.tape.record_trades(rfq.rfq_id, rfq.market_ticker, trades, since_ts)
