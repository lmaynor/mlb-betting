"""
ev.novig.paper_orders -- send the executor's decisions to the Novig PAPER sandbox as real signed IOC orders.

The decision is ALWAYS made on the PRODUCTION public book (ev.novig.exec.run_pass). This module only mirrors a PLAN
that already passed every gate onto api.paper.novig.com, the way props_exec._demo_attempt does for the Kalshi demo:
it validates plumbing (signing, outcome mapping, qty/price units, fill reconciliation), NOT edge -- paper books are
synthetic (84,240 contracts per side at a ~5c spread), so a production-priced limit often will not fill there.

  * Production market -> paper market by (game_key, stat, player_norm, strike), the strict mapper's own key. Market and
    outcome ids differ between the two catalogs; descriptions and event teams/dates match (1954 of 1960 live legs
    matched on 2026-10-13). No match -> status NO_MARKET and nothing is sent.
  * Over == 'yes', Under == 'no'. A Kalshi-style $1 unit == UNIT (100) Novig cent-contracts, so qty = units * 100.
  * IOC only. NO IDEMPOTENCY on Novig ("a replayed place always places again"): a POST is sent AT MOST ONCE per intent.
    The clientId is a deterministic UUID of the intent, and after any ambiguous failure (timeout / 5xx / connection
    error) the caller reconciles through the fills endpoint by clientId instead of resending. An intent that cannot
    be reconciled is recorded as UNKNOWN and never re-sent.
  * The fills payload has no price field: {fillId, orderId, clientId, marketId, outcomeId, qty, cost "1.06000",
    taker, ts(ms)}. Price per $1 unit = cost / (qty / UNIT). The first status read after a POST is still PENDING, so
    fills (not order status) are the source of truth.
"""

from __future__ import annotations

import logging
import time
import uuid

import requests

from ev.novig.client import NovigAPIError, NovigClient, snap_price
from mlb.execution.kalshi.risk import KILL_KEY, RiskGate
from mlb.execution.kalshi.venue import Ack, FillRec, OrderIntent

log = logging.getLogger("ev.novig.paper")
UNIT = 100                                   # Novig cent-contracts per Kalshi-style $1 unit (== ev.novig.exec.UNIT)
_FILL_POLLS = 4
_FILL_POLL_S = 0.5
_MAX_FILL_PAGES = 5


class PaperRiskGate(RiskGate):
    """The sandbox mirror's own consecutive-error breaker. RiskGate's kill switch is ONE ledger-wide key, so a plain
    RiskGate("novig_paper") tripping on a paper-API outage would also halt the production-priced shadow passes (the
    evidence we are actually collecting). This one trips and resets on its own key and never touches the global one."""
    _KEY = KILL_KEY + ":novig_paper"

    @property
    def killed(self) -> bool:
        return self.ledger.kv_get(self._KEY) is not None

    @property
    def kill_reason(self) -> str | None:
        return self.ledger.kv_get(self._KEY)

    def trip(self, reason: str) -> None:
        if not self.killed:
            self.ledger.kv_set(self._KEY, reason)

    def reset(self) -> None:
        self.ledger.kv_del(self._KEY)
        self._consecutive_errors = 0


def leg_key(leg) -> tuple:
    """The cross-catalog join key (same shape as ev.novig.exec.load_novig_legs)."""
    return (leg.game_key, leg.stat, leg.player_norm, leg.strike)


def client_id_for(intent_id: str) -> str:
    """Deterministic UUID per intent: re-deriving it after a crash lets us reconcile instead of resending."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "ev-novig-paper/" + intent_id))


def _status(filled_units: float, wanted_units: float) -> str:
    if filled_units <= 0:
        return "NO_FILL"
    return "FILLED" if filled_units >= wanted_units - 1e-9 else "PARTIAL"


class NovigPaperVenue:
    """Signed IOC orders to the paper sandbox. `client` must be a PAPER NovigClient carrying the TRADING key
    (ev.novig.setup.trading_client); production is refused by the client itself."""

    def __init__(self, client: NovigClient, paper_legs: dict | None = None, *, sleep=time.sleep,
                 polls: int = _FILL_POLLS, poll_s: float = _FILL_POLL_S):
        self.client, self.legs = client, dict(paper_legs or {})
        self._sleep, self._polls, self._poll_s = sleep, polls, poll_s

    def set_legs(self, paper_legs: dict) -> None:
        self.legs = dict(paper_legs)

    def paper_leg(self, prod_leg):
        return self.legs.get(leg_key(prod_leg))

    # ------------------------------------------------------------------ fills
    def _fills_for(self, client_id: str) -> list[dict]:
        """Fills for one clientId (filtered client-side: the filter params of GET /v3/portfolio/fills are not
        verified). Follows `next` for a few pages."""
        out, q = [], {}
        for _ in range(_MAX_FILL_PAGES):
            page = self.client.fills(**q) or {}
            out += [f for f in page.get("items", []) if f.get("clientId") == client_id]
            nxt = page.get("next")
            if not nxt:
                break
            q = {"after": nxt}
        return out

    def _ack_from_fills(self, intent: OrderIntent, client_id: str, order_id: str | None, fills: list[dict],
                        raw) -> Ack:
        recs, units, cost = [], 0.0, 0.0
        for f in fills:
            n = float(f["qty"]) / UNIT
            c = float(f["cost"])
            if n <= 0:
                continue
            recs.append(FillRec(str(f["fillId"]), str(f.get("orderId") or order_id or ""), intent.ticker, intent.side,
                                n, round(c / n, 6), 0.0, str(f.get("ts") or "")))
            units += n
            cost += c
        oid = order_id or (fills[0].get("orderId") if fills else None)
        return Ack(oid, client_id, _status(units, intent.contracts), units, round(cost, 4), 0.0, recs, raw=raw)

    # ------------------------------------------------------------------ submit
    def submit(self, intent: OrderIntent, prod_leg) -> Ack:
        """`intent.ticker` is the PRODUCTION market id the decision was made on; the returned Ack's fills carry the
        PAPER market id in `raw['paper_market_id']`. At most one POST per call."""
        cid = client_id_for(intent.intent_id)
        pleg = self.paper_leg(prod_leg)
        if pleg is None:
            return Ack(None, cid, "NO_MARKET", error=f"no paper market for {leg_key(prod_leg)}")
        outcome_id = pleg.over_id if intent.side == "yes" else pleg.under_id
        qty = int(round(intent.contracts * UNIT))
        if qty <= 0:
            return Ack(None, cid, "ERROR", error="qty rounds to zero")
        price = snap_price(intent.limit_price, "down")
        meta = {"paper_market_id": pleg.market_id, "outcome_id": outcome_id, "qty": qty, "price": price,
                "description": pleg.description}
        try:
            resp = self.client.place_order(outcome_id, price, qty, "IOC", client_id=cid)
        except NovigAPIError as e:
            if e.status < 500:                                   # definite rejection, nothing was placed
                return Ack(None, cid, "ERROR", raw=dict(meta, rejected=str(e)[:300]), error=str(e)[:300])
            return self._reconcile(intent, cid, meta, repr(e)[:300])
        except (requests.RequestException, OSError) as e:        # ambiguous: may or may not have been placed
            return self._reconcile(intent, cid, meta, repr(e)[:300])
        order_id = (resp or {}).get("orderId") or (resp or {}).get("id")
        fills = []
        for i in range(self._polls):
            fills = self._fills_for(cid)
            if fills:
                break
            if i < self._polls - 1:
                self._sleep(self._poll_s)
        return self._ack_from_fills(intent, cid, order_id, fills, dict(meta, response=resp))

    def _reconcile(self, intent: OrderIntent, cid: str, meta: dict, why: str) -> Ack:
        """After an ambiguous submit: look for fills by clientId. Never resends."""
        try:
            fills = self._fills_for(cid)
        except Exception as e:                                   # noqa: BLE001
            return Ack(None, cid, "UNKNOWN", raw=dict(meta, ambiguous=why), error=f"{why}; reconcile failed: {e!r}"[:400])
        if fills:
            return self._ack_from_fills(intent, cid, None, fills, dict(meta, ambiguous=why, reconciled=True))
        return Ack(None, cid, "UNKNOWN", raw=dict(meta, ambiguous=why),
                   error=f"{why}; no fill found by clientId (not re-sent)"[:400])
