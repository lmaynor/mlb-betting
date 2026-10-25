"""
mlb.execution.kalshi.rfq_client -- Kalshi Request-for-Quote (RFQ) REST + WebSocket client (DEMO ONLY).

Reuses the executor's RSA-PSS signing, pacing and 429 handling (client.KalshiClient). The demo-only guard
is KEPT: constructing a client for any other env raises ProductionNotEnabled, so there is no code path from
this module to real money. Production RFQ quoting needs an explicit owner decision (see docs/analysis/
kalshi_rfq_combo_lay_2026-10-25.md).

Wire facts (verified against docs.kalshi.com 2026-10-25; the original spec differed on three points):
  * POST /communications/quotes body = {rfq_id, yes_bid, no_bid, rest_remainder[, post_only]} with the two
    prices as dollar strings ("0.5600", 2-4 decimals). There is NO replace_existing field: a new quote from
    the same maker on the same RFQ replaces the previous one server-side. Response: 201 {"id": "<quote uuid>"}.
  * Either price may be "0" (declines that side) but not both; yes_bid + no_bid above $1 is rejected.
  * Confirm: PUT /communications/rfqs/{rfq_id}/quotes/{quote_id}/confirm (the bare
    /communications/quotes/{quote_id}/confirm form is DEPRECATED and has worse rate limits). 204, no body.
    Window: 30 s standard markets, 3 s High Volatility Markets -- and EVERY combo (MVE) market is an HVM.
  * A combo RFQ is a single market_ticker (the MVE market) with `mve_collection_ticker` and
    `mve_selected_legs` = [{event_ticker, market_ticker, side}]; contracts_fp / target_cost_dollars size it.
  * WS channel "communications" events: rfq_created, rfq_deleted, quote_created, quote_accepted
    (carries accepted_side, yes_bid_dollars, no_bid_dollars, contracts_accepted_fp), quote_executed.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import time

from mlb.execution.kalshi.client import (
    KalshiAPIError, KalshiClient, PROD_READONLY_ENV, ProductionNotEnabled, sign,
)

log = logging.getLogger("kalshi_exec.rfq")

WS_PATH = "/trade-api/ws/v2"
WS_URLS = {"demo": "wss://external-api-ws.demo.kalshi.co" + WS_PATH,
           # the stream only ever SUBSCRIBES (receive-only); quoting goes through REST, which the read-only client lacks
           PROD_READONLY_ENV: "wss://external-api-ws.kalshi.com" + WS_PATH}
CONFIRM_TIMEOUT_S = 2.0          # must beat the 3 s HVM / combo confirm window
MAX_QUOTE_PRICE = 0.99


class QuoteInvariantError(ValueError):
    """The (yes_bid, no_bid) pair violates a Kalshi quote rule. Raised BEFORE anything is sent."""


def validate_quote_prices(yes_bid_fp: str, no_bid_fp: str) -> tuple[str, str]:
    """Enforce the quote invariants and return the two prices as 4-decimal strings.

    Each price in [0.00, 0.99]; at least one > 0 (cannot decline both sides); when both > 0 their sum must
    be <= 0.99 (Kalshi rejects > 1.00; the extra cent is a margin against grid rounding)."""
    try:
        y, n = float(yes_bid_fp), float(no_bid_fp)
    except (TypeError, ValueError) as e:
        raise QuoteInvariantError(f"non-numeric quote price: {yes_bid_fp!r}/{no_bid_fp!r}") from e
    if not (0.0 <= y <= MAX_QUOTE_PRICE) or not (0.0 <= n <= MAX_QUOTE_PRICE):
        raise QuoteInvariantError(f"quote price outside [0.00, 0.99]: yes={y} no={n}")
    if y <= 0.0 and n <= 0.0:
        raise QuoteInvariantError("a quote cannot decline both sides (yes_bid and no_bid are both 0)")
    if y > 0.0 and n > 0.0 and y + n > MAX_QUOTE_PRICE + 1e-9:
        raise QuoteInvariantError(f"yes_bid + no_bid = {y + n:.4f} exceeds 0.99")
    return f"{y:.4f}", f"{n:.4f}"


class _RFQReads:
    """GET-only /communications queries, shared by the demo quoting client and the read-only production client."""

    def get_rfqs(self, status: str = "open", ticker: str | None = None, limit: int = 100) -> list[dict]:
        out: list[dict] = []
        cursor = None
        for _ in range(5):
            p: dict = {"status": status, "limit": max(1, min(int(limit), 100))}
            if ticker:
                p["market_ticker"] = ticker
            if cursor:
                p["cursor"] = cursor
            data = self.request("GET", "/communications/rfqs", params=p)
            out += data.get("rfqs", []) or []
            cursor = data.get("cursor")
            if not cursor or len(out) >= limit:
                break
        return out[:limit]


class RFQReadOnlyClient(_RFQReads, KalshiClient):
    """Signed GETs against PRODUCTION for tape recording. It has NO create_quote / confirm_quote / delete_quote, and
    KalshiClient.request() refuses every non-GET method for this env before touching the network -- two independent
    layers, so it cannot send a quote."""

    def __init__(self, key_id: str | None, private_key_pem: bytes | None, **kw):
        super().__init__(PROD_READONLY_ENV, key_id, private_key_pem, **kw)


class RFQClient(_RFQReads, KalshiClient):
    """Signed REST methods for /communications/*. Demo only (inherited guard + an explicit one here)."""

    def __init__(self, env: str, key_id: str | None, private_key_pem: bytes | None, **kw):
        if env != "demo":
            raise ProductionNotEnabled(
                f"RFQ client env {env!r} refused: RFQ quoting is DEMO-only until the owner explicitly "
                "enables production.")
        super().__init__(env, key_id, private_key_pem, **kw)

    # ---- queries ----
    def get_quotes(self, status: str = "open") -> list[dict]:
        data = self.request("GET", "/communications/quotes", params={"status": status})
        return data.get("quotes", []) or []

    # ---- quoting ----
    def create_quote(self, rfq_id: str, yes_bid_fp: str, no_bid_fp: str, rest_remainder: bool = False,
                     post_only: bool = False) -> dict:
        """POST a private two-sided quote. Never retried on an ambiguous failure (request() only retries 429,
        which is rejected before processing). Returns {"id": quote_id}."""
        y, n = validate_quote_prices(yes_bid_fp, no_bid_fp)
        body = {"rfq_id": rfq_id, "yes_bid": y, "no_bid": n, "rest_remainder": bool(rest_remainder)}
        if post_only:
            body["post_only"] = True
        return self.request("POST", "/communications/quotes", body=body)

    def confirm_quote(self, quote_id: str, rfq_id: str | None = None) -> dict:
        """Last-look confirm. Tight timeout, NO retries and NO pacing sleep: the 3 s HVM window cannot absorb a
        backoff. With rfq_id (always available from the quote_accepted event) it uses the current RFQ-scoped path."""
        path = (f"/communications/rfqs/{rfq_id}/quotes/{quote_id}/confirm" if rfq_id
                else f"/communications/quotes/{quote_id}/confirm")
        return self.request("PUT", path, timeout=CONFIRM_TIMEOUT_S, max_retries=0, paced=False)

    def delete_quote(self, quote_id: str) -> dict:
        return self.request("DELETE", f"/communications/quotes/{quote_id}")


# --------------------------------------------------------------------------------------------- WebSocket
def ws_headers(key_id: str, private_key, ts_ms: int | None = None) -> dict:
    """Signed handshake headers: signature = RSA-PSS(f"{ts_ms}GET{WS_PATH}")."""
    ts = str(int(time.time() * 1000) if ts_ms is None else ts_ms)
    return {"KALSHI-ACCESS-KEY": key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sign(private_key, f"{ts}GET{WS_PATH}")}


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 30.0, jitter: float = 0.25,
                  rng=random.random) -> float:
    """Exponential backoff with +/- jitter, capped."""
    d = min(cap, base * (2 ** max(0, attempt)))
    return d * (1.0 - jitter + 2.0 * jitter * rng())


class KalshiRFQStream:
    """`communications` channel consumer with exponential-backoff reconnect.

    on_event(type, msg, received_ts) is called for rfq_created / rfq_deleted / quote_created / quote_accepted /
    quote_executed (sync or async). `received_ts` is time.time() at receipt -- the last-look latency anchor.
    The `websockets` package is imported lazily so the rest of the package (and every offline test) has no
    hard dependency on it: `pip install websockets` on the machine that runs the daemon.
    """
    EVENTS = frozenset({"rfq_created", "rfq_deleted", "quote_created", "quote_accepted", "quote_executed"})

    def __init__(self, env: str, key_id: str, private_key, on_event, *, connect=None, sleep=asyncio.sleep,
                 ping_interval: float = 10.0, max_attempts: int | None = None):
        if env not in WS_URLS:
            raise ProductionNotEnabled(f"RFQ stream env {env!r} refused: demo / production-readonly only.")
        self.url, self.key_id, self._key, self.on_event = WS_URLS[env], key_id, private_key, on_event
        self._connect, self._sleep = connect, sleep
        self.ping_interval, self.max_attempts = ping_interval, max_attempts
        self._stop = False
        self._cmd_id = 0
        self.connects = 0

    def stop(self) -> None:
        self._stop = True

    def _open(self):
        """-> async context manager for one connection (injectable for tests)."""
        if self._connect is not None:
            return self._connect(self.url, ws_headers(self.key_id, self._key))
        try:
            import websockets
        except ImportError as e:                                          # pragma: no cover
            raise RuntimeError("the RFQ WebSocket needs `pip install websockets`") from e
        headers = ws_headers(self.key_id, self._key)
        kw = {"ping_interval": self.ping_interval, "ping_timeout": self.ping_interval}
        try:                                    # websockets >= 14 renamed extra_headers -> additional_headers
            return websockets.connect(self.url, additional_headers=headers, **kw)
        except TypeError:                                                  # pragma: no cover
            return websockets.connect(self.url, extra_headers=headers, **kw)

    async def _dispatch(self, raw) -> None:
        try:
            m = json.loads(raw)
        except (TypeError, ValueError):
            log.warning("rfq stream: non-JSON frame dropped")
            return
        typ = m.get("type")
        if typ in self.EVENTS:
            res = self.on_event(typ, m.get("msg") or {}, time.time())
            if inspect.isawaitable(res):
                await res
        elif typ == "error":
            log.error("rfq stream error frame: %s", str(m)[:300])

    async def run(self) -> None:
        attempt = 0
        while not self._stop:
            try:
                async with self._open() as ws:
                    self.connects += 1
                    self._cmd_id += 1
                    await ws.send(json.dumps({"id": self._cmd_id, "cmd": "subscribe",
                                              "params": {"channels": ["communications"]}}))
                    attempt = 0                                           # healthy connection: reset the backoff
                    async for raw in ws:
                        await self._dispatch(raw)
                        if self._stop:
                            return
            except asyncio.CancelledError:
                raise
            except Exception as e:                                        # noqa: BLE001 -- reconnect on anything
                log.warning("rfq stream dropped (%r); reconnecting", e)
            if self._stop:
                return
            attempt += 1
            if self.max_attempts is not None and attempt > self.max_attempts:
                raise RuntimeError(f"rfq stream gave up after {self.max_attempts} reconnect attempts")
            await self._sleep(backoff_delay(attempt))


__all__ = ["RFQClient", "RFQReadOnlyClient", "KalshiRFQStream", "KalshiAPIError", "QuoteInvariantError", "validate_quote_prices",
           "ws_headers", "backoff_delay", "WS_URLS", "CONFIRM_TIMEOUT_S"]
