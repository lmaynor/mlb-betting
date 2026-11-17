"""Cash out: sell an open bet back to the contest at what the current board says it is worth, less a margin.

    value = full payout x P(every leg still wins) x (1 - margin)          (rounded DOWN to the cent, never above the payout)

* P(leg) starts from the CURRENT two-sided price of the leg's market with the vig removed (that is where de-vigging is needed:
  a cash-out must value the bet at fair odds, then take the margin once, in the open).
* A leg bought at a different line than the board now shows is moved to the held line with a normal model of the final margin / total:
  p = Phi(Phi^-1(p_now) + delta / sigma), where delta is how many points the held line is better than the current one. A line that
  moved IN YOUR FAVOR therefore lifts the value, and it can end above the stake (closing-line value). The sigmas are PLACEHOLDERS
  (rules.cashout_sigma), not fitted, and a normal model ignores key numbers (3, 7): UNVALIDATED.
* Legs are on different games (one leg per game), so a parlay multiplies its legs' probabilities.
* Offered only while every leg's game is unplayed and betting is open (the same 15-minute close as placing a bet) and every quote is
  fresh. In-game cash-out needs live prices we do not have except in the halftime window; teasers are priced from a fixed table and
  are not offered.

Nothing here touches a database: store.cash_out applies an offer atomically.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import NormalDist
from typing import Mapping, Sequence

from contest.odds import american_to_decimal, payout_cents
from contest.rules import ContestRules
from contest.slips import GameInfo, Leg, Quote, win_decimal

_N = NormalDist()
OPPOSITE = {"home": "away", "away": "home", "over": "under", "under": "over"}
_EPS = 1e-6


class CashoutRefused(Exception):
    """No offer, or an offer that no longer matches. `code` is machine-readable; `detail` carries data the client needs
    (for VALUE_CHANGED: the new value in cents)."""

    def __init__(self, code: str, message: str = "", detail: dict | None = None):
        super().__init__(f"{code}: {message}" if message else code)
        self.code, self.message, self.detail = code, message, detail or {}


@dataclass(frozen=True)
class CashoutOffer:
    cents: int                   # what the player would receive now
    payout_cents: int            # the full payout if every leg wins (at the prices held)
    win_prob: float              # chance every leg still wins, per the current board
    leg_probs: tuple[float, ...]


def devig(price_a: int, price_b: int) -> float:
    """Fair probability of side A from a two-sided American price (proportional de-vig)."""
    a, b = 1 / float(american_to_decimal(price_a)), 1 / float(american_to_decimal(price_b))
    return a / (a + b)


def leg_probability(leg: Leg, board: Mapping[tuple, Quote], sigma: float) -> float:
    """P(this leg wins) on the current board, moved from the current line to the line the player holds."""
    q = board.get((leg.game_id, leg.market, leg.side))
    o = board.get((leg.game_id, leg.market, OPPOSITE[leg.side]))
    if q is None or o is None:
        raise CashoutRefused("NO_QUOTE", f"no two-sided price for game {leg.game_id} {leg.market}")
    p = devig(q.price, o.price)
    if leg.market == "ml":
        return p
    if leg.line is None or q.line is None:
        raise CashoutRefused("NO_QUOTE", "missing line")
    # points by which the line the player HOLDS beats the line now on offer (positive = held line is better)
    if leg.market == "spread":
        delta = leg.line - q.line                    # both are the picked side's handicap
    else:
        delta = (q.line - leg.line) if leg.side == "over" else (leg.line - q.line)
    z = _N.inv_cdf(min(max(p, _EPS), 1 - _EPS)) + delta / sigma
    return _N.cdf(z)


def cashout_offer(kind: str, stake_cents: int, legs: Sequence[Leg], board: Mapping[tuple, Quote], rules: ContestRules,
                  games: Mapping[int, GameInfo], now: datetime) -> CashoutOffer:
    """The current cash-out value of an open straight or parlay, or CashoutRefused."""
    if not rules.cashout_enabled:
        raise CashoutRefused("DISABLED", "cash out is off for this contest")
    if kind == "teaser":
        raise CashoutRefused("TEASER", "teasers cannot be cashed out")
    close_before = timedelta(minutes=rules.close_buffer_min)
    for leg in legs:
        g = games.get(leg.game_id)
        if g is None or g.status != "scheduled" or now >= g.commence_time - close_before:
            raise CashoutRefused("GAME_CLOSED", f"game {leg.game_id} is closed to cash out")
        for side in (leg.side, OPPOSITE[leg.side]):
            q = board.get((leg.game_id, leg.market, side))
            if q is not None and (now - q.fetched_at).total_seconds() > rules.quote_max_age_s:
                raise CashoutRefused("QUOTE_STALE", "prices are too old to cash out against")
    # a moneyline has no line to move, so it needs no sigma
    probs = tuple(leg_probability(leg, board, 1.0 if leg.market == "ml" else rules.cashout_sigma(leg.market)) for leg in legs)
    payout = payout_cents(stake_cents, win_decimal(kind, legs, rules))
    p = math.prod(probs)
    value = int(math.floor(payout * p * (1 - rules.cashout_margin_bp / 10_000)))
    return CashoutOffer(cents=max(0, min(value, payout)), payout_cents=payout, win_prob=p, leg_probs=probs)
