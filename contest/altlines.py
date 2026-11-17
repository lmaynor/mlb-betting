"""Alt lines: the same game, a different number, priced by the contest from the MAIN line's two-sided price.

The board quotes one spread and one total per game. An alt line moves that number in 0.5-point steps (within `alt_max_points` of the
main line) and the contest prices it with `margin_model`: de-vig the main prices, refit the margin model to that probability, read off
the probability at the new line, then add the margin (`alt_margin_bp`) once, in the open. The result is a deterministic function of the
current main quote, so the server can check any alt leg a client sends against the board without storing a ladder.

    * spreads and totals only (a moneyline has no number to move);  never inside a teaser (a teaser already moves the number);
    * a number that makes the leg near-certain or near-impossible is not offered (alt_min_prob .. alt_max_prob);
    * UNVALIDATED: the model is fitted to one season of closing lines, not to a book's alt ladder.
"""
from __future__ import annotations

import math

from typing import TYPE_CHECKING

from contest.margin_model import fair_prob_at
from contest.odds import OPPOSITE, devig
from contest.rules import ContestRules

if TYPE_CHECKING:                       # slips imports this module to validate alt legs; the cycle is type-only
    from contest.slips import Quote

ALT_MIN_PROB = 0.04
ALT_MAX_PROB = 0.96
_STEP = 0.5


def prob_to_american(implied: float) -> int:
    """Implied probability (0 < p < 1) -> American price, nearest 5 (so a one-cent float wobble can never change a quoted price)."""
    if implied >= 0.5:
        raw = -100.0 * implied / (1.0 - implied)
        price = -int(math.floor(-raw / 5.0 + 0.5)) * 5
        return min(price, -100)
    raw = 100.0 * (1.0 - implied) / implied
    price = int(math.floor(raw / 5.0 + 0.5)) * 5
    return max(price, 100)


def on_grid(line: float) -> bool:
    return abs(line * 2 - round(line * 2)) < 1e-9


def alt_price(market: str, side: str, main: Quote, opposite: Quote, line: float, rules: ContestRules) -> int | None:
    """The contest's price for (`market`, `side`) at `line`, or None when that number is not offered."""
    if not rules.alt_lines_enabled or market not in ("spread", "total") or main.line is None:
        return None
    if line == main.line:
        return main.price                                  # the main line is the board's own price, not the model's
    if not on_grid(line) or abs(line - main.line) > rules.alt_max_points + 1e-9 or (market == "total" and line <= 0):
        return None
    sigma = rules.cashout_sigma(market)
    weights = rules.key_weights() if market == "spread" else None
    p_main = devig(main.price, opposite.price)
    p = fair_prob_at(market, side, p_main, main.line, line, sigma, weights)
    if not ALT_MIN_PROB <= p <= ALT_MAX_PROB:
        return None
    implied = min(p * (1.0 + rules.alt_margin_bp / 10_000.0), 0.985)
    return prob_to_american(implied)


def alt_ladder(market: str, side: str, main: Quote, opposite: Quote, rules: ContestRules) -> list[tuple[float, int]]:
    """Every offered (line, price) for this side, best line for the bettor last. Includes the main line at its own board price."""
    if main.line is None:
        return []
    n = int(rules.alt_max_points / _STEP)
    out: list[tuple[float, int]] = []
    for k in range(-n, n + 1):
        line = main.line + k * _STEP
        price = main.price if k == 0 else alt_price(market, side, main, opposite, line, rules)
        if price is not None:
            out.append((line, price))
    return out


def opposite_quote(board, game_id: int, market: str, side: str):
    return board.get((game_id, market, OPPOSITE[side]))
