"""A small model of the final margin / total, used to price ALT LINES and to move a held line to the current one (cash out).

The board gives one two-sided price per market (the MAIN line). From it we recover the market's win probability p for the picked
side, then ask: what would the price be at a different line? We model the picked side's final margin X (or the game total T) as a
discretised normal with standard deviation `sigma`, whose centre is solved so the model reproduces p at the main line. A spread's
margin gets key-number weights (a football margin lands on 3 and 7 far more often than a normal curve says), so moving a line ACROSS
a key number costs more than moving it the same distance elsewhere. Totals use the plain normal (no key numbers fitted).

    P(win at handicap h)  = P(X + h > 0)         P(push) = P(X + h == 0)         price uses win / (win + lose)

Everything is a PLACEHOLDER until forward evidence: the sigmas and key weights are fitted to one season of closing lines
(docs/analysis/contest_sportsbook_experiments_2026-11-17.md), not to the book we are mimicking. UNVALIDATED.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Mapping

_SPAN = 90                      # margins and totals are modelled on integers in [-_SPAN, _SPAN] (totals are shifted: see _pmf)
_EPS = 1e-9


def _phi(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


@lru_cache(maxsize=4096)
def _pmf(mu_q: int, sigma_q: int, weights: tuple, lo: int, hi: int) -> tuple:
    """Probability of each integer outcome in [lo, hi] for a normal(mu, sigma) (mu, sigma passed in thousandths so the call caches),
    multiplied by the key-number weight of |outcome| and renormalised."""
    mu, sigma = mu_q / 1000.0, sigma_q / 1000.0
    w = dict(weights)
    raw = []
    for k in range(lo, hi + 1):
        p = _phi((k + 0.5 - mu) / sigma) - _phi((k - 0.5 - mu) / sigma)        # continuity-corrected mass on the integer k
        raw.append(p * w.get(abs(k), 1.0))
    s = sum(raw)
    return tuple(r / s for r in raw)


def _freeze(weights: Mapping[int, float] | None) -> tuple:
    return tuple(sorted((int(k), float(v)) for k, v in (weights or {}).items()))


def win_lose_push(mu: float, sigma: float, weights: Mapping[int, float] | None, handicap: float) -> tuple[float, float, float]:
    """(P(win), P(lose), P(push)) for the side whose final margin is X ~ model(mu, sigma), at `handicap` (win when X + handicap > 0)."""
    pm = _pmf(round(mu * 1000), round(sigma * 1000), _freeze(weights), -_SPAN, _SPAN)
    win = lose = push = 0.0
    for i, p in enumerate(pm):
        v = (i - _SPAN) + handicap
        if v > _EPS:
            win += p
        elif v < -_EPS:
            lose += p
        else:
            push += p
    return win, lose, push


def total_win_lose_push(mu: float, sigma: float, line: float, side: str) -> tuple[float, float, float]:
    """The same for a game total T (integers 0..2*_SPAN, no key weights). `side` is over/under."""
    pm = _pmf(round(mu * 1000), round(sigma * 1000), (), 0, 2 * _SPAN)
    over = under = push = 0.0
    for t, p in enumerate(pm):
        if t > line + _EPS:
            over += p
        elif t < line - _EPS:
            under += p
        else:
            push += p
    return (over, under, push) if side == "over" else (under, over, push)


def _fair(win: float, lose: float) -> float:
    return win / (win + lose) if win + lose > 0 else 0.5


def fit_spread_mu(p: float, handicap: float, sigma: float, weights: Mapping[int, float] | None) -> float:
    """The margin centre at which the picked side wins with (push-excluded) probability `p` at `handicap`. Bisection: monotone in mu."""
    lo, hi = -70.0, 70.0
    for _ in range(60):
        mid = (lo + hi) / 2
        w, l, _p = win_lose_push(mid, sigma, weights, handicap)
        if _fair(w, l) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def fit_total_mu(p: float, line: float, side: str, sigma: float) -> float:
    lo, hi = 5.0, 180.0
    for _ in range(60):
        mid = (lo + hi) / 2
        w, l, _p = total_win_lose_push(mid, sigma, line, side)
        if _fair(w, l) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def fair_prob_at(market: str, side: str, p_main: float, main_line: float, line: float, sigma: float,
                 weights: Mapping[int, float] | None = None) -> float:
    """Fair (push-excluded) probability that `side` wins at `line`, given its de-vigged probability `p_main` at `main_line`.
    For a spread, lines are the picked side's handicap; for a total, the total itself."""
    p_main = min(max(p_main, 0.001), 0.999)
    if market == "spread":
        mu = fit_spread_mu(p_main, main_line, sigma, weights)
        w, l, _p = win_lose_push(mu, sigma, weights, line)
        return _fair(w, l)
    if market == "total":
        # fit on the OVER (probability rises as the centre rises), then read the picked side off it
        p_over = p_main if side == "over" else 1.0 - p_main
        mu = fit_total_mu(p_over, main_line, "over", sigma)
        w, l, _p = total_win_lose_push(mu, sigma, line, "over")
        return _fair(w, l) if side == "over" else 1.0 - _fair(w, l)
    raise ValueError(f"no margin model for market {market!r}")
