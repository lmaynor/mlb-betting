"""Same-game parlays (SGP): two legs on ONE game, priced together because their results are correlated.

A normal parlay multiplies the legs' prices, which is right only when the legs are independent. Within one game they are not: a
favourite that covers tends to do it in a higher-scoring game, so "favourite covers + over" happens more often than the product of
its two probabilities. The contest prices the pair with a Gaussian copula:

    p_joint = P(Z1 < Phi^-1(p1), Z2 < Phi^-1(p2))   with corr(Z1, Z2) = rho,   p_i = the leg's de-vigged probability

and then adds the margin (`sgp_margin_bp`) once. rho is positive for "favourite + over" and "underdog + under" and negative for the
opposite pairs (measured on 726 college football closing lines: +/-0.20 for spread x total, about 0 for moneyline x total; see
docs/analysis/contest_sportsbook_experiments_2026-11-17.md). UNVALIDATED: one season, one sport, closing lines only.

Allowed pairs: spread + total, and moneyline + total. Spread + moneyline on one game is refused (the legs nest: a team that covers a
negative spread has already won). Legs sit on the MAIN lines only (no alt lines), and a ticket is not cashed out.
The ticket price is quoted by the server and stored on the bet (`ticket_price`); pushed / voided legs drop out and the other leg is
paid at its own price.
"""
from __future__ import annotations

import math
from statistics import NormalDist

from contest.altlines import prob_to_american
from contest.odds import OPPOSITE, devig
from contest.rules import ContestRules

_N = NormalDist()
_EPS = 1e-6
ALLOWED = ({"spread", "total"}, {"ml", "total"})


def bvn_cdf(a: float, b: float, rho: float, steps: int = 2000) -> float:
    """P(Z1 < a, Z2 < b) for standard normals with correlation rho: Simpson's rule on  int_{-8}^{a} phi(x) Phi((b - rho x) / s) dx."""
    if a <= -8.0:
        return 0.0
    s = math.sqrt(max(1.0 - rho * rho, 1e-12))
    h = (a + 8.0) / steps
    total = 0.0
    for i in range(steps + 1):
        x = -8.0 + i * h
        f = math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi) * _N.cdf((b - rho * x) / s)
        total += f if i in (0, steps) else (4.0 * f if i % 2 else 2.0 * f)
    return total * h / 3.0


def pair_ok(legs) -> bool:
    return len(legs) == 2 and legs[0].game_id == legs[1].game_id and {legs[0].market, legs[1].market} in ALLOWED


def leg_probability(leg, board) -> float | None:
    """De-vigged probability that `leg` wins on the current board, or None when the board has no two-sided price for it."""
    q, o = board.get((leg.game_id, leg.market, leg.side)), board.get((leg.game_id, leg.market, OPPOSITE[leg.side]))
    if q is None or o is None:
        return None
    return devig(q.price, o.price)


def pair_rho(legs, probs, rules: ContestRules) -> float:
    """Signed correlation of the two EVENTS ("this leg wins"). Same orientation (favourite + over, underdog + under) is positive."""
    side_leg = next(l for l, _ in zip(legs, probs) if l.market != "total")
    total_leg = next(l for l in legs if l.market == "total")
    p_side = probs[legs.index(side_leg)]
    favourite = p_side > 0.5 if side_leg.market == "ml" else (side_leg.line or 0.0) < 0
    base = (rules.sgp_rho_ml_bp if side_leg.market == "ml" else rules.sgp_rho_bp) / 10_000.0
    aligned = (favourite and total_leg.side == "over") or (not favourite and total_leg.side == "under")
    return base if aligned else -base


def sgp_price(legs, board, rules: ContestRules) -> int | None:
    """The contest's American price for the pair, or None when the pair cannot be priced (not allowed, no quotes, off the main line)."""
    legs = tuple(legs)
    if not rules.sgp_enabled or not pair_ok(legs):
        return None
    probs = []
    for leg in legs:
        q = board.get((leg.game_id, leg.market, leg.side))
        p = leg_probability(leg, board)
        if q is None or p is None or q.line != leg.line:
            return None
        probs.append(min(max(p, _EPS), 1.0 - _EPS))
    rho = pair_rho(legs, probs, rules)
    joint = bvn_cdf(_N.inv_cdf(probs[0]), _N.inv_cdf(probs[1]), rho)
    if not 0.01 <= joint <= 0.9:
        return None
    return prob_to_american(min(joint * (1.0 + rules.sgp_margin_bp / 10_000.0), 0.985))
