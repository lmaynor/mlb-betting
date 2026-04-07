"""
Shared odds math used by all systems.
Single source of truth — do not reimplement in notebooks.
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


def kelly_stake(
    edge: float,
    odds,
    bankroll: float,
    fraction: float = 0.25,
    min_pct: float = 0.005,
    max_pct: float = 0.05,
) -> float:
    """
    Fractional Kelly criterion. Returns dollar stake.
    Returns 0 if edge <= 0 or Kelly < min_pct.
    """
    if pd.isna(edge) or pd.isna(odds) or edge <= 0:
        return 0.0
    b = odds / 100 if odds > 0 else 100 / abs(odds)
    pct = max(0.0, (edge / b) * fraction)
    if pct < min_pct:
        return 0.0
    return round(min(pct, max_pct) * bankroll, 2)


def kelly_pct(edge: float, odds, fraction: float = 0.25) -> float:
    """Returns Kelly as fraction of bankroll. Use for signal gating."""
    if pd.isna(edge) or pd.isna(odds) or edge <= 0:
        return 0.0
    b = odds / 100 if odds > 0 else 100 / abs(odds)
    return max(0.0, (edge / b) * fraction)
