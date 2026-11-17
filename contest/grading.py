"""Grading: leg results from a final score, and bet settlement for straights, parlays and teasers.

Leg results are 'win' | 'loss' | 'push' | 'void'. A leg is void when its game is voided (postponed / cancelled), push when it lands
exactly on the (teaser-adjusted) line. Overtime counts: pass the final score including it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from contest.odds import payout_cents
from contest.rules import ContestRules
from contest.slips import Leg, adjusted_line, win_decimal


def grade_leg(leg: Leg, home_score: int, away_score: int) -> str:
    """'win' | 'loss' | 'push' for one leg given the final score."""
    margin = (home_score - away_score) if leg.side == "home" else (away_score - home_score)
    if leg.market == "ml":
        value = margin
    elif leg.market == "spread":
        value = margin + adjusted_line(leg)
    else:                                            # total
        total = home_score + away_score
        value = total - adjusted_line(leg) if leg.side == "over" else adjusted_line(leg) - total
    return "win" if value > 0 else "loss" if value < 0 else "push"


@dataclass(frozen=True)
class Settlement:
    status: str          # won | lost | push | void
    payout_cents: int    # total returned to the bankroll (0 for a loss; the stake for a push / void; stake + profit for a win)


def settle_bet(kind: str, stake_cents: int, graded: Sequence[tuple[Leg, str | None]], rules: ContestRules,
               ticket_price: int | None = None) -> Settlement | None:
    """Settle a bet from its legs' results, or None when it cannot be decided yet.

    * any loss loses the whole parlay / teaser at once, even while other legs are still open
    * pushed / voided legs drop out: a parlay is repriced on the winners, a teaser on the table price for the remaining count, a
      same-game parlay on the surviving leg's own price
    * nothing left to win (every leg pushed / voided, or a teaser down to one winner) refunds the stake
    """
    results = [r for _, r in graded]
    if "loss" in results:
        return Settlement("lost", 0)
    if any(r is None for r in results):
        return None
    wins = [leg for leg, r in graded if r == "win"]
    if kind == "straight":
        if wins:
            return Settlement("won", payout_cents(stake_cents, win_decimal("straight", wins, rules)))
        return Settlement("void" if results[0] == "void" else "push", stake_cents)
    floor = 2 if kind == "teaser" else 1
    if len(wins) >= floor:
        return Settlement("won", payout_cents(stake_cents, win_decimal(kind, wins, rules, ticket_price)))
    return Settlement("void" if all(r == "void" for r in results) else "push", stake_cents)
