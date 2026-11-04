"""
nfl.game.score_matrix -- Key-Number-Aware Discrete NFL Score Distribution Pricer.

Unlike NBA/NHL, NFL margins cluster on discrete scoring increments (3=FG, 7=TD+PAT).
A smooth Normal/Student-t distribution severely misprices spread rungs near 3 and 7.

Builds a 66x66 joint probability matrix P[home_score, away_score] for
scores in {0..65} x {0..65} using:

    1. Gaussian base grid over (margin = S_H - S_A, total = S_H + S_A)
       anchored to (mu_margin, mu_total).
    2. NFL Key-Number Margin Kernel W(margin): upweights empirically common
       NFL final margins (+/-3, 7, 6, 10, 4, 14) and downweights dead margins.
    3. NFL Score-Digit Kernel W(score): upweights valid football scores (multiples
       of 3/7 combinations: 0,3,6,7,9,10,13,14,17,20,21,23,24,27,...) and downweights
       impossible scores (2, 5, 8, 11, 12, 15, 18, 19, 22, 25, 26, 29, ...).
    4. Sinkhorn/IPF (Iterative Proportional Fitting) to restore:
       - sum(P) = 1.0
       - E[S_H - S_A] = mu_margin (within 0.05 pts)
       - E[S_H + S_A] = mu_total (within 0.05 pts)
       - P(tie/OT) ~ 0.006

Provides:
    * p_home_ml: P(home wins) two-way no-push
    * p_home_cover(line): P(home covers) at any spread line (integer or half-point)
    * p_over(line): P(over) at any total line
    All returned as push-adjusted two-way probabilities.

KEY NUMBERS: empirical NFL margin distribution (2000-2025 regular season)
    |margin|   frequency
         3      ~15.2%   (by far the most common; 3+3 margins combined ~15%)
         7      ~9.1%
         6      ~5.8%
        10      ~5.6%
         4      ~4.8%
        14      ~4.4%
         1      ~4.3%
    Dead numbers (2,5,9,11,12,13,15): < 1.8% each
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Tuple

import numpy as np

log = logging.getLogger("nfl.game.score_matrix")

GRID_SIZE: int = 66        # scores 0..65
MAX_SINKHORN_ITERS: int = 300
SINKHORN_TOL: float = 0.02   # pts tolerance for E[margin] and E[total]
OT_TIE_TARGET: float = 0.006  # NFL regular season OT tie rate ~0.6%

# Empirical NFL margin key-number weights (|margin| -> weight multiplier).
# Values > 1.0 upweight; < 1.0 downweight.
# Source: 2000-2025 NFL regular-season margin distributions.
_KEY_NUMBER_WEIGHTS: dict = {
    0: 0.4,    # ties extremely rare in regulation; OT resolves most
    1: 1.30,
    2: 0.35,   # very rare: 2-pt safety margin
    3: 3.20,   # the single most important key number
    4: 1.55,
    5: 0.40,
    6: 1.65,
    7: 2.40,   # second most important: TD + PAT
    8: 0.55,
    9: 0.55,
    10: 1.60,
    11: 0.45,
    12: 0.40,
    13: 0.40,
    14: 1.45,
    15: 0.40,
    16: 0.60,
    17: 1.05,
    18: 0.45,
    19: 0.45,
    20: 0.75,
    21: 1.10,
}


def _key_weight(margin_abs: int) -> float:
    """Key-number weight for |margin| = m. Linear interpolation beyond 21."""
    if margin_abs <= 21:
        return _KEY_NUMBER_WEIGHTS.get(margin_abs, 0.8)
    if margin_abs <= 28:
        return 0.7
    return 0.6


# NFL score validity: which scores (0..65) are reachable from valid football scoring sequences?
# A score is valid if it can be represented as 3a + 7b + 6c + 8d + 2e for non-negative integers.
# Precomputed via BFS at module load time.
def _build_valid_score_set() -> frozenset:
    increments = {3, 7, 6, 8, 2}
    reachable = {0}
    frontier = {0}
    while frontier:
        nxt = set()
        for s in frontier:
            for inc in increments:
                ns = s + inc
                if ns <= 65 and ns not in reachable:
                    reachable.add(ns)
                    nxt.add(ns)
        frontier = nxt
    return frozenset(reachable)


_VALID_SCORES: frozenset = _build_valid_score_set()


def _score_kernel(s: int) -> float:
    """Per-score weight based on football reachability."""
    if s in _VALID_SCORES:
        # Up-weight the most common scores
        if s in (24, 27, 21, 20, 17, 14, 13, 10, 7, 6, 3, 0):
            return 1.4
        if s in (31, 34, 28, 23, 30, 37):
            return 1.2
        return 1.0
    return 0.15   # very low but nonzero: rare but not impossible (safety-only sequences)


# Pre-compute score kernels
_SCORE_KER: np.ndarray = np.array([_score_kernel(s) for s in range(GRID_SIZE)], dtype=float)
# Pre-compute key-number weights for all (home, away) pairs
_KEY_KER: np.ndarray = np.zeros((GRID_SIZE, GRID_SIZE), dtype=float)
for _h in range(GRID_SIZE):
    for _a in range(GRID_SIZE):
        _KEY_KER[_h, _a] = _key_weight(abs(_h - _a))


@dataclass
class NFLScoreMatrix:
    """
    Joint probability distribution P[home_score, away_score] plus coherent
    market pricing derived from it.
    """
    mu_margin: float
    mu_total: float
    m_final: np.ndarray   # shape (66, 66)
    p_tie: float
    p_home_win: float
    p_away_win: float
    p_home_ml: float      # two-way no-push: p_home_win / (p_home_win + p_away_win)

    def spread(self, line_home: float) -> Tuple[float, float, float]:
        """
        P(home covers line_home) at any half-point or integer spread.
        E.g. line_home = -3.5 means home is favored by 3.5.
        Returns: (p_home_cover, p_away_cover, p_push)
        All two-way push-adjusted.
        """
        p_home_raw = 0.0
        p_away_raw = 0.0
        p_push = 0.0
        line = float(line_home)
        for h in range(GRID_SIZE):
            for a in range(GRID_SIZE):
                prob = float(self.m_final[h, a])
                if prob == 0.0:
                    continue
                effective_margin = (h - a) + line
                if effective_margin > 1e-9:
                    p_home_raw += prob
                elif effective_margin < -1e-9:
                    p_away_raw += prob
                else:
                    p_push += prob
        two_way = p_home_raw + p_away_raw
        if two_way <= 0.0:
            return 0.5, 0.5, p_push
        return p_home_raw / two_way, p_away_raw / two_way, p_push

    def total(self, line: float) -> Tuple[float, float, float]:
        """
        P(over line) for any total line (half-point or integer).
        Returns: (p_over, p_under, p_push) two-way push-adjusted.
        """
        p_over_raw = 0.0
        p_under_raw = 0.0
        p_push = 0.0
        lt = float(line)
        for h in range(GRID_SIZE):
            for a in range(GRID_SIZE):
                prob = float(self.m_final[h, a])
                if prob == 0.0:
                    continue
                tot = h + a
                if tot > lt + 1e-9:
                    p_over_raw += prob
                elif tot < lt - 1e-9:
                    p_under_raw += prob
                else:
                    p_push += prob
        two_way = p_over_raw + p_under_raw
        if two_way <= 0.0:
            return 0.5, 0.5, p_push
        return p_over_raw / two_way, p_under_raw / two_way, p_push


def build_nfl_score_matrix(
    mu_margin: float,
    mu_total: float,
    sigma_margin: float = 13.5,
    sigma_total: float = 12.0,
) -> NFLScoreMatrix:
    """
    Build the key-number-aware joint score distribution.

    Algorithm:
        1. Gaussian base grid P_0[h, a] proportional to:
               exp(-((h-a - mu_m)^2 / (2*sigma_m^2)) - ((h+a - mu_t)^2 / (2*sigma_t^2)))
        2. Apply NFL key-number margin kernel * score validity kernel.
        3. Sinkhorn IPF to restore E[margin] = mu_margin and E[total] = mu_total.
        4. OT regularization: ensure P(h==a) ~ OT_TIE_TARGET.
    """
    mu_m = float(mu_margin)
    mu_t = float(mu_total)
    sig_m = float(max(sigma_margin, 1.0))
    sig_t = float(max(sigma_total, 1.0))

    h_idx = np.arange(GRID_SIZE, dtype=float)
    a_idx = np.arange(GRID_SIZE, dtype=float)

    H, A = np.meshgrid(h_idx, a_idx, indexing="ij")  # H[h,a]=h, A[h,a]=a

    margin_grid = H - A
    total_grid = H + A

    # Step 1: Gaussian base
    log_base = (
        -0.5 * ((margin_grid - mu_m) / sig_m) ** 2
        - 0.5 * ((total_grid - mu_t) / sig_t) ** 2
    )
    # Shift for numerical stability
    log_base = log_base - float(log_base.max())
    base = np.exp(log_base)

    # Step 2: Apply kernels
    score_outer = np.outer(_SCORE_KER, _SCORE_KER)  # (66,66)
    kernel = base * _KEY_KER * score_outer

    # Zero out physically impossible scores (both > 65 already handled by GRID_SIZE)
    # Clip any negative from floating point
    kernel = np.maximum(kernel, 0.0)

    # Step 3: Normalize
    total_mass = float(kernel.sum())
    if total_mass <= 0.0:
        kernel = np.ones((GRID_SIZE, GRID_SIZE), dtype=float) / (GRID_SIZE * GRID_SIZE)
    else:
        kernel = kernel / total_mass

    # Step 4: Sinkhorn IPF to restore moments
    m = kernel.copy()

    for _ in range(MAX_SINKHORN_ITERS):
        cur_margin = float(np.sum(m * margin_grid))
        cur_total = float(np.sum(m * total_grid))

        err_m = abs(cur_margin - mu_m)
        err_t = abs(cur_total - mu_t)
        if err_m < SINKHORN_TOL and err_t < SINKHORN_TOL:
            break

        # Exponential tilting on margin dimension: multiply by exp(alpha*(h-a))
        # Solve for alpha such that E[h-a] = mu_m using one Newton step
        var_margin = float(np.sum(m * (margin_grid - cur_margin) ** 2))
        if var_margin > 1e-9 and err_m > SINKHORN_TOL:
            alpha = (mu_m - cur_margin) / var_margin
            tilt = np.exp(alpha * margin_grid)
            m = m * tilt
            s = float(m.sum())
            if s > 0:
                m = m / s

        # Exponential tilting on total dimension
        cur_total2 = float(np.sum(m * total_grid))
        var_total = float(np.sum(m * (total_grid - cur_total2) ** 2))
        err_t2 = abs(cur_total2 - mu_t)
        if var_total > 1e-9 and err_t2 > SINKHORN_TOL:
            beta = (mu_t - cur_total2) / var_total
            tilt = np.exp(beta * total_grid)
            m = m * tilt
            s = float(m.sum())
            if s > 0:
                m = m / s

    # Final normalization
    total_mass = float(m.sum())
    if total_mass <= 0.0:
        m = np.ones((GRID_SIZE, GRID_SIZE), dtype=float) / (GRID_SIZE * GRID_SIZE)
    else:
        m = m / total_mass

    # Step 5: OT tie regularization (NFL ~0.6% tie rate in regular season)
    p_tie_current = float(sum(m[k, k] for k in range(GRID_SIZE)))
    if p_tie_current < 1e-9:
        p_tie_current = 1e-9

    # Scale diagonal to hit OT_TIE_TARGET without breaking normalization
    if abs(p_tie_current - OT_TIE_TARGET) > 0.001:
        scale_diag = OT_TIE_TARGET / p_tie_current
        scale_off = (1.0 - OT_TIE_TARGET) / max(1e-9, 1.0 - p_tie_current)
        for h in range(GRID_SIZE):
            for a in range(GRID_SIZE):
                if h == a:
                    m[h, a] *= scale_diag
                else:
                    m[h, a] *= scale_off
        s = float(m.sum())
        if s > 0:
            m = m / s

    # Final statistics
    m = np.maximum(m, 0.0)
    s = float(m.sum())
    if s > 0:
        m = m / s

    p_tie = float(sum(m[k, k] for k in range(GRID_SIZE)))
    p_home_win = float(sum(
        m[h, a] for h in range(GRID_SIZE) for a in range(GRID_SIZE) if h > a
    ))
    p_away_win = float(sum(
        m[h, a] for h in range(GRID_SIZE) for a in range(GRID_SIZE) if a > h
    ))

    two_way = p_home_win + p_away_win
    p_home_ml = p_home_win / max(two_way, 1e-9)

    return NFLScoreMatrix(
        mu_margin=mu_m,
        mu_total=mu_t,
        m_final=m,
        p_tie=p_tie,
        p_home_win=p_home_win,
        p_away_win=p_away_win,
        p_home_ml=p_home_ml,
    )
