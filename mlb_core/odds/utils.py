"""
Shared odds math used by all systems.
Single source of truth - do not reimplement in notebooks.
"""
import numpy as np
import pandas as pd


def american_to_implied_prob(odds) -> float:
    """Convert American odds to vig-inclusive implied probability."""
    if pd.isna(odds):
        return np.nan
    odds = float(odds)
    return 100 / (odds + 100) if odds > 0 else abs(odds) / (abs(odds) + 100)


def implied_to_american(prob) -> float:
    """Convert a probability to American odds."""
    if pd.isna(prob) or prob <= 0 or prob >= 1:
        return np.nan
    return round(-prob / (1 - prob) * 100) if prob > 0.5 else round((1 - prob) / prob * 100)


def remove_vig(prob_a: float, prob_b: float) -> tuple:
    """Remove vig from a two-sided market. Returns (fair_prob_a, fair_prob_b)."""
    total = prob_a + prob_b
    if total <= 0 or pd.isna(total):
        return np.nan, np.nan
    return prob_a / total, prob_b / total


def devig_unilateral(market_prob: float, vig_pct: float = 0.07) -> float:
    """Remove vig from a one-sided prop market (HR yes/no, K over/under, etc.).

    For markets where only one side is quoted, there is no complementary
    probability to use the proportional devig method. We assume a fixed vig
    percentage embedded by the book and divide through.

    For YES props (HR yes, K over) where market_prob is vig-inclusive, devigging
    produces a LOWER fair probability — dividing by (1 + vig_pct) achieves this.

    Previous run_hr.py code used `market_prob / 1.07` which is arithmetically
    equivalent but hardcoded. This function centralises and names the assumption.

    Args:
        market_prob: vig-inclusive implied probability from American odds.
        vig_pct: book's embedded vig as a fraction (0.07 = 7% for DK HR props).
                 Calibrate empirically from historical closing-line analysis.

    Returns:
        Fair (no-vig) probability for the YES/OVER side.
    """
    if pd.isna(market_prob) or market_prob <= 0:
        return np.nan
    return float(market_prob) / (1.0 + vig_pct)


def kelly_stake(
    edge: float,
    odds,
    bankroll: float,
    fraction: float = 0.25,
    min_pct: float = 0.005,
    max_pct: float = 0.05,
) -> float:
    """Fractional Kelly criterion. Returns dollar stake.

    Full Kelly: f* = edge * (b + 1) / b
    where b = decimal odds - 1 (net payout per unit wagered),
    edge = p_model - p_fair (no-vig implied probability).

    Previous formula used edge / b which undersized by ~52% at -110.
    Fixed 2026-05-19 (T01).
    """
    if pd.isna(edge) or pd.isna(odds) or edge <= 0:
        return 0.0
    b = odds / 100 if odds > 0 else 100 / abs(odds)
    pct = max(0.0, edge * (b + 1) / b * fraction)
    if pct < min_pct:
        return 0.0
    return round(min(pct, max_pct) * bankroll, 2)


def kelly_pct(edge: float, odds, fraction: float = 0.25) -> float:
    """Returns Kelly as fraction of bankroll. Use for signal gating.

    Full Kelly: f* = edge * (b + 1) / b  (fixed 2026-05-19, T01).
    """
    if pd.isna(edge) or pd.isna(odds) or edge <= 0:
        return 0.0
    b = odds / 100 if odds > 0 else 100 / abs(odds)
    return max(0.0, edge * (b + 1) / b * fraction)