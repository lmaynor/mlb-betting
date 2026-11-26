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

import hashlib
import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from ev.kalshi_rfq_filter import FilterConfig, RFQFilterState, classify_rfq_record
from mlb.execution.kalshi.client import KalshiAPIError
from mlb.execution.kalshi.fees import taker_fee
from mlb.execution.kalshi.ledger import Ledger, now_iso
from mlb.execution.kalshi.risk import KILL_KEY, RiskGate

log = logging.getLogger("kalshi_exec.rfq_maker")

LIVE_ENV_FLAG = "KALSHI_RFQ_LIVE"
ACTIVE_STATES = ("open", "accepted", "confirmed", "filled")
RFQ_KILL_KEY = KILL_KEY + ":kalshi_rfq"          # 'kill_reason:kalshi_rfq'


@dataclass(frozen=True)
class RFQConfig:
    # SCAFFOLDING values (2026-11-01, owner decision): loosened so the demo exchange exercises the whole pipeline. Quoting is
    # demo-only (production is refused in code), so these cap play money. The real gates are to be set later, before any
    # production quoting exists; the old values were 50 / 150 / 75 / 25.
    max_liability_per_quote_dollars: float = 1000.0   # contracts * bid, the collateral a fill locks
    max_exposure_per_game_dollars: float = 5000.0
    max_exposure_per_player_dollars: float = 2500.0   # across every open quote AND filled combo the player is in
    max_open_quotes: int = 100                        # Kalshi's own cap is 100
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
# Tape schema v2 canonicalization tables, mirrored from the RES-17 settlement fix in
# ev/betmgm_under_shadow.py (SETTLE_SUFFIX_TOKENS and NHL_TEAM_DIALECT, lines ~147-155 there): the tape
# canonicalizes player / team identity AT WRITE TIME so settlement joins are equalities on stored keys,
# not re-derivations per reader (rfq-tape-storage-design-2026-11-24.md section 2g).
SETTLE_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv", "v"}
TEAM_DIALECT = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL"}

# Per-leg family labels, mirroring ev.combo_taker_window2.leg_family semantics.
_SPORT_TO_FAMILY = {"nba": "NBA_PROP", "nhl": "NHL_PROP", "nfl": "NFL_PROP"}


def _canon_player(name) -> str | None:
    """player_norm with trailing generational suffix tokens stripped (case-insensitive, token-wise,
    repeatedly; a trailing '.' or ',' on the suffix token is ignored). None stays None."""
    if not name:
        return None
    tokens = str(name).split()
    while tokens and tokens[-1].strip(".,").lower() in SETTLE_SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def _canon_team(code) -> str | None:
    """Team code mapped through the RES-17 dialect table; unmapped codes pass through, None stays None."""
    if not code:
        return None
    return TEAM_DIALECT.get(str(code), str(code))


def _series_of(ticker) -> str:
    """The series prefix of a Kalshi ticker: everything before the first '-'."""
    return str(ticker or "").split("-", 1)[0]


def _leg_family_label(sport, player_norm) -> str | None:
    """ev.combo_taker_window2.leg_family semantics without the raise: a player leg maps by sport
    (nba -> NBA_PROP, nhl -> NHL_PROP, nfl -> NFL_PROP); an unknown sport WITH a player returns None
    (the tape never raises on flow it is only recording); a leg with no player is a GAME_LINE."""
    if player_norm:
        return _SPORT_TO_FAMILY.get(str(sport or "").lower())
    return "GAME_LINE"


def _excluded_series_class(tickers) -> str:
    """Ratified Window 1 denominator exclusion (pre-registration item 10): 'KXNBA1Q' / 'KXNCAAFGAME' /
    'both' / 'none', by the series prefix of the given tickers."""
    series = {_series_of(t) for t in tickers if t}
    nba1q = "KXNBA1Q" in series
    ncaaf = "KXNCAAFGAME" in series
    if nba1q and ncaaf:
        return "both"
    if nba1q:
        return "KXNBA1Q"
    if ncaaf:
        return "KXNCAAFGAME"
    return "none"


def _decimal_or_none(x):
    if x is None:
        return None
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return None


def _float_or_none(x):
    d = _decimal_or_none(x)
    return float(d) if d is not None else None


def _w2_day_of(legs) -> str | None:
    """Window 2 pool bucket: the min parseable YYYY-MM-DD date across the decision legs' game_key values
    shaped '{sport}:{date}:{...}', else None."""
    days = []
    for leg in legs or ():
        parts = str((leg or {}).get("game_key") or "").split(":")
        if len(parts) >= 2:
            try:
                days.append(datetime.strptime(parts[1], "%Y-%m-%d").date().isoformat())
            except ValueError:
                continue
    return min(days) if days else None


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
        # Tape schema v2 run context (set by the daemon at startup; stamped on the kind:"run" header).
        self._git_sha: str | None = None
        self._config_hash: str | None = None
        self._book_build_id: str | None = None
        self._run_written = False
        # Creator rolling state (design section 2f): per-creator deque of receipt ts + flag episode start.
        self._creator_ts: dict[str, deque] = {}
        self._creator_flag_since: dict[str, float] = {}
        # Day summary accumulators (design section 2i), keyed by UTC day.
        self._sum_day: str | None = None
        self._sum = self._fresh_summary()
        # Ratified RUL-20261125 ingest filter (Window-1 shadow mode).
        self._filter_state = RFQFilterState()
        self._filter_cfg = FilterConfig()

    def path(self) -> str:
        day = datetime.fromtimestamp(self._clock(), timezone.utc).date().isoformat()
        return os.path.join(self.dir, f"rfq_tape_{day}.jsonl")

    # ---- tape schema v2: run context, creator rolling state, day summary ----
    def set_run_context(self, git_sha, config_hash) -> None:
        self._git_sha, self._config_hash = git_sha, config_hash

    def set_book_build_id(self, x) -> None:
        self._book_build_id = x

    def record_run(self, extra: dict) -> None:
        """Write the kind:"run" header exactly once (subsequent calls are no-ops): code + config + book
        identity for every record that follows, per design section 2."""
        if self._run_written:
            return
        self._run_written = True
        rec = {"kind": "run", "tape_schema": 2, "ts": self._clock(), "git_sha": self._git_sha,
               "config_hash": self._config_hash, "book_build_id": self._book_build_id}
        rec.update(extra or {})
        self.append(rec)

    def _creator_state(self, creator_id: str, ts: float) -> tuple[int, bool, float | None]:
        """Rolling 10-min creator counts as of this record (design section 2f). The ratified spam rule:
        >= 60 requests in a rolling 10-min window sustained across 2 consecutive windows, i.e. the
        trailing [ts-600, ts] count AND the preceding [ts-1200, ts-600) count are both >= 60."""
        dq = self._creator_ts.setdefault(creator_id, deque())
        dq.append(ts)
        while dq and dq[0] < ts - 1200.0:
            dq.popleft()
        count_10m = sum(1 for t in dq if ts - 600.0 <= t <= ts)
        count_prev = sum(1 for t in dq if ts - 1200.0 <= t < ts - 600.0)
        flagged = count_prev >= 60 and count_10m >= 60
        if flagged:
            since = self._creator_flag_since.setdefault(creator_id, ts)
            return count_10m, True, since
        self._creator_flag_since.pop(creator_id, None)
        return count_10m, False, None

    @staticmethod
    def _fresh_summary() -> dict:
        return {"records_by_kind": {}, "fires": 0, "evaluated": 0, "in_denominator": 0,
                "by_family": {}, "by_excluded_series_class": {}, "preview_10": 0,
                "followup_records": 0, "creator_counts": {}, "flagged_creators": {},
                "by_primary_class": {}, "by_disposition": {}, "fires_deduped": 0}

    def _account(self, record: dict) -> None:
        """Day-summary accounting (design section 2i) on every append, with automatic rollover: when the
        record's UTC day differs from the accumulated day, the finished day's summary is written first."""
        try:
            ts = float(record.get("ts") or self._clock())
        except (TypeError, ValueError):
            ts = self._clock()
        day = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
        if self._sum_day is None:
            self._sum_day = day
        elif day != self._sum_day:
            self.write_day_summary(self._sum_day)
            self._sum_day = day
            self._sum = self._fresh_summary()
        s = self._sum
        kind = str(record.get("kind") or "")
        s["records_by_kind"][kind] = s["records_by_kind"].get(kind, 0) + 1
        if kind == "followup":
            s["followup_records"] += 1
        if kind != "rfq":
            return
        s["evaluated"] += 1
        s["fires"] += 1 if record.get("is_fire") else 0
        s["in_denominator"] += 1 if record.get("in_denominator") else 0
        s["preview_10"] += 1 if record.get("preview_10") else 0
        fam = record.get("family")
        s["by_family"][str(fam)] = s["by_family"].get(str(fam), 0) + 1
        exc = str(record.get("excluded_series_class") or "none")
        s["by_excluded_series_class"][exc] = s["by_excluded_series_class"].get(exc, 0) + 1
        cid = record.get("creator_id")
        if cid:
            s["creator_counts"][str(cid)] = s["creator_counts"].get(str(cid), 0) + 1
            if record.get("creator_flagged"):
                s["flagged_creators"][str(cid)] = s["creator_counts"][str(cid)]
        pc = str(record.get("primary_class") or "UNCLASSIFIED")
        s["by_primary_class"][pc] = s["by_primary_class"].get(pc, 0) + 1
        disp = str(record.get("disposition") or "UNCLASSIFIED")
        s["by_disposition"][disp] = s["by_disposition"].get(disp, 0) + 1
        if record.get("disposition") == "ADMITTED_FIRE" and not record.get("is_dup_creator_combo"):
            s["fires_deduped"] += 1

    def day_summary(self, day: str | None = None) -> dict:
        """The accumulated section 2i counters for `day` (default: the current accumulated day)."""
        s = self._sum
        evaluated = s["evaluated"]
        top = sorted(s["creator_counts"].items(), key=lambda kv: (-kv[1], kv[0]))[:20]
        return {"day": day or self._sum_day, "tape_schema": 2,
                "records_by_kind": dict(s["records_by_kind"]), "fires": s["fires"],
                "evaluated": evaluated, "in_denominator": s["in_denominator"],
                "by_family": dict(s["by_family"]),
                "by_excluded_series_class": dict(s["by_excluded_series_class"]),
                "preview_10": s["preview_10"],
                "preview_10_share": (s["preview_10"] / evaluated) if evaluated else 0.0,
                "flagged_creators": dict(s["flagged_creators"]),
                "top_creators": [{"creator_id": c, "count": n} for c, n in top],
                "followup_records": s["followup_records"],
                "followup_attach_rate": (s["followup_records"] / evaluated) if evaluated else 0.0,
                "by_primary_class": dict(s["by_primary_class"]),
                "by_disposition": dict(s["by_disposition"]),
                "fires_deduped": s["fires_deduped"]}

    def write_day_summary(self, day: str | None = None) -> dict | None:
        """Write the day summary sidecar: {prefix}/{day}/day_summary.json in storage mode (through the
        same writer as the chunks), {dir}/day_summary_{day}.json locally. Best-effort: failures are
        logged, never raised. Tracked at day level only; the per-rfq attach rate is the derived
        layer's job."""
        day = day or self._sum_day or datetime.fromtimestamp(self._clock(), timezone.utc).date().isoformat()
        summary = self.day_summary(day)
        try:
            payload = (json.dumps(summary, default=str, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")
            if self.prefix is None:
                os.makedirs(self.dir, exist_ok=True)
                with open(os.path.join(self.dir, f"day_summary_{day}.json"), "wb") as fh:
                    fh.write(payload)
            else:
                writer = self._writer
                if writer is None:
                    from mlb_core.storage import write_bytes as writer
                writer(payload, f"{self.prefix}/{day}/day_summary.json")
        except Exception as e:                                          # noqa: BLE001 -- sidecar, never fatal
            log.warning("day summary write for %s failed: %r", day, e)
            return None
        return summary

    def append(self, record: dict) -> None:
        self._account(record)
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

    def record_rfq(self, rfq_msg: dict, decision, *, q_by_ticker=None) -> None:
        """One kind:"rfq" record: the v1 payload byte-identical in name and value, plus the tape schema
        v2 fields materialized at write time (design sections 2a-2g). Null means the component is not
        knowable at record time in the recorder path -- never fabricated, never 0."""
        ts = self._clock()
        rfq_msg = rfq_msg or {}
        raw_legs = rfq_msg.get("mve_selected_legs") or rfq_msg.get("legs") or []
        leg_tickers = [str(l.get("market_ticker") or l.get("ticker") or "") for l in raw_legs if isinstance(l, dict)]
        market_ticker = str(rfq_msg.get("market_ticker") or "")
        # Hoisted scalars (2a).
        target_dec = _decimal_or_none(rfq_msg.get("target_cost_dollars"))
        # $10-preview exclusion (2b): exact DECIMAL compare ("10.0000" == 10.00), never a float compare;
        # an absent field is the contract-sized class, not a preview.
        preview_10 = target_dec is not None and target_dec == Decimal("10.00")
        excluded = _excluded_series_class(leg_tickers or [market_ticker])
        if raw_legs:
            pairs = sorted(f"{t}|{str(l.get('side') or '').lower()}"
                           for l, t in zip(raw_legs, leg_tickers) if isinstance(l, dict))
            combo_id = hashlib.sha256(",".join(pairs).encode("ascii", "replace")).hexdigest()
        else:
            combo_id = hashlib.sha256(market_ticker.encode("ascii", "replace")).hexdigest()
        creator_id = rfq_msg.get("creator_id")
        if creator_id:
            creator_count_10m, creator_flagged, creator_flag_since_ts = self._creator_state(str(creator_id), ts)
        else:
            creator_count_10m, creator_flagged, creator_flag_since_ts = None, False, None
        # Money (2e): the NO ask the ROI was computed on, and the canonical taker fee on it.
        ask_no = decision.no_bid if decision.action == "QUOTE" else None
        fee = taker_fee(ask_no, 1.0) if ask_no else None
        # Combo family (2b): under a cold trust book every family is ACCRUING, so the weakest-link label
        # is the FIRST leg's label in leg order; per-leg families are stored below so scoring can
        # recompute the exact weakest link against the real trust book.
        dlegs = list(decision.legs or ())
        family = _leg_family_label(dlegs[0].get("sport"), dlegs[0].get("player_norm")) if dlegs else None
        legs = [self._enrich_leg(snap, ts, q_by_ticker) for snap in dlegs]
        if q_by_ticker is None:
            leg_quotes = None
        else:
            leg_quotes = {}
            for tk in dict.fromkeys(t for t in leg_tickers if t):
                q_ts, q_bid, q_ask = self._unpack_quote(q_by_ticker.get(tk))
                leg_quotes[tk] = {"ts": q_ts, "bid": q_bid, "ask": q_ask}
        record = {
            # v1 payload, unchanged.
            "kind": "rfq", "ts": ts, "rfq": rfq_msg, "plan": decision.action,
            "reason": decision.reason, "yes_bid": decision.yes_bid, "no_bid": decision.no_bid,
            "p_yes_true": decision.p_yes_true, "p_yes_cons": decision.p_yes_cons,
            "ev_per_contract": decision.ev_per_contract, "roi": decision.roi_on_collateral,
            "legs": legs, "leg_quotes": leg_quotes, "notes": decision.notes,
            # v2 additions.
            "tape_schema": 2,
            "book_build_id": self._book_build_id,
            "rfq_id": str(rfq_msg.get("id") or rfq_msg.get("rfq_id") or ""),
            "creator_id": creator_id,
            # Kalshi's own timestamp when the message carries one; null when absent, never backfilled
            # with our receipt ts (absence is itself the recorded fact).
            "created_ts": rfq_msg.get("created_ts"),
            "target_cost_dollars_num": float(target_dec) if target_dec is not None else None,
            "contracts_fp_num": _float_or_none(rfq_msg.get("contracts_fp", rfq_msg.get("contracts"))),
            "n_legs": len(raw_legs) if raw_legs else 1,
            "is_combo": bool(raw_legs),
            "collection_ticker": rfq_msg.get("mve_collection_ticker"),
            "combo_id": combo_id,
            "preview_10": preview_10,
            "excluded_series_class": excluded,
            "in_denominator": (not preview_10 and excluded == "none"),
            "family": family,
            # tier is decided by the Window 2 admission path, not at record time: null here.
            "tier": None,
            # Record-mode fire: the pricing engine would have quoted this RFQ.
            "is_fire": decision.action == "QUOTE",
            "bounded": bool((decision.notes or {}).get("bounded")),
            # Synthetic-start timing is a harness concept; the recorder's fair book carries no
            # commence provenance, so timing_approx is not knowable at record time: null.
            "timing_approx": None,
            "day_utc": datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
            "day_et": datetime.fromtimestamp(ts, ZoneInfo("America/New_York")).date().isoformat(),
            "w2_day": _w2_day_of(dlegs),
            # LegFairValue carries no commence time, so neither earliest commence nor mins-to-tip can
            # be computed at record time: both null.
            "earliest_commence_ts": None,
            "mins_to_tip_min": None,
            "ask_no": ask_no,
            "taker_fee_per_contract": fee,
            "creator_count_10m": creator_count_10m,
            "creator_flagged": creator_flagged,
            "creator_flag_since_ts": creator_flag_since_ts,
        }
        try:
            record.update(classify_rfq_record(record, self._filter_state, self._filter_cfg))
        except Exception as e:                                      # noqa: BLE001 -- classifier must never break recording
            log.warning("RFQ filter classification failed for %s: %r", record.get("rfq_id"), e)
            record.setdefault("primary_class", "UNCLASSIFIED")
        self.append(record)

    @staticmethod
    def _unpack_quote(q) -> tuple:
        """Normalize a q_by_ticker entry ({ts, bid, ask} dict or (ts, bid, ask) tuple) to (ts, bid, ask)."""
        if isinstance(q, (tuple, list)):
            q_ts, q_bid, q_ask = (list(q) + [None, None, None])[:3]
            return q_ts, q_bid, q_ask
        if isinstance(q, dict):
            return q.get("ts"), q.get("bid"), q.get("ask")
        return None, None, None

    def _enrich_leg(self, snap: dict, ts: float, q_by_ticker) -> dict:
        """One decision leg snapshot (already enriched by ev.kalshi_rfq_pricer._snap) plus the v2 tape
        fields: series, per-leg family, fair-value age, canonical settlement identity (2g) and the
        Kalshi single-leg quote captured at intake (2d)."""
        leg = dict(snap or {})
        ticker = leg.get("ticker")
        series = _series_of(ticker)
        player_canon = _canon_player(leg.get("player_norm"))
        team_canon = _canon_team(leg.get("team"))
        fv_ts = leg.get("fv_updated_ts")
        q = (q_by_ticker or {}).get(ticker) if ticker else None
        q_ts, q_bid, q_ask = self._unpack_quote(q)
        if q_bid is not None and q_ask is not None:
            q_mid = (float(q_bid) + float(q_ask)) / 2.0
        elif q_bid is not None:
            q_mid = float(q_bid)
        elif q_ask is not None:
            q_mid = float(q_ask)
        else:
            q_mid = None
        strike = leg.get("strike")
        leg.update({
            "series": series,
            "family": _leg_family_label(leg.get("sport"), leg.get("player_norm")),
            # Fair-value age at receipt; null when the snap carries no fv_updated_ts.
            "age_sec": (ts - float(fv_ts)) if fv_ts is not None else None,
            "player_norm_canon": player_canon,
            "team_canon": team_canon,
            "settle_key": "|".join([series, str(leg.get("game_key") or ""), player_canon or "",
                                    team_canon or "", str(leg.get("stat") or ""),
                                    "" if strike is None else str(strike), str(leg.get("side") or "")]),
            # The recorder never default-prices a leg: unpriced legs are dropped by the pricer and the
            # combo is flagged bounded at combo level, so no leg here is ever an assumed leg.
            "assumed_leg": False,
            "pricing_source": "book",
            "q_kalshi_bid": q_bid,
            "q_kalshi_ask": q_ask,
            "q_kalshi": q_mid,
            "q_kalshi_ts": q_ts,
        })
        return leg

    def record_trades(self, rfq_id: str, market_ticker: str, trades: list[dict], since_ts: float) -> None:
        self.append({"kind": "followup", "tape_schema": 2, "ts": self._clock(), "rfq_id": rfq_id,
                     "market_ticker": market_ticker, "since_ts": since_ts, "trades": trades})


def _trades_for(client, market_ticker: str, since_ts: float) -> list[dict]:
    data = client.request("GET", "/markets/trades", params={"ticker": market_ticker, "min_ts": int(since_ts),
                                                            "limit": 100}, auth=False)
    return data.get("trades", []) or []


def fetch_trades_after(client, market_ticker: str, since_ts: float, extra_tickers=()) -> list[dict]:
    """Public trade tape since `since_ts` (unsigned GET) for `market_ticker` plus any `extra_tickers`, de-duplicated
    by trade id. Used 15 s after an RFQ is seen.

    Why more than one ticker: the RFQ's `market_ticker` is NOT the ticker combo trades print under. On the demo
    tape (2026-10-26) RFQs carried `KXMVECROSSCATEGORY0-...` while the trades printed under
    `KXMVECROSSCATEGORY-...`, so a follow-up keyed on the RFQ ticker alone attached zero trades to every combo and
    markout was unmeasurable. The traded ticker is not derivable from the RFQ ticker by string surgery (Kalshi
    assigns series namespaces; the two shapes share no computable mapping), so combo callers resolve the traded
    ticker(s) from the exchange (RFQRecorder._combo_trade_tickers) and pass them here. Single-market RFQs print
    under their own ticker and need no extras."""
    out: list[dict] = []
    seen: set = set()
    for tk in dict.fromkeys(t for t in (market_ticker, *extra_tickers) if t):
        for tr in _trades_for(client, tk, since_ts):
            tid = tr.get("trade_id") or tr.get("id")
            if tid is not None:
                if tid in seen:
                    continue
                seen.add(tid)
            out.append(tr)
    return out


class RFQRecorder:
    """--mode record: price every RFQ against the fair book, write it to the tape, and 15 s later pull the public
    trade tape for that market. Never quotes, never touches the ledger, never needs a risk gate."""
    FOLLOWUP_SEC = 15.0

    Q_TTL_SEC = 5.0

    def __init__(self, pricer, tape: RFQTape, client=None, schedule=None, clock=time.time):
        self.pricer, self.tape, self.client, self._clock = pricer, tape, client, clock
        self._schedule = schedule            # schedule(delay_sec, fn); the daemon injects loop.call_later
        self._seen: set[str] = set()
        self._q_cache: dict[str, tuple] = {}  # ticker -> (ts, yes_bid, yes_ask); tape v2 q_kalshi capture

    def _q_for_ticker(self, ticker: str) -> dict | None:
        """Kalshi's own single-leg YES bid/ask for `ticker` at intake (design section 2d / section 5
        decision 1): yes_bid = max yes_dollars price, yes_ask = 1 - max no_dollars price, from the public
        orderbook. Cached for Q_TTL_SEC. Evidence only, never fatal: any failure or odd shape degrades
        to no q data (all four q fields null on the leg)."""
        try:
            now = self._clock()
            hit = self._q_cache.get(ticker)
            if hit is not None and now - hit[0] < self.Q_TTL_SEC:
                return {"ts": hit[0], "bid": hit[1], "ask": hit[2]}
            request = getattr(self.client, "request", None)
            if request is None:
                return None
            data = request("GET", f"/markets/{ticker}/orderbook", auth=False) or {}
            book = (data.get("orderbook_fp") or {}) if isinstance(data, dict) else {}
            def _best(rows):
                prices = []
                for row in rows or []:
                    try:
                        prices.append(float(row[0]))
                    except (TypeError, ValueError, IndexError):
                        continue
                return max(prices) if prices else None
            yes_bid = _best(book.get("yes_dollars"))
            no_best = _best(book.get("no_dollars"))
            yes_ask = (1.0 - no_best) if no_best is not None else None
            self._q_cache[ticker] = (now, yes_bid, yes_ask)
            return {"ts": now, "bid": yes_bid, "ask": yes_ask}
        except Exception:                                              # noqa: BLE001 -- evidence only, never fatal
            return None

    def _q_by_ticker(self, rfq) -> dict | None:
        """q capture for every distinct ticker in the parsed RFQ (leg tickers, or the market ticker for
        a single-market RFQ). None when there is no client or nothing could be fetched."""
        if self.client is None:
            return None
        tickers = [l.ticker for l in rfq.legs] if rfq.is_combo else [rfq.market_ticker]
        out: dict = {}
        for tk in dict.fromkeys(t for t in tickers if t):
            q = self._q_for_ticker(tk)
            if q is not None:
                out[tk] = q
        return out or None

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
        # q_kalshi capture at intake, only now that the decision will actually be recorded (post-dedupe).
        q_by_ticker = self._q_by_ticker(rfq) if self.client is not None else None
        self.tape.record_rfq(msg, d, q_by_ticker=q_by_ticker)
        if self.client is not None and self._schedule is not None:
            self._schedule(self.FOLLOWUP_SEC, lambda: self._followup(rfq, now))
        return d

    def _combo_trade_tickers(self, rfq) -> list[str]:
        """The traded market ticker(s) of a combo RFQ's MVE market, resolved from the exchange: page the RFQ's
        collection series (`mve_collection_ticker`) and keep the markets whose selected legs are exactly this RFQ's
        legs (market tickers must match as a set; event tickers must also match wherever both sides carry them).
        [] for single-market RFQs, when the RFQ names no collection, and on any lookup failure -- the caller still
        queries the RFQ's own ticker, so a failed resolution degrades to the old behaviour, never to silence."""
        if not rfq.is_combo or not rfq.collection:
            return []
        want = {l.ticker for l in rfq.legs}
        want_ev = {(l.event_ticker, l.ticker) for l in rfq.legs if l.event_ticker}
        out: list[str] = []
        cursor = None
        try:
            for _ in range(10):                                        # bounded: 10 pages x 200 markets
                params = {"series_ticker": rfq.collection, "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                data = self.client.request("GET", "/markets", params=params, auth=False)
                for mk in data.get("markets", []) or []:
                    legs = mk.get("mve_selected_legs") or []
                    if not legs or {str(l.get("market_ticker") or "") for l in legs} != want:
                        continue
                    ev = {(l.get("event_ticker"), str(l.get("market_ticker") or "")) for l in legs if l.get("event_ticker")}
                    if want_ev and ev and ev != want_ev:
                        continue
                    tk = str(mk.get("ticker") or "")
                    if tk and tk != rfq.market_ticker:
                        out.append(tk)
                cursor = data.get("cursor")
                if not cursor:
                    break
        except Exception:                                              # noqa: BLE001 -- evidence only, never fatal
            return []
        return out

    def _followup(self, rfq, since_ts: float) -> None:
        try:
            trades = fetch_trades_after(self.client, rfq.market_ticker, since_ts,
                                        extra_tickers=self._combo_trade_tickers(rfq))
        except Exception as e:                                          # noqa: BLE001 -- evidence only, never fatal
            trades = [{"error": repr(e)[:200]}]
        self.tape.record_trades(rfq.rfq_id, rfq.market_ticker, trades, since_ts)
