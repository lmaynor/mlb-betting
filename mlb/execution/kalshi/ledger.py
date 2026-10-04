"""
mlb.execution.kalshi.ledger -- append-only decision / order / fill ledger (sqlite).

Every candidate that reaches the gates is recorded with its full inputs and ONE outcome
(an EXECUTED/PLANNED order or a REJ_* reason code). The rejection histogram is the first-class
output of shadow mode: it answers "is Kalshi actually soft, net of fee" before real money.

The venue is the source of truth for orders and fills; this ledger mirrors what the venue
reports (never what we merely intended), plus the kill-switch state.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, day TEXT NOT NULL, mode TEXT NOT NULL,
    intent_id TEXT, ticker TEXT, series TEXT, market TEXT, game_pk INTEGER, side TEXT,
    outcome TEXT NOT NULL, reason TEXT, p_cons REAL, p_hat REAL, n_books INTEGER, ask REAL,
    ev REAL, contracts INTEGER, limit_price REAL, lag_class TEXT, inputs_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_dec_day ON decisions(day, outcome);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, day TEXT NOT NULL, mode TEXT NOT NULL,
    intent_id TEXT NOT NULL UNIQUE, client_order_id TEXT, order_id TEXT, ticker TEXT, game_pk INTEGER,
    side TEXT, contracts INTEGER, limit_price REAL, status TEXT, fill_count REAL DEFAULT 0,
    filled_cost REAL DEFAULT 0, fee REAL DEFAULT 0, raw_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_ord_day ON orders(day);
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY, order_id TEXT, ticker TEXT, side TEXT, count REAL, price REAL,
    fee REAL, ts TEXT, mode TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Ledger:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("KEXEC_DB", os.path.expanduser("~/.kalshi_exec/ledger.sqlite"))
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.Lock()
        self._c = sqlite3.connect(self.path, check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._c.executescript(_SCHEMA)
        self._c.commit()

    # ---- decisions ----
    def record_decision(self, *, day: str, mode: str, outcome: str, reason: str = "", **f) -> int:
        cols = ("intent_id", "ticker", "series", "market", "game_pk", "side", "p_cons", "p_hat",
                "n_books", "ask", "ev", "contracts", "limit_price", "lag_class")
        vals = [f.get(c) for c in cols]
        inputs = json.dumps(f.get("inputs") or {}, default=str, sort_keys=True)
        with self._lock:
            cur = self._c.execute(
                "INSERT INTO decisions (ts, day, mode, outcome, reason, " + ",".join(cols) +
                ", inputs_json) VALUES (?,?,?,?,?," + ",".join("?" * len(cols)) + ",?)",
                [now_iso(), day, mode, outcome, reason, *vals, inputs])
            self._c.commit()
            return cur.lastrowid

    def histogram(self, day: str, mode: str | None = None) -> dict:
        q, a = "SELECT outcome, COUNT(*) n FROM decisions WHERE day=?", [day]
        if mode:
            q, a = q + " AND mode=?", a + [mode]
        with self._lock:
            return {r["outcome"]: r["n"] for r in self._c.execute(q + " GROUP BY outcome", a)}

    # ---- orders / fills ----
    def has_intent(self, intent_id: str) -> bool:
        with self._lock:
            return self._c.execute("SELECT 1 FROM orders WHERE intent_id=?", (intent_id,)).fetchone() is not None

    def record_order(self, *, day: str, mode: str, intent_id: str, client_order_id: str,
                     order_id: str | None, ticker: str, game_pk, side: str, contracts: int,
                     limit_price: float, status: str, fill_count: float = 0.0,
                     filled_cost: float = 0.0, fee: float = 0.0, raw=None) -> None:
        with self._lock:
            self._c.execute(
                "INSERT INTO orders (ts, day, mode, intent_id, client_order_id, order_id, ticker, game_pk,"
                " side, contracts, limit_price, status, fill_count, filled_cost, fee, raw_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now_iso(), day, mode, intent_id, client_order_id, order_id, ticker, game_pk, side,
                 contracts, limit_price, status, fill_count, filled_cost, fee,
                 json.dumps(raw, default=str) if raw is not None else None))
            self._c.commit()

    def record_fill(self, *, fill_id: str, order_id: str, ticker: str, side: str, count: float,
                    price: float, fee: float, ts: str, mode: str) -> None:
        with self._lock:
            self._c.execute(
                "INSERT OR IGNORE INTO fills (fill_id, order_id, ticker, side, count, price, fee, ts, mode)"
                " VALUES (?,?,?,?,?,?,?,?,?)", (fill_id, order_id, ticker, side, count, price, fee, ts, mode))
            self._c.commit()

    def orders_today(self, day: str, mode: str | None = None) -> int:
        q, a = "SELECT COUNT(*) n FROM orders WHERE day=?", [day]
        if mode:
            q, a = q + " AND mode=?", a + [mode]
        with self._lock:
            return self._c.execute(q, a).fetchone()["n"]

    def deployed_today(self, day: str, mode: str | None = None) -> float:
        q, a = "SELECT COALESCE(SUM(filled_cost),0) s FROM orders WHERE day=?", [day]
        if mode:
            q, a = q + " AND mode=?", a + [mode]
        with self._lock:
            return float(self._c.execute(q, a).fetchone()["s"])

    def game_exposure(self, game_pk, day: str, mode: str | None = None) -> float:
        if game_pk is None:
            return 0.0
        q, a = "SELECT COALESCE(SUM(filled_cost),0) s FROM orders WHERE day=? AND game_pk=?", [day, game_pk]
        if mode:
            q, a = q + " AND mode=?", a + [mode]
        with self._lock:
            return float(self._c.execute(q, a).fetchone()["s"])

    def tickers(self, mode: str | None = None) -> set:
        q, a = "SELECT DISTINCT ticker FROM orders WHERE fill_count > 0", []
        if mode:
            q, a = q + " AND mode=?", [mode]
        with self._lock:
            return {r["ticker"] for r in self._c.execute(q, a)}

    # ---- kv (kill switch etc.) ----
    def kv_get(self, k: str, default: str | None = None) -> str | None:
        with self._lock:
            r = self._c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            return r["v"] if r else default

    def kv_set(self, k: str, v: str) -> None:
        with self._lock:
            self._c.execute("INSERT INTO kv (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
            self._c.commit()

    def kv_del(self, k: str) -> None:
        with self._lock:
            self._c.execute("DELETE FROM kv WHERE k=?", (k,))
            self._c.commit()
