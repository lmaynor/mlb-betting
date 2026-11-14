"""
ev.novig.maker_venue -- resting paper placement for the Novig maker falsification study (NOV-03).

PAPER SANDBOX ONLY:
  * Orders go strictly to api.paper.novig.com using post-only (PO) time-in-force limits.
  * Real-money production is refused by NovigClient itself; this module never constructs a client,
    it receives an existing PAPER NovigClient carrying the trading key.
  * NO IDEMPOTENCY on Novig: one POST per intent.
  * Reconcile-don't-resend: ambiguous network/5xx failures are never blindly retried; they are
    reconciled through fills by clientId or marked UNKNOWN.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

import requests

from ev.novig.client import NovigAPIError, NovigClient, snap_price
from ev.novig.paper_orders import leg_key

log = logging.getLogger("ev.novig.maker_venue")
UNIT = 100                                   # Novig cent-contracts per $1-payout unit (== ev.novig.exec.UNIT)
MAX_PAGES = 5
_OPEN_STATUSES = frozenset({"OPEN", "PENDING", "WORKING", "LIVE", "PARTIALLY_FILLED", "PARTIAL"})


def client_id_for_maker(intent_id: str) -> str:
    """Deterministic UUID per maker intent."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "ev-novig-maker/" + intent_id))


class MakerPaperVenue:
    """Resting paper placement against api.paper.novig.com.

    `client` must be a PAPER NovigClient carrying the trading key.
    """

    def __init__(self, client: NovigClient, paper_legs: dict | None = None):
        self.client = client
        self.legs = dict(paper_legs or {})

    def set_legs(self, paper_legs: dict) -> None:
        self.legs = dict(paper_legs)

    def paper_leg(self, prod_leg):
        return self.legs.get(leg_key(prod_leg))

    def place_resting(self, prod_leg, *, intent_id: str, side: str, price: float, contracts_units: float) -> dict:
        """Place a resting post-only (PO) bid for a maker candidate.

        At most one POST per call. Never resends on ambiguity.
        """
        cid = client_id_for_maker(intent_id)
        pleg = self.paper_leg(prod_leg)
        if pleg is None:
            return {
                "status": "NO_MARKET",
                "client_id": cid,
                "order_id": None,
                "paper_market_id": None,
                "qty": 0,
                "price": None,
                "error": f"no paper market for {leg_key(prod_leg)}",
                "raw": {},
            }

        qty = int(round(contracts_units * UNIT))
        if qty <= 0:
            return {
                "status": "ERROR",
                "client_id": cid,
                "order_id": None,
                "paper_market_id": pleg.market_id,
                "qty": 0,
                "price": None,
                "error": "qty rounds to zero",
                "raw": {},
            }

        outcome_id = pleg.over_id if side == "yes" else pleg.under_id
        snapped = snap_price(price, "down")
        meta = {
            "paper_market_id": pleg.market_id,
            "outcome_id": outcome_id,
            "qty": qty,
            "price": snapped,
            "description": getattr(pleg, "description", ""),
        }

        try:
            resp = self.client.place_order(outcome_id, snapped, qty, "PO", client_id=cid)
        except NovigAPIError as e:
            if e.status < 500:
                return {
                    "status": "ERROR",
                    "client_id": cid,
                    "order_id": None,
                    "paper_market_id": pleg.market_id,
                    "qty": qty,
                    "price": snapped,
                    "error": str(e)[:300],
                    "raw": dict(meta, rejected=str(e)[:300]),
                }
            return self._reconcile_ambiguous(cid, pleg.market_id, qty, snapped, meta, repr(e)[:300])
        except (requests.RequestException, OSError) as e:
            return self._reconcile_ambiguous(cid, pleg.market_id, qty, snapped, meta, repr(e)[:300])

        order_id = (resp or {}).get("orderId") or (resp or {}).get("id")
        return {
            "status": "OPEN",
            "order_id": str(order_id) if order_id is not None else None,
            "client_id": cid,
            "paper_market_id": pleg.market_id,
            "qty": qty,
            "price": snapped,
            "raw": resp,
        }

    def _reconcile_ambiguous(self, cid: str, paper_market_id: str, qty: int, price: str, meta: dict, why: str) -> dict:
        """After a 5xx or connection error: check fills before declaring UNKNOWN. Never resends."""
        try:
            fills = self.fills_for(cid)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "UNKNOWN",
                "client_id": cid,
                "order_id": None,
                "paper_market_id": paper_market_id,
                "qty": qty,
                "price": price,
                "error": f"{why}; reconcile failed: {exc!r}"[:400],
                "raw": dict(meta, ambiguous=why),
            }

        if fills:
            oid = fills[0].get("orderId")
            return {
                "status": "OPEN",
                "reconciled": True,
                "order_id": str(oid) if oid is not None else None,
                "client_id": cid,
                "paper_market_id": paper_market_id,
                "qty": qty,
                "price": price,
                "raw": dict(meta, ambiguous=why, reconciled=True, fills=fills),
            }

        return {
            "status": "UNKNOWN",
            "client_id": cid,
            "order_id": None,
            "paper_market_id": paper_market_id,
            "qty": qty,
            "price": price,
            "error": f"{why}; no fill found by clientId (not re-sent)"[:400],
            "raw": dict(meta, ambiguous=why),
        }

    def cancel(self, order_id: str) -> dict:
        """Cancel an order on the venue. Exceptions propagate to caller."""
        return self.client.cancel_order(order_id)

    def get_order(self, order_id: str) -> dict:
        """Passthrough get_order."""
        return self.client.get_order(order_id)

    def list_open_orders(self) -> list[dict]:
        """Page client.list_orders() following 'next' with {'after': next} up to 5 pages, keeping only
        orders that are still open. The orders endpoint is not documented as open-only, so an order
        carrying a terminal status (CANCELLED / FILLED / EXPIRED / CLOSED / REJECTED) is dropped;
        an order with no status field is kept (status unknown, treated as open)."""
        out = []
        q = {}
        for _ in range(MAX_PAGES):
            page = self.client.list_orders(**q) or {}
            items = page.get("items", [])
            for o in items:
                st = str(o.get("status") or "").upper()
                if st and st not in _OPEN_STATUSES:
                    continue
                out.append(o)
            nxt = page.get("next")
            if not nxt or not items:
                break
            q = {"after": nxt}
        return out

    def fills_for(self, client_id: str) -> list[dict]:
        """Page client.fills() following 'next' with {'after': next} up to 5 pages, filtering clientId."""
        out = []
        q = {}
        for _ in range(MAX_PAGES):
            page = self.client.fills(**q) or {}
            items = page.get("items", [])
            out.extend([f for f in items if f.get("clientId") == client_id])
            nxt = page.get("next")
            if not nxt or not items:
                break
            q = {"after": nxt}
        return out
