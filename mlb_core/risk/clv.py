"""
mlb_core.risk.clv -- Closing Line Value verdict (promotion scorecard). (A4)

CLV (closing line value) is the lowest-variance signal of whether a system has a
REAL edge: it measures whether we consistently beat the closing line, independent
of high-variance realized win/loss. ROI can be positive on luck or negative on
variance over hundreds of bets; CLV converges far faster.

IMPORTANT: this module does NOT drive automatic suppression. Suppression stays
ROI-only in monitor_performance._gate_condition_met -- bet-sample discrimination
metrics are selection-biased (the 2026-06-11 run-sim spike proved a system can
show bet-sample AUC < 0.50 while earning +11% ROI). CLV is used here as the
AFFIRMATIVE promotion scorecard (the paper->live / T17 bar) and as a leading
NEGATIVE-edge indicator surfaced in the model-health verdict and weekly digest.

T17 promotion bar (env-configurable):
  mean CLV >= CLV_PROMOTE_MIN (default +2.0%)
  AND t-stat >  CLV_TSTAT_MIN  (default 2.0)
  AND clv_n  >= CLV_MIN_N      (default 100 bets with a captured closing line)
"""
from __future__ import annotations

import os

CLV_PROMOTE_MIN = float(os.getenv("CLV_PROMOTE_MIN", "2.0"))   # mean CLV %, promotion bar
CLV_TSTAT_MIN   = float(os.getenv("CLV_TSTAT_MIN",   "2.0"))   # |t| for significance
CLV_MIN_N       = int(os.getenv("CLV_MIN_N",         "100"))   # min bets w/ closing line


def clv_verdict(mean_clv, clv_tstat, clv_n, *,
                min_n: int | None = None,
                promote_min: float | None = None,
                tstat_min: float | None = None) -> dict:
    """Classify a system's CLV into a promotion-scorecard status.

    Args:
        mean_clv:  mean closing-line value in percent (e.g. 2.3 == +2.3%), or None
        clv_tstat: t-statistic of the CLV mean vs 0, or None
        clv_n:     number of settled bets with a captured closing line
        min_n, promote_min, tstat_min: override the module-level env-configured
            bar (CLV_MIN_N/CLV_PROMOTE_MIN/CLV_TSTAT_MIN) for THIS call only, e.g. a
            smaller min_n for a one-shot backtest sample vs. the live 100-bet T17
            gate. None (default) keeps existing behavior for existing callers.

    Returns dict with:
        clv_status        in {ready, promising, flat, negative, insufficient}
        clv_note          one-line human-readable explanation
        clv_promote_ready bool -- True only when the full T17 bar is cleared
    """
    min_n = CLV_MIN_N if min_n is None else min_n
    promote_min = CLV_PROMOTE_MIN if promote_min is None else promote_min
    tstat_min = CLV_TSTAT_MIN if tstat_min is None else tstat_min

    if not clv_n or clv_n < min_n:
        return {
            "clv_status": "insufficient",
            "clv_note": f"only {clv_n or 0}/{min_n} bets with a closing line",
            "clv_promote_ready": False,
        }
    if mean_clv is None:
        return {
            "clv_status": "insufficient",
            "clv_note": "no CLV captured",
            "clv_promote_ready": False,
        }

    significant = clv_tstat is not None and abs(clv_tstat) > tstat_min

    if mean_clv >= promote_min and clv_tstat is not None and clv_tstat > tstat_min:
        return {
            "clv_status": "ready",
            "clv_note": (f"mean CLV {mean_clv:+.2f}% (t={clv_tstat}) clears the "
                         f"+{promote_min:.0f}%/t>{tstat_min:.0f} bar over {clv_n} bets"),
            "clv_promote_ready": True,
        }
    if mean_clv < 0 and significant:
        return {
            "clv_status": "negative",
            "clv_note": (f"mean CLV {mean_clv:+.2f}% (t={clv_tstat}) significantly "
                         f"negative -- no edge at the close"),
            "clv_promote_ready": False,
        }
    if mean_clv > 0:
        return {
            "clv_status": "promising",
            "clv_note": (f"mean CLV {mean_clv:+.2f}% (t={clv_tstat}) positive but short of "
                         f"the +{promote_min:.0f}%/t>{tstat_min:.0f} promotion bar"),
            "clv_promote_ready": False,
        }
    return {
        "clv_status": "flat",
        "clv_note": f"mean CLV {mean_clv:+.2f}% (t={clv_tstat}) not distinguishable from zero",
        "clv_promote_ready": False,
    }
