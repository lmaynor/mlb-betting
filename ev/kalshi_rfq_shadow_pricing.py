"""Shadow-pricing for unpriceable SPORTS combos (Luc approved Nov 25, RUL-20261125).

Analysis records only: this module has no admission concept and never produces an
ADMITTED_FIRE disposition. Non-sports legs and unresolved legs fail closed.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Protocol, runtime_checkable

from ev.kalshi_rfq_filter import is_non_sports_series


@runtime_checkable
class ReferenceProvider(Protocol):
    """Protocol for single-leg reference probability providers."""

    def reference_win_p(self, leg: dict) -> tuple[float, str] | None:
        """Return (side_adjusted_win_p, source_label) or None when unavailable."""
        ...


class KalshiLegMidProvider:
    """Reference probability from Kalshi's two-sided single-leg orderbook."""

    def __init__(self, book_by_ticker: dict[str, tuple[float | None, float | None]]):
        self.book_by_ticker = book_by_ticker

    def reference_win_p(self, leg: dict) -> tuple[float, str] | None:
        ticker = str(leg.get("ticker") or "")
        side = str(leg.get("side") or "").lower()
        if side not in ("yes", "no"):
            return None
        quote = self.book_by_ticker.get(ticker)
        if quote is None:
            return None
        bid, ask = quote
        if bid is None or ask is None or bid <= 0.0 or ask >= 1.0 or bid >= ask:
            return None
        p_yes = (float(bid) + float(ask)) / 2.0
        win_p = p_yes if side == "yes" else 1.0 - p_yes
        return win_p, "kalshi_leg_mid"


class FamilyFrequencyProvider:
    """Reference probability from empirical series YES win rates, shaded against the NO quoter."""

    def __init__(self, table: dict[str, float], shade: float = 0.10):
        self.table = dict(table)
        self.shade = float(shade)

    def reference_win_p(self, leg: dict) -> tuple[float, str] | None:
        series = str(leg.get("series") or str(leg.get("ticker") or "").split("-", 1)[0])
        side = str(leg.get("side") or "").lower()
        if series not in self.table or side not in ("yes", "no"):
            return None
        rate = float(self.table[series])
        raw = rate if side == "yes" else 1.0 - rate
        p_win_used = min(0.99, raw * (1.0 + self.shade))
        return p_win_used, "family_default"


def build_family_frequency_table(rows) -> dict[str, float]:
    """Compute empirical YES win rate per series from {'series', 'won'} settlement rows.

    Series with no rows are absent (no default may be invented).
    """
    counts: dict[str, list[int]] = {}
    for row in rows:
        series = str(row.get("series") or "")
        if not series:
            continue
        bucket = counts.setdefault(series, [0, 0])
        bucket[0] += 1 if row.get("won") else 0
        bucket[1] += 1
    return {s: wins / total for s, (wins, total) in counts.items() if total > 0}


def _event_key_for_leg(leg: dict) -> str:
    """Return event_ticker when provided; else approximate by stripping the final '-' segment.

    Note: splitting at the second '-' segment is unreliable across Kalshi ticker schemas,
    so rsplit('-', 1)[0] is used as the fallback approximation when event_ticker is absent.
    """
    ev = leg.get("event_ticker")
    if ev:
        return str(ev)
    ticker = str(leg.get("ticker") or "")
    return ticker.rsplit("-", 1)[0] if "-" in ticker else ticker


def shadow_price_combo(
    legs: list[dict],
    book_win_p: dict[str, float],
    providers: list[ReferenceProvider],
) -> dict:
    """Shadow-price a combo across book_win_p and fallback reference providers.

    Fail-closed on any non-sports leg (`FAIL_CLOSED_NON_SPORTS`) or any unresolved leg
    (`FAIL_CLOSED_NO_REFERENCE`).
    """
    if not legs:
        return {
            "status": "FAIL_CLOSED_NO_REFERENCE",
            "disposition": "FAIL_CLOSED_NO_REFERENCE",
            "reason": "empty_legs",
        }

    for leg in legs:
        ticker = str(leg.get("ticker") or "")
        series = str(leg.get("series") or ticker.split("-", 1)[0])
        if is_non_sports_series(series):
            return {
                "status": "FAIL_CLOSED_NON_SPORTS",
                "disposition": "FAIL_CLOSED_NON_SPORTS",
                "non_sports_series": series,
            }

    priced_legs: list[dict] = []
    event_keys: list[str] = []

    for leg in legs:
        ticker = str(leg.get("ticker") or "")
        side = str(leg.get("side") or "").lower()
        if side not in ("yes", "no"):
            return {
                "status": "FAIL_CLOSED_NO_REFERENCE",
                "disposition": "FAIL_CLOSED_NO_REFERENCE",
                "unresolved_ticker": ticker,
            }

        win_p: float | None = None
        source: str | None = None
        assumed = False

        if ticker in book_win_p and book_win_p[ticker] is not None:
            win_p = float(book_win_p[ticker])
            source = "book"
            assumed = False
        else:
            for provider in providers:
                hit = provider.reference_win_p(leg)
                if hit is not None:
                    win_p = float(hit[0])
                    source = str(hit[1])
                    assumed = source == "family_default"
                    break

        if win_p is None or source is None:
            return {
                "status": "FAIL_CLOSED_NO_REFERENCE",
                "disposition": "FAIL_CLOSED_NO_REFERENCE",
                "unresolved_ticker": ticker,
            }

        # win_p is already side-adjusted (P(this leg wins on its selected side)),
        # which is this leg's direct multiplicative contribution to p_yes_shadow.
        priced_legs.append(
            {
                "ticker": ticker,
                "side": side,
                "source": source,
                "assumed_leg": assumed,
                "yes_p": win_p,
            }
        )
        ev_key = _event_key_for_leg(leg)
        if ev_key:
            event_keys.append(ev_key)

    p_yes_shadow = math.prod(item["yes_p"] for item in priced_legs)
    correlation_unmodeled = any(cnt >= 2 for cnt in Counter(event_keys).values())

    return {
        "status": "SHADOW_PRICED",
        "disposition": "SHADOW_PRICED",
        "p_yes_shadow": p_yes_shadow,
        "fair_no": 1.0 - p_yes_shadow,
        "legs": priced_legs,
        "n_assumed": sum(1 for item in priced_legs if item["assumed_leg"]),
        "correlation_unmodeled": correlation_unmodeled,
    }
