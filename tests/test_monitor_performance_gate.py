"""
Regression tests for the 2026-08-16 audit's monitor_performance.py fixes:

- C6.1: the model-health suppression gate could self-clear via zero-stake
  window dilution, not genuine recovery -- _rolling_stats/_gate_condition_met
  never filtered by kelly_triggered; once a suppressed system's trailing-30
  window fully turned over to stake=0 rows (which always contribute
  profit=0), roi hit a literal 0.0% fallback -- not < GATE_ROI_MIN(-20%) --
  auto-clearing the gate.

- C6.2: the "model rank-ordering backwards" AUC alert measured the market's
  own accuracy (auc, from market_prob), not the model's (auc_model, from
  model_prob) -- meaning real concept drift (e.g. NRFI's documented live
  AUC 0.498) would essentially never trigger it, since books are generally
  well-calibrated regardless of how the model itself is doing.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import numpy as np
import pandas as pd

from mlb.runners.monitor_performance import (
    _rolling_stats, _gate_condition_met, _check_alerts,
    MIN_GATE_N, GATE_ROI_MIN,
)


def _row(kelly_triggered, stake, profit, model_prob=0.5, market_prob=0.5, result="loss", edge=0.05):
    return {
        "result": result, "stake": stake, "profit": profit,
        "model_prob": model_prob, "market_prob": market_prob,
        "kelly_triggered": kelly_triggered, "edge": edge,
        "clv_pct": np.nan,
    }


def test_zero_stake_window_does_not_report_healthy_roi():
    """The core C6.1 scenario: a suppressed system's trailing window has
    fully turned over to log-only rows. Must report as underpowered
    (n < MIN_GATE_N), not a healthy 0.0% ROI."""
    rows = [_row(False, 0.0, 0.0, model_prob=0.3, result="loss") for _ in range(MIN_GATE_N)]
    df = pd.DataFrame(rows)

    rolling = _rolling_stats(df, window=30)
    assert rolling.get("n", 0) == 0, (
        f"kelly_triggered=False rows leaked into rolling stats: {rolling}"
    )

    should_suppress, reason = _gate_condition_met(rolling)
    assert should_suppress is False and "underpowered" in reason, (
        f"expected 'underpowered', got should_suppress={should_suppress} reason={reason!r}"
    )


def test_genuinely_bad_triggered_bets_still_suppress():
    """The fix must not mask a REAL problem -- enough genuinely-staked,
    genuinely-losing bets must still trip the gate."""
    rows = [_row(True, 10.0, -10.0, model_prob=0.6, result="loss") for _ in range(MIN_GATE_N)]
    df = pd.DataFrame(rows)

    rolling = _rolling_stats(df, window=30)
    assert rolling["n"] == MIN_GATE_N
    assert rolling["roi"] == -100.0

    should_suppress, reason = _gate_condition_met(rolling)
    assert should_suppress is True
    assert f"< {GATE_ROI_MIN}" in reason


def test_mixed_window_only_counts_triggered_rows():
    """A window with SOME real bets and some log-only padding must compute
    roi/n from the real bets alone, not be diluted by the padding."""
    rows = (
        [_row(True, 10.0, -10.0, result="loss") for _ in range(MIN_GATE_N)]
        + [_row(False, 0.0, 0.0, result="loss") for _ in range(50)]
    )
    df = pd.DataFrame(rows)

    rolling = _rolling_stats(df, window=30)
    # tail(30) is taken AFTER the kelly_triggered filter now, so the 50
    # log-only padding rows must not appear in the window at all.
    assert rolling["n"] == MIN_GATE_N
    assert rolling["roi"] == -100.0


def test_alert_uses_model_auc_not_market_auc_for_broken_model():
    """The real-world danger case: market looks efficient (auc high) while
    the model itself is broken (auc_model < 0.50) -- must alert."""
    stats = {"n": 25, "roi": 0.0, "hit_rate": 0.55, "auc": 0.65, "auc_model": 0.45}
    alerts = _check_alerts("1IOU", stats)
    assert any("rank-ordering backwards" in a for a in alerts), (
        f"expected an AUC-backwards alert using auc_model=0.45, got: {alerts}"
    )


def test_alert_does_not_fire_on_bad_market_auc_when_model_is_fine():
    """The inverse: market_prob-derived auc looks bad, but the model itself
    (auc_model) is fine -- must NOT alert on the market's own noise."""
    stats = {"n": 25, "roi": 0.0, "hit_rate": 0.55, "auc": 0.40, "auc_model": 0.65}
    alerts = _check_alerts("1IOU", stats)
    assert not any("AUC over last" in a for a in alerts), (
        f"alerted on market auc even though auc_model=0.65 is healthy: {alerts}"
    )


def test_alert_falls_back_to_market_auc_when_model_auc_unavailable():
    """Systems/rows without model_prob tracked yet must still get the
    (less precise but better-than-nothing) market-auc alert."""
    stats = {"n": 25, "roi": 0.0, "hit_rate": 0.55, "auc": 0.40, "auc_model": None}
    alerts = _check_alerts("SOME_SYSTEM", stats)
    assert any("AUC over last" in a for a in alerts)
