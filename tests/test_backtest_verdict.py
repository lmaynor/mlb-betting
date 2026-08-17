"""
tests/test_backtest_verdict.py -- Unit tests for mlb.analysis.backtest_market.verdict()
(the model bake-off's codified profitability rubric) and the mlb_core.risk.clv.clv_verdict
min_n override it relies on.

Pure logic -- no GCS/DB/odds_history, synthetic in-memory `candidates` frames only (the
same shape backtest_market.backtest() returns). Covers all three verdict outcomes plus the
exact "only the 10%+ bucket pays" artifact shape already proven real on this harness
(see docs/solutions/logic-errors/backtest-roi-vs-clv-soft-line-artifact.md).

Run: pytest tests/test_backtest_verdict.py -v
"""
import numpy as np
import pandas as pd

from mlb.analysis import backtest_market as bt
from mlb_core.risk.clv import clv_verdict


def _rows(edge: float, n: int, roi_cons: float, clv_pattern: list | None = None) -> pd.DataFrame:
    """n synthetic settled-bet rows at a fixed edge/roi_cons. clv_pattern (cycled across
    rows) fills clv_pct; omit for NaN (no closing-line match)."""
    clv = [clv_pattern[i % len(clv_pattern)] for i in range(n)] if clv_pattern else [np.nan] * n
    return pd.DataFrame({
        "edge": [edge] * n, "won": [1.0] * n, "n_books": [4] * n,
        "roi": [roi_cons] * n, "roi_cons": [roi_cons] * n, "clv_pct": clv,
    })


# ── backtest_market._tstat ──────────────────────────────────────────────────────

def test_tstat_none_below_two_points():
    assert bt._tstat(pd.Series([5.0])) is None


def test_tstat_none_zero_variance():
    # sem==0 (all identical values) -- undefined, must not divide by zero
    assert bt._tstat(pd.Series([2.0, 2.0, 2.0])) is None


def test_tstat_matches_mean_over_sem():
    from scipy import stats as scipy_stats
    vals = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    expected = round(float(vals.mean()) / float(scipy_stats.sem(vals)), 3)
    assert bt._tstat(vals) == expected


# ── mlb_core.risk.clv.clv_verdict min_n override (backward-compat extension) ────

def test_clv_verdict_default_min_n_unchanged():
    # 40 bets is "insufficient" under the live 100-bet bar -- existing behavior untouched.
    v = clv_verdict(5.0, 4.0, 40)
    assert v["clv_status"] == "insufficient"


def test_clv_verdict_min_n_override_allows_smaller_sample():
    v = clv_verdict(5.0, 4.0, 40, min_n=30)
    assert v["clv_status"] == "ready"
    assert v["clv_promote_ready"] is True


# ── backtest_market.verdict ──────────────────────────────────────────────────────

def test_promote_candidate_significant_clv_and_monotonic_ladder():
    cand = pd.concat([
        _rows(0.01, 12, 0.02, [3.0, 5.0]),   # low-edge buckets: rising roi_cons,
        _rows(0.03, 12, 0.05, [3.0, 5.0]),   # significant +CLV (mean 4%, tiny spread)
        _rows(0.05, 12, 0.08, [3.0, 5.0]),
        _rows(0.08, 12, 0.10),
        _rows(0.15, 12, 0.15),               # 10%+ pays MORE, not exclusively
    ], ignore_index=True)

    v = bt.verdict(cand)

    assert v["n_bets"] == 60
    assert v["n_lo"] == 36                     # the 3 low-edge (<=6%) buckets, 12 each
    assert v["clv_mean"] == 4.0
    assert v["clv_tstat"] is not None and v["clv_tstat"] > 2
    assert v["ladder_monotonic"] is True
    assert v["verdict"] == "PROMOTE_CANDIDATE"


def test_no_edge_when_low_edge_clv_not_significant():
    cand = pd.concat([
        _rows(0.01, 12, 0.02, [-1.0, 1.0]),  # mean ~0 CLV -- not significant either sign
        _rows(0.03, 12, 0.05, [-1.0, 1.0]),
        _rows(0.05, 12, 0.08, [-1.0, 1.0]),
        _rows(0.08, 12, 0.10),
        _rows(0.15, 12, 0.15),
    ], ignore_index=True)

    v = bt.verdict(cand)

    assert v["verdict"] == "NO_EDGE"


def test_no_edge_when_only_top_bucket_pays_artifact_shape():
    """The exact shape docs/solutions/logic-errors/backtest-roi-vs-clv-soft-line-artifact.md
    already found real: low-edge CLV can look significant while the ladder itself is the
    soft-line artifact (everything <10% flat/negative, only 10%+ profits). Must NOT promote."""
    cand = pd.concat([
        _rows(0.01, 12, -0.02, [3.0, 5.0]),  # same significant +4% CLV as the promote case...
        _rows(0.03, 12, -0.01, [3.0, 5.0]),
        _rows(0.05, 12, 0.00, [3.0, 5.0]),
        _rows(0.08, 12, -0.03),
        _rows(0.15, 12, 0.20),               # ...but only this bucket actually pays
    ], ignore_index=True)

    v = bt.verdict(cand)

    assert v["clv_tstat"] is not None and v["clv_tstat"] > 2   # CLV alone looks fine
    assert v["ladder_monotonic"] is False                       # the artifact shape trips it
    assert v["verdict"] == "NO_EDGE"                            # so it must not promote


def test_insufficient_n_too_few_bets():
    cand = _rows(0.01, 5, 0.02, [3.0, 5.0])   # 5 << BAKEOFF_MIN_N (30)
    v = bt.verdict(cand)
    assert v["verdict"] == "INSUFFICIENT_N"


def test_insufficient_n_bets_exist_but_no_closing_line_match():
    # 40 low-edge bets, but none have a captured closing line -- a DIFFERENT failure mode
    # than "too few bets", and must still land INSUFFICIENT_N rather than false NO_EDGE.
    cand = _rows(0.01, 40, 0.02, clv_pattern=None)
    v = bt.verdict(cand)
    assert v["n_bets"] == 40
    assert v["n_lo"] == 0
    assert v["verdict"] == "INSUFFICIENT_N"


def test_insufficient_n_empty_frame():
    cand = pd.DataFrame(columns=["edge", "won", "n_books", "roi", "roi_cons", "clv_pct"])
    v = bt.verdict(cand)
    assert v["verdict"] == "INSUFFICIENT_N"
    assert v["n_bets"] == 0


def test_insufficient_n_none_frame():
    v = bt.verdict(None)
    assert v["verdict"] == "INSUFFICIENT_N"
    assert v["n_bets"] == 0
