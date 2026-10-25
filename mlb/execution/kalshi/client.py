"""
mlb.execution.kalshi.client -- minimal Kalshi v2 REST client (signed, DEMO ONLY).

Auth (same scheme as ~/market-maker's wsAuth.ts / the SDK's KalshiAuth):
    headers KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP (ms), KALSHI-ACCESS-SIGNATURE
    signature = base64( RSA-PSS-SHA256( timestamp + METHOD + path ) ), salt length = digest
    `path` is the full URL path INCLUDING the /trade-api/v2 prefix, WITHOUT the query string.

Public market-data GETs are sent unsigned. Orders/fills/balance are signed.

SAFETY: only the demo environment is wired. Constructing a client for any other env
raises ProductionNotEnabled -- there is deliberately no code path to real money here.
Credentials come from the environment / a caller-named env file; they are never logged.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from urllib.parse import urlparse

import requests

from mlb.execution.kalshi.book import Book

log = logging.getLogger("kalshi_exec.client")

BASE_URLS = {"demo": "https://external-api.demo.kalshi.co/trade-api/v2"}
# Public market data on the PRODUCTION host (read-only, no credentials) -- used by shadow
# mode to see what real books look like. Never used for orders.
PUBLIC_PROD_DATA_URL = "https://api.elections.kalshi.com/trade-api/v2"
PUBLIC_DATA_ENV = "public-data"
# SIGNED but GET-ONLY production access (RFQ tape recording). request() refuses every non-GET method for this env
# BEFORE any network call, so no order / quote / confirm / cancel can leave this client even by mistake.
PROD_READONLY_ENV = "production-readonly"
PROD_READONLY_URL = "https://external-api.kalshi.com/trade-api/v2"


class ProductionNotEnabled(RuntimeError):
    pass


class KalshiAPIError(RuntimeError):
    def __init__(self, status: int, body: str, method: str, path: str):
        super().__init__(f"{method} {path} -> HTTP {status}: {body[:300]}")
        self.status, self.body, self.method, self.path = status, body, method, path


def load_private_key(pem_bytes: bytes):
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_private_key(pem_bytes, password=None)


def sign(private_key, message: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    sig = private_key.sign(
        message.encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode()


def read_env_keys(path: str, keys: tuple[str, ...]) -> dict:
    """Extract ONLY the named keys from a dotenv-style file (never loads/returns the rest).

    Lets this executor reuse the demo credentials already provisioned for ~/market-maker
    without importing that file's unrelated secrets (TypeSafe, Gemini).
    """
    out = {}
    with open(os.path.expanduser(path)) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k in keys:
                out[k] = v.strip().strip('"').strip("'")
    return out


class KalshiClient:
    def __init__(self, env: str, key_id: str | None, private_key_pem: bytes | None,
                 session: requests.Session | None = None, base_url: str | None = None):
        if env == PUBLIC_DATA_ENV:
            if key_id or private_key_pem:
                raise ProductionNotEnabled("public-data client must carry no credentials")
            base_url = base_url or PUBLIC_PROD_DATA_URL        # read-only, cannot sign
        elif env == PROD_READONLY_ENV:
            if not (key_id and private_key_pem):
                raise ProductionNotEnabled("read-only production client needs credentials (signed GETs)")
            base_url = base_url or PROD_READONLY_URL
        elif env != "demo":
            raise ProductionNotEnabled(
                f"KALSHI env {env!r} refused: this executor is DEMO-only until the owner "
                "explicitly enables production (design doc s8 Phase 3).")
        self.env = env
        self.base_url = base_url or BASE_URLS["demo"]
        self._path_prefix = urlparse(self.base_url).path          # '/trade-api/v2'
        self.key_id = key_id
        self._key = load_private_key(private_key_pem) if private_key_pem else None
        self._s = session or requests.Session()
        self._s.headers["User-Agent"] = "beezy-kexec/0.1"
        self.max_retries = 5
        self.min_interval_s = 0.15          # ~6-7 req/s ceiling; Kalshi 429s well above that
        self._last_req = 0.0

    # ---- plumbing ----
    def _auth_headers(self, method: str, path: str) -> dict:
        if not (self.key_id and self._key):
            raise RuntimeError("signed request attempted without credentials")
        ts = str(int(time.time() * 1000))
        full = self._path_prefix + path
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": sign(self._key, ts + method.upper() + full)}

    def request(self, method: str, path: str, params: dict | None = None,
                body: dict | None = None, auth: bool = True, timeout: float = 15.0,
                max_retries: int | None = None, paced: bool = True) -> dict:
        # Retry policy: 429 is rejected BEFORE processing, so it is safe to retry for any method.
        # 5xx/timeouts are retried for GET only -- a POST (order) with an ambiguous outcome must
        # NEVER be blindly retried; the caller reconciles from the venue instead.
        # `max_retries` / `paced` let a latency-critical caller (RFQ confirm, 3s window) opt out of
        # the backoff sleeps and the inter-request pacing; defaults are unchanged for everyone else.
        if self.env == PROD_READONLY_ENV and method.upper() != "GET":
            raise ProductionNotEnabled(
                f"{method.upper()} {path} refused: the production client is READ-ONLY (GET only); "
                "production quoting/ordering is not enabled.")
        idempotent = method.upper() == "GET"
        retries = self.max_retries if max_retries is None else max_retries
        for attempt in range(retries + 1):
            if paced:
                self._pace()
            headers = self._auth_headers(method, path) if auth else {}      # fresh timestamp each try
            try:
                r = self._s.request(method, self.base_url + path, params=params, json=body,
                                    headers=headers, timeout=timeout)
            except requests.RequestException:
                if idempotent and attempt < retries:
                    time.sleep(self._backoff(attempt))
                    continue
                raise
            # Kalshi's CDN intermittently 403s a shared Cloud Run egress IP with an HTML "request could not be
            # satisfied" page (seen 2026-10-04; the same call succeeds minutes later). Safe to retry for GET only.
            cdn_block = (idempotent and r.status_code == 403 and "could not be satisfied" in (r.text or "")[:2000])
            retryable = r.status_code == 429 or cdn_block or (idempotent and r.status_code in (500, 502, 503, 504))
            if retryable and attempt < retries:
                time.sleep(self._backoff(attempt, r.headers.get("Retry-After")))
                continue
            if r.status_code >= 400:
                raise KalshiAPIError(r.status_code, r.text, method, path)
            return r.json() if r.content else {}
        raise AssertionError("unreachable")

    def _pace(self) -> None:
        wait = self.min_interval_s - (time.monotonic() - self._last_req)
        if wait > 0:
            time.sleep(wait)
        self._last_req = time.monotonic()

    @staticmethod
    def _backoff(attempt: int, retry_after=None) -> float:
        try:
            if retry_after is not None:
                return min(30.0, float(retry_after))
        except ValueError:
            pass
        return min(30.0, 0.5 * (2 ** attempt))

    # ---- public market data ----
    def exchange_status(self) -> dict:
        return self.request("GET", "/exchange/status", auth=False)

    def get_market(self, ticker: str) -> dict:
        return self.request("GET", f"/markets/{ticker}", auth=False)["market"]

    def get_markets(self, series_ticker: str, status: str = "open") -> list[dict]:
        out, cursor = [], None
        for _ in range(40):
            p = {"series_ticker": series_ticker, "status": status, "limit": 1000}
            if cursor:
                p["cursor"] = cursor
            data = self.request("GET", "/markets", params=p, auth=False)
            out += [m for m in data.get("markets", []) if m.get("status") == "active"]
            cursor = data.get("cursor")
            if not cursor:
                break
        return out

    def get_series(self, series_ticker: str) -> dict:
        """Series object; carries fee_type / fee_multiplier (KXMLBGAME: 'quadratic', 0.5)."""
        return self.request("GET", f"/series/{series_ticker}", auth=False).get("series", {})

    def get_orderbook(self, ticker: str) -> Book:
        data = self.request("GET", f"/markets/{ticker}/orderbook", auth=False)
        return Book.from_response(ticker, data, time.time())

    # ---- account (signed) ----
    def get_balance(self) -> dict:
        return self.request("GET", "/portfolio/balance")

    def shard_balances(self) -> dict[int, float]:
        """Cash per exchange shard in dollars. The demo exchange is sharded: a market on shard N
        can only be traded with cash that is ON shard N (else 404 insufficient_shard_balance)."""
        b = self.get_balance()
        out = {int(x["exchange_index"]): float(x["balance"]) for x in b.get("balance_breakdown") or []}
        return out or {0: float(b.get("balance_dollars") or 0.0)}

    def transfer_between_shards(self, amount_dollars: float, source_shard: int, destination_shard: int) -> dict:
        """Move cash between exchange shards (amount is sent in centicents: $1 = 10,000)."""
        return self.request("POST", "/portfolio/intra_exchange_instance_transfer", body={
            "source": "event_contract", "destination": "event_contract",
            "amount": int(round(amount_dollars * 10_000)),
            "source_exchange_shard": source_shard, "destination_exchange_shard": destination_shard})

    def create_order(self, *, ticker: str, client_order_id: str, book_side: str, count: float,
                     price: float, time_in_force: str = "immediate_or_cancel",
                     exchange_index: int | None = None) -> dict:
        """POST /portfolio/events/orders (v2). book_side 'bid' = buy YES at `price`;
        'ask' = sell YES at `price` (== buy NO at 1-price). `price`/`count` are sent as
        fixed-point strings. No post_only, no resting remainder unless time_in_force says so."""
        body = {
            "ticker": ticker, "client_order_id": client_order_id, "side": book_side,
            "count": f"{count:.2f}", "price": f"{price:.4f}",
            "time_in_force": time_in_force, "self_trade_prevention_type": "taker_at_cross",
        }
        if exchange_index is not None:
            body["exchange_index"] = exchange_index
        return self.request("POST", "/portfolio/events/orders", body=body)

    def get_orders(self, ticker: str | None = None, status: str | None = None) -> list[dict]:
        p = {k: v for k, v in (("ticker", ticker), ("status", status)) if v}
        return self.request("GET", "/portfolio/orders", params=p).get("orders", []) or []

    def cancel_order(self, order_id: str, ticker: str | None = None) -> dict:
        return self.request("DELETE", f"/portfolio/events/orders/{order_id}",
                            params={"ticker": ticker} if ticker else None)

    def get_fills(self, ticker: str | None = None, order_id: str | None = None,
                  min_ts: int | None = None) -> list[dict]:
        p = {k: v for k, v in (("ticker", ticker), ("order_id", order_id), ("min_ts", min_ts))
             if v is not None}
        return self.request("GET", "/portfolio/fills", params=p).get("fills", []) or []

    def get_positions(self) -> dict:
        return self.request("GET", "/portfolio/positions", params={"limit": 1000})

    def market_positions(self) -> list[dict]:
        return self.get_positions().get("market_positions", []) or []

    def get_settlements(self) -> list[dict]:
        return self.request("GET", "/portfolio/settlements").get("settlements", []) or []
