"""
Regression tests for the 2026-08-16 audit's backtest_market.py fixes:

- C4.1: OFFSHORE (the book denylist for line-shopping / CLV closing-price
  reference) never excluded "kalshi" -- a no-vig SHARP REFERENCE feed, not a
  real sportsbook a bet could be placed at. _closing_index() keys the
  closing price purely by (game_pk, player_id, selection, line) with no book
  in the key, so it could pick Kalshi's tighter no-vig price as "the closing
  line" for a real sportsbook bet.

- C4.3: verdict() only checked CLV significance on the LOW-edge bucket and
  never inspected the HIGH-edge ("10%+") bucket at all -- the documented
  winner's-curse pattern (small edge = good CLV, big edge = bad CLV) could
  clear PROMOTE_CANDIDATE completely invisibly.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import numpy as np
import pandas as pd
import pytest

import mlb.analysis.backtest_market as bt


def test_offshore_excludes_kalshi():
    assert "kalshi" in bt.OFFSHORE, (
        "kalshi (a no-vig sharp REFERENCE feed) must be in OFFSHORE, or "
        "_closing_index() can pick its tighter no-vig price as the CLV "
        "closing line for a real sportsbook bet -- finding C4.1"
    )
    # sanity: the other known-anchor-only sources this contrasts with in the
    # finding are still there too (regression guard against someone "cleaning
    # up" the set later and dropping one).
    assert {"pinnacle", "consensus", "average"} <= bt.OFFSHORE


def _synthetic_cand(n_lo: int, lo_clv_mean: float,
                    n_hi: int, hi_clv_mean: float,
                    seed: int = 0) -> pd.DataFrame:
    """Build a settled-candidates frame with a clean, monotonic ROI ladder
    (so ladder_monotonic=True is never the thing under test) but
    independently-controlled low-edge / high-edge CLV, since real backtests
    can show attractive ROI against the very snapshot used to grade it while
    CLV -- an independent, forward-looking signal -- goes the other way."""
    rng = np.random.default_rng(seed)
    rows = []

    def _bucket(edge_lo, edge_hi, n, roi_pct, clv_mean=None, clv_scale=0.3):
        for _ in range(n):
            edge = rng.uniform(edge_lo, edge_hi)
            clv = None if clv_mean is None else float(rng.normal(clv_mean, clv_scale))
            rows.append({
                "edge": edge, "won": 1.0 if roi_pct > 0 else 0.0,
                "n_books": 5, "roi": roi_pct / 100, "roi_cons": roi_pct / 100,
                "clv_pct": clv,
            })

    # Monotonic ROI ladder across every EDGE_LABELS bucket, >=5 bets each,
    # so ladder_monotonic is always True here -- only lo/hi CLV vary per test.
    _bucket(-0.05, 0.00, 10, roi_pct=-2, clv_mean=None)
    _bucket(0.00, 0.02, 10, roi_pct=0,  clv_mean=lo_clv_mean if n_lo else None,
            clv_scale=0.3)
    _bucket(0.02, 0.04, max(n_lo - 20, 10), roi_pct=1, clv_mean=lo_clv_mean,
            clv_scale=0.3)
    _bucket(0.04, 0.06, 10, roi_pct=2, clv_mean=lo_clv_mean, clv_scale=0.3)
    _bucket(0.06, 0.10, 10, roi_pct=3, clv_mean=None)
    _bucket(0.10, 0.30, n_hi, roi_pct=5,
            clv_mean=hi_clv_mean if n_hi else None, clv_scale=0.3)

    return pd.DataFrame(rows)


def test_significantly_negative_high_edge_clv_blocks_promotion():
    """The winner's-curse case this finding is about: low-edge CLV clears
    the promotion bar, the ROI ladder is monotonic, but the top ("10%+")
    bucket's CLV is significantly negative -- must NOT promote."""
    cand = _synthetic_cand(n_lo=40, lo_clv_mean=3.0, n_hi=40, hi_clv_mean=-3.0, seed=1)
    out = bt.verdict(cand)

    assert out["hi_n"] == 40
    assert out["hi_clv"] < 0
    assert out["verdict"] == "NO_EDGE", f"expected NO_EDGE, got {out['verdict']}: {out['reason']}"
    assert "10%+" in out["reason"] or "winner" in out["reason"].lower()


def test_positive_or_thin_high_edge_clv_does_not_block_promotion():
    """Same low-edge/ladder setup, but the top bucket's CLV is positive (not
    the winner's-curse pattern) -- Rule 3 must not block promotion."""
    cand = _synthetic_cand(n_lo=40, lo_clv_mean=3.0, n_hi=40, hi_clv_mean=3.0, seed=2)
    out = bt.verdict(cand)

    assert out["hi_n"] == 40
    assert out["hi_clv"] > 0
    assert out["verdict"] == "PROMOTE_CANDIDATE", f"got {out['verdict']}: {out['reason']}"


def test_thin_high_edge_data_does_not_block_promotion():
    """No/thin high-edge bets is NOT itself a failure -- clv_verdict reports
    'insufficient' for that, which Rule 3 deliberately does not treat as
    blocking (there's nothing to be significantly negative about)."""
    cand = _synthetic_cand(n_lo=40, lo_clv_mean=3.0, n_hi=2, hi_clv_mean=-9.0, seed=3)
    out = bt.verdict(cand)

    assert out["hi_n"] == 2
    assert out["verdict"] == "PROMOTE_CANDIDATE", f"got {out['verdict']}: {out['reason']}"


def test_hi_clv_and_hi_n_always_present_in_output():
    """Surfaced fields (finding C4.3's other ask) must exist even on the
    earliest/degenerate return paths, not just the full-computation path."""
    empty_out = bt.verdict(pd.DataFrame())
    assert "hi_n" in empty_out and "hi_clv" in empty_out
    assert empty_out["hi_n"] == 0
