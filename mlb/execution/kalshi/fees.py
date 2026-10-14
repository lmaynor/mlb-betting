"""
mlb.execution.kalshi.fees -- Kalshi taker fee model.

fee = round_up_to_centicent(M_taker * 0.07 * C * P * (1 - P))

Source: kalshi-fee-schedule-2026-07-07.pdf (kept in ~/market-maker/docs/reference).
M_taker defaults to 1 (the general schedule) but the series object carries the real value
(GET /series/KXMLBGAME -> fee_multiplier 0.5, verified 2026-10-04 on demo AND production), so
the runner reads it per series and compares every fill's ACTUAL fee against this model.
Note a 1-contract order on an IN-PROGRESS game was charged at multiplier 1.0 (probe), so the
multiplier is not guaranteed constant across market states -- hence the post-fill check.

Fee as a fraction of cost is 0.07 * (1 - P): 3.5% at a 50c price, 6.3% at 10c,
0.7% at 90c. That drag is why the EV gate is NET of fee.
"""

from __future__ import annotations

import math

TAKER_BASE_RATE = 0.07
CENTICENT = 0.0001


def _ceil_centicent(dollars: float) -> float:
    """Ceiling to the nearest $0.0001, guarded against float noise (1.7499999999999998)."""
    centicents = math.ceil(dollars / CENTICENT - 1e-9)
    return round(centicents * CENTICENT, 4) + 0.0  # +0.0 normalizes -0.0


def taker_fee(price: float, contracts: float, multiplier: float = 1.0) -> float:
    """Total taker fee in dollars for `contracts` filled at `price` (dollars, 0-1)."""
    if contracts <= 0 or price <= 0.0 or price >= 1.0 or multiplier <= 0:
        return 0.0
    return _ceil_centicent(multiplier * TAKER_BASE_RATE * contracts * price * (1.0 - price))
