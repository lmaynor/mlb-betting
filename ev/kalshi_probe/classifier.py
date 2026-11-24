"""Orderbook parsing + liquidity classification for the Kalshi probe.

Kalshi's public orderbook (``orderbook_fp``) carries resting BIDS only:
``yes_dollars`` = bids to buy YES, ``no_dollars`` = bids to buy NO, each a
list of ``[price_dollars, contracts_fp]`` levels.

Derived YES quotes:
  * best YES bid  = highest yes_dollars price (size = contracts at that level)
  * best YES ask  = 1 - highest no_dollars price (a NO bid at price p is an
    offer to sell YES at 1-p); ask size in contracts = contracts at that
    NO level; dollar size at best ask = ask_price * ask_contracts (the cash
    cost of lifting the full best-ask level).

Classification (ruling taxonomy):
  * HAS_ASK_25    -- best ask exists and dollar size at best ask >= $25
  * HAS_ASK_SMALL -- best ask exists but dollar size < $25
  * BID_ONLY      -- YES bids exist but no YES ask (no NO bids at all)
  * NO_BOOK       -- neither side has any level (also NO_QUOTE alias used
                     when the orderbook fetch itself returns empty)
"""
from __future__ import annotations

ASK_25_THRESHOLD_DOLLARS = 25.0


def _best_level(levels):
    """Return (price, contracts) of the highest-price level, or (None, 0.0)."""
    best_price, best_size = None, 0.0
    for lvl in levels or []:
        try:
            price, size = float(lvl[0]), float(lvl[1])
        except (TypeError, ValueError, IndexError):
            continue
        if best_price is None or price > best_price:
            best_price, best_size = price, size
    return best_price, best_size


def parse_orderbook(orderbook):
    """Parse a Kalshi orderbook payload into best bid/ask fields.

    Accepts the full API response (``{"orderbook_fp": {...}}``), the legacy
    ``{"orderbook": {...}}`` shape, or the inner book dict directly.
    """
    book = {}
    if isinstance(orderbook, dict):
        book = (orderbook.get("orderbook_fp") or orderbook.get("orderbook")
                or orderbook)
    yes_levels = book.get("yes_dollars") or book.get("yes") or []
    no_levels = book.get("no_dollars") or book.get("no") or []

    # Legacy cent-denominated books use integer cents; normalize to dollars.
    def _norm(levels):
        out = []
        for lvl in levels:
            try:
                price, size = float(lvl[0]), float(lvl[1])
            except (TypeError, ValueError, IndexError):
                continue
            if price > 1.0:  # cents
                price = price / 100.0
            out.append([price, size])
        return out

    yes_levels, no_levels = _norm(yes_levels), _norm(no_levels)

    best_bid, bid_size = _best_level(yes_levels)
    best_no_bid, no_bid_size = _best_level(no_levels)
    if best_no_bid is not None:
        best_ask = round(1.0 - best_no_bid, 4)
        ask_size = no_bid_size
    else:
        best_ask, ask_size = None, 0.0
    ask_dollar_size = (round(best_ask * ask_size, 4)
                       if best_ask is not None else 0.0)
    spread = (round(best_ask - best_bid, 4)
              if (best_ask is not None and best_bid is not None) else None)
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_size_contracts": bid_size,
        "ask_size_contracts": ask_size if best_ask is not None else 0.0,
        "ask_dollar_size": ask_dollar_size,
        "spread": spread,
        "has_yes_bids": best_bid is not None,
        "has_asks": best_ask is not None,
    }


def classify(parsed):
    """Classify a parsed book per the ruling taxonomy."""
    if parsed.get("has_asks"):
        if parsed.get("ask_dollar_size", 0.0) >= ASK_25_THRESHOLD_DOLLARS:
            return "HAS_ASK_25"
        return "HAS_ASK_SMALL"
    if parsed.get("has_yes_bids"):
        return "BID_ONLY"
    return "NO_BOOK"
