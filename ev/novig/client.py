"""
ev.novig.client -- Novig REST client (public catalog + signed trading), PAPER ONLY.

  * Public read-only catalog (/v3/public/*) needs no key and is served from production (read-only market data).
  * Trading is signed (NOVIG-V3) and only the paper sandbox (https://api.paper.novig.com, $10,000 play money) is
    allowed: production is refused (NovigProductionNotEnabled) until the owner explicitly enables it.
  * IMPORTANT (docs): "A replayed place always places again" -- there is NO idempotency. A POST is never retried
    blindly; after an ambiguous submit the caller must reconcile (list orders by clientId) before sending again.
Prices are STRINGS on a 3-band grid; qty is whole contracts; a winning contract pays 1 cent.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from urllib.parse import urlencode, urlsplit

import requests

from ev.novig import signing

log = logging.getLogger("ev.novig")

PAPER_URL = "https://api.paper.novig.com"
PUBLIC_DATA_URL = "https://api.novig.com"          # read-only /v3/public/* market data
PUBLIC_ENV = "public-data"
PAPER_ENV = "paper"
CENT = Decimal("0.01")                              # dollars paid by one winning contract


class NovigProductionNotEnabled(RuntimeError):
    pass


class NovigAPIError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, method: str, path: str):
        super().__init__(f"{method} {path} -> HTTP {status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


# ---------------------------------------------------------------- price grid / fees / book math
def snap_price(price, direction: str = "down") -> str:
    """Snap to the exchange grid (0.001-0.050 step 0.001 | 0.055-0.945 step 0.005 | 0.950-0.999 step 0.001).
    'down' for a BUY limit (never exceed our max); 'up' otherwise. Returns the 3-decimal string the API wants."""
    p = Decimal(str(price))
    rnd = ROUND_DOWN if direction == "down" else ROUND_UP
    # the grid has GAPS between bands: no valid price in (0.050, 0.055) or (0.945, 0.950)
    if Decimal("0.050") < p < Decimal("0.055"):
        return "0.050" if direction == "down" else "0.055"
    if Decimal("0.945") < p < Decimal("0.950"):
        return "0.945" if direction == "down" else "0.950"
    step = Decimal("0.005") if Decimal("0.055") <= p <= Decimal("0.945") else Decimal("0.001")
    snapped = (p / step).to_integral_value(rounding=rnd) * step
    snapped = min(max(snapped, Decimal("0.001")), Decimal("0.999"))
    return f"{snapped:.3f}"


def taker_fee(coefficient: float, price: float, qty: int, live: bool, charged: str = "WHEN_LIVE") -> float:
    """Dollars. Fee = c * P(1-P) * N * 1 cent, charged to the taker; 'WHEN_LIVE' markets are free pre-game."""
    if charged == "WHEN_LIVE" and not live:
        return 0.0
    return float(Decimal(str(coefficient)) * Decimal(str(price)) * (1 - Decimal(str(price))) * qty * CENT)


def contract_cost(price: float, qty: int) -> float:
    """Dollars paid for qty contracts at a price (qty x price x 1 cent)."""
    return float(Decimal(str(price)) * qty * CENT)


class Book:
    """Resting BIDS per outcome (best price first). Buying outcome X is executable against the complement of the OTHER
    outcome's bids (a bid of b on Y is a sale of X at 1-b) -- same structure as Kalshi; to be CONFIRMED on paper."""

    def __init__(self, market_id: str, orders: dict, seq: int = 0, fetched_at: float = 0.0):
        self.market_id, self.seq, self.fetched_at = market_id, seq, fetched_at
        self.bids = {oid: sorted(((Decimal(o["price"]), int(o["qty"])) for o in lv), reverse=True)
                     for oid, lv in (orders or {}).items()}

    @classmethod
    def from_response(cls, resp: dict, fetched_at: float) -> "Book":
        return cls(resp.get("marketId"), resp.get("orders") or {}, resp.get("seq", 0), fetched_at)

    def asks(self, outcome_id: str, other_outcome_id: str) -> list:
        """[(price Decimal, qty)] cheapest first for BUYING outcome_id."""
        return [(Decimal(1) - p, q) for p, q in self.bids.get(other_outcome_id, [])]

    def best_bid(self, outcome_id: str):
        v = self.bids.get(outcome_id)
        return v[0][0] if v else None


# ---------------------------------------------------------------- client
class NovigClient:
    def __init__(self, env: str, key_id: str | None = None, private_key_pem: bytes | None = None,
                 session: requests.Session | None = None, clock=time.time):
        if env == PUBLIC_ENV:
            if key_id or private_key_pem:
                raise NovigProductionNotEnabled("public-data client must carry no credentials")
            self.base = PUBLIC_DATA_URL
        elif env == PAPER_ENV:
            self.base = PAPER_URL
        else:
            raise NovigProductionNotEnabled(
                f"Novig env {env!r} refused: paper sandbox only until the owner explicitly enables production.")
        self.env, self.key_id, self._clock = env, key_id, clock
        self._key = signing.load_ed25519(private_key_pem) if private_key_pem else None
        self._s = session or requests.Session()
        self._s.headers["User-Agent"] = "beezy-novig/0.1"
        self.max_retries = 4

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None,
                signed: bool = False, timeout: float = 20.0):
        method = method.upper()
        if self.env == PUBLIC_ENV and (signed or method != "GET" or not path.startswith("/v3/public/")):
            raise NovigProductionNotEnabled("the public-data client may only GET /v3/public/* (read-only)")
        raw_query = urlencode(sorted((params or {}).items()))
        raw_body = json.dumps(body, separators=(",", ":")).encode() if body is not None else b""
        idempotent = method == "GET"
        for attempt in range(self.max_retries + 1):
            headers = {}
            if body is not None:
                headers["Content-Type"] = "application/json"
            if signed:
                if self._key is None:
                    raise RuntimeError("signed request needs a key")
                headers.update(signing.auth_headers(self.key_id, self._key, int(self._clock() * 1000), method, path,
                                                    raw_query, raw_body))
            url = f"{self.base}{path}" + (f"?{raw_query}" if raw_query else "")
            try:
                r = self._s.request(method, url, data=raw_body if body is not None else None, headers=headers,
                                    timeout=timeout)
            except requests.RequestException:
                if idempotent and attempt < self.max_retries:
                    time.sleep(min(10, 0.5 * 2 ** attempt))
                    continue
                raise
            if r.status_code == 429 or (idempotent and r.status_code in (500, 502, 503, 504)):
                if attempt < self.max_retries:                                  # 429 is rejected pre-processing
                    time.sleep(min(30.0, float(r.headers.get("Retry-After", 0.5 * 2 ** attempt)) + 0.2))
                    continue
            if r.status_code >= 400:
                try:
                    j = r.json()
                except ValueError:
                    j = {}
                raise NovigAPIError(r.status_code, j.get("code", ""), j.get("message", r.text[:200]), method, path)
            return r.json() if r.content else {}
        raise AssertionError("unreachable")

    # ---- public market data (no key) ----
    def list_markets(self, league: str, limit: int = 500, **filters) -> list:
        out, after = [], None
        while True:
            q = {"league": league, "limit": limit, **filters}
            if after:
                q["after"] = after
            page = self.request("GET", "/v3/public/catalog/markets", q)
            out += page.get("items", [])
            after = page.get("next")
            if not after or not page.get("items"):
                return out

    def list_events(self, league: str, limit: int = 500, **filters) -> list:
        out, after = [], None
        while True:
            q = {"league": league, "limit": limit, **filters}
            if after:
                q["after"] = after
            page = self.request("GET", "/v3/public/catalog/events", q)
            out += page.get("items", [])
            after = page.get("next")
            if not after or not page.get("items"):
                return out

    def get_book(self, market_id: str, depth: int = 10) -> Book:
        resp = self.request("GET", f"/v3/public/catalog/markets/{market_id}/book", {"depth": depth})
        return Book.from_response(resp, time.time())

    # ---- trading (paper) ----
    def echo(self) -> dict:
        return self.request("POST", "/v3/echo", body={}, signed=True)

    def balance(self) -> dict:
        return self.request("GET", "/v3/account/balance", signed=True)

    def place_order(self, outcome_id: str, price, qty: int, tif: str = "IOC", client_id: str | None = None) -> dict:
        """One order. NOT retried on any ambiguity -- the caller reconciles via list_orders/fills first."""
        if tif not in ("GTC", "GTT", "IOC", "FOK", "PO"):
            raise ValueError(f"bad tif {tif!r}")
        body = {"outcomeId": outcome_id, "price": price if isinstance(price, str) else snap_price(price),
                "qty": int(qty), "tif": tif, "clientId": client_id or str(uuid.uuid4())}
        return self.request("POST", "/v3/orders", body=body, signed=True)

    def get_order(self, order_id: str) -> dict:
        return self.request("GET", f"/v3/orders/{order_id}", signed=True)

    def list_orders(self, **q) -> dict:
        return self.request("GET", "/v3/orders", q, signed=True)

    def cancel_order(self, order_id: str) -> dict:
        return self.request("DELETE", f"/v3/orders/{order_id}", signed=True)

    def cancel_all(self) -> dict:
        return self.request("DELETE", "/v3/orders", signed=True)

    def fills(self, **q) -> dict:
        return self.request("GET", "/v3/portfolio/fills", q, signed=True)

    def positions(self) -> dict:
        return self.request("GET", "/v3/portfolio/positions", signed=True)
