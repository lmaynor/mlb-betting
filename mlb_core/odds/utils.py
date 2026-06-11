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


def american_to_decimal(odds) -> float:
    """Convert American odds to decimal (total return per 1 unit staked)."""
    if pd.isna(odds):
        return np.nan
    odds = float(odds)
    return 1.0 + (odds / 100.0 if odds > 0 else 100.0 / abs(odds))


def clv_pct_from_prices(entry_odds, closing_odds) -> float:
    """Closing Line Value as a price ratio (the industry-standard CLV).

    CLV% = (decimal_entry / decimal_close - 1) * 100

    Positive => you got a better price than the close (line moved your way).
    This is bounded and stable, unlike a probability-relative CLV which divides
    by the closing probability and blows up for small probs / mismatched lines.
    Same-side prices are compared, so book vig largely cancels.
    """
    d_entry = american_to_decimal(entry_odds)
    d_close = american_to_decimal(closing_odds)
    if pd.isna(d_entry) or pd.isna(d_close) or d_close <= 1.0:
        return np.nan
    return round((d_entry / d_close - 1.0) * 100.0, 4)


def remove_vig(prob_a: float, prob_b: float) -> tuple:
    """Remove vig from a two-sided market (PROPORTIONAL method). Returns (fair_a, fair_b).

    Proportional de-vig assumes vig is split in proportion to implied prob. It is
    known to MISPRICE favorites/longshots (the favorite-longshot bias): it shades
    too little off favorites. Shin and log de-vig (below) are the standard
    alternatives. See devig_two_way() for a method-selectable wrapper.
    """
    total = prob_a + prob_b
    if total <= 0 or pd.isna(total):
        return np.nan, np.nan
    return prob_a / total, prob_b / total


def shin_two_way(prob_a: float, prob_b: float) -> tuple:
    """Shin (1992) de-vig for a two-sided market. Returns (fair_a, fair_b).

    Models the vig as protection against insider/sharp money (proportion z) and
    backs out true probs. Shades favorites more than proportional, which better
    matches observed favorite-longshot bias. Solves for z numerically (robust for
    any over-round); falls back to proportional if inputs are degenerate.
    """
    if pd.isna(prob_a) or pd.isna(prob_b):
        return np.nan, np.nan
    s = prob_a + prob_b
    if s <= 0:
        return np.nan, np.nan
    if s <= 1.0:  # no vig present -> nothing to remove beyond normalization
        return remove_vig(prob_a, prob_b)

    def _fair(z):
        out = []
        for q in (prob_a, prob_b):
            val = (np.sqrt(z * z + 4.0 * (1.0 - z) * q * q / s) - z) / (2.0 * (1.0 - z))
            out.append(val)
        return out

    # Solve sum(_fair(z)) == 1 by bisection on z in (0, 0.5).
    lo, hi = 1e-9, 0.5
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if sum(_fair(mid)) > 1.0:
            lo = mid
        else:
            hi = mid
    fa, fb = _fair((lo + hi) / 2.0)
    tot = fa + fb
    if tot <= 0 or pd.isna(tot):
        return remove_vig(prob_a, prob_b)
    return fa / tot, fb / tot


def log_two_way(prob_a: float, prob_b: float) -> tuple:
    """Power/odds-ratio de-vig: fair_i proportional to q_i**k, solve k so sum==1.

    Another standard favorite-longshot correction. Returns (fair_a, fair_b).
    """
    if pd.isna(prob_a) or pd.isna(prob_b):
        return np.nan, np.nan
    s = prob_a + prob_b
    if s <= 0:
        return np.nan, np.nan
    if s <= 1.0:
        return remove_vig(prob_a, prob_b)

    def _sum(k):
        return prob_a ** k + prob_b ** k

    # k >= 1 shrinks the over-round; bisection on k in [1, 8].
    lo, hi = 1.0, 8.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if _sum(mid) > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    tot = _sum(k)
    if tot <= 0 or pd.isna(tot):
        return remove_vig(prob_a, prob_b)
    return prob_a ** k / tot, prob_b ** k / tot


def devig_two_way(prob_a: float, prob_b: float, method: str = "proportional") -> tuple:
    """Method-selectable two-way de-vig. method in {proportional, shin, log}."""
    if method == "shin":
        return shin_two_way(prob_a, prob_b)
    if method == "log":
        return log_two_way(prob_a, prob_b)
    return remove_vig(prob_a, prob_b)


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