"""Pre-registered grading for Arm T vs Arm S (minutes-restriction program).

Graded on test seasons (2024-25, 2025-26 in the design), on the SAME cases
as Arm S: only cases with an Arm S prediction AND a non-abstention Arm T
output count as shared cases.

KILL BAR (pre-registered, do not alter):
- Arm T's band-mapped MAE (minutes) must beat Arm S's MAE on the same cases
  by >= 10% relative AND >= 0.4 minutes absolute, at n >= 150 shared cases;
- AND Arm T's band log-loss must beat the dev empirical base-rate
  distribution's log-loss on the same cases.

Verdict enum: PASS / KILL / INSUFFICIENT_N (n < 150 shared cases).
Abstentions are excluded from grading and reported (count + rate).

The S+T combiner is graded in the same report, informational only (no
separate bar), when a fitted shrinkage weight is supplied.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd
from scipy import stats

from .arm_t import Calibration, band_mapped_minutes, combine, normalize_arm_s, realized_band
from .arm_t_config import LOGLOSS_MAX_P, LOGLOSS_MIN_GAIN_NATS
from .decisions_client import BANDS

MIN_SHARED_CASES = 150
MIN_REL_IMPROVEMENT = 0.10
MIN_ABS_IMPROVEMENT_MIN = 0.4
_EPS = 1e-15


def paired_logloss_test(
    t_ll: Sequence[float] | np.ndarray,
    base_ll: Sequence[float] | np.ndarray,
    game_dates: Sequence[Any] | pd.Series,
) -> tuple[float, float, int]:
    """Cluster-robust paired log-loss test (CR1 variance of the mean, date clusters).

    Argon 2026-11-13 ruling (Q1 tightened distribution prong):
      d_i = base_ll_i - t_ll_i; gain = mean(d).
      Cluster by calendar date of game_date.
      Cluster-robust CR1 variance of the mean:
        s_g = sum over cluster g of (d_i - gain);
        var = (G / (G - 1)) * sum(s_g^2) / n^2;
        se = sqrt(var);
        t = gain / se;
        p = one-sided survival of Student-t with G - 1 df (scipy.stats.t.sf).
      Fail closed:
        G < 2 -> p = 1.0;
        se == 0 -> p = 0.0 if gain > 0 else 1.0.
    """
    d = (np.asarray(base_ll, dtype=float) - np.asarray(t_ll, dtype=float)).ravel()
    n = len(d)
    if n == 0:
        return 0.0, 1.0, 0
    gain = float(np.mean(d))

    dt_series = pd.to_datetime(pd.Series(game_dates).to_numpy()).date
    residuals = d - gain
    df_clust = pd.DataFrame({"res": residuals, "cluster": dt_series})
    s_g = df_clust.groupby("cluster")["res"].sum().to_numpy(dtype=float)
    G = len(s_g)
    if G < 2:
        return gain, 1.0, G

    sum_sq = float(np.sum(s_g ** 2))
    var = (G / (G - 1)) * (sum_sq / (n ** 2))
    se = math.sqrt(max(0.0, var))
    if se == 0.0:
        p = 0.0 if gain > 0.0 else 1.0
    else:
        t_stat = gain / se
        p = float(stats.t.sf(t_stat, df=G - 1))

    return gain, p, G


def verdict_for_graded(
    n_shared: int,
    rel_improvement: float,
    abs_improvement: float,
    logloss_gain_nats: float,
    logloss_p_value: float,
) -> str:
    """The tightened graded kill-bar verdict (Argon 2026-11-13 ruling).

    PASS requires:
      - n_shared >= MIN_SHARED_CASES (150)
      - rel_improvement >= MIN_REL_IMPROVEMENT (0.10)
      - abs_improvement >= MIN_ABS_IMPROVEMENT_MIN (0.40)
      - logloss_gain_nats >= LOGLOSS_MIN_GAIN_NATS (0.010)
      - logloss_p_value < LOGLOSS_MAX_P (0.05)
    """
    if n_shared < MIN_SHARED_CASES:
        return "INSUFFICIENT_N"
    mae_pass = (
        rel_improvement >= MIN_REL_IMPROVEMENT
        and abs_improvement >= MIN_ABS_IMPROVEMENT_MIN
    )
    logloss_pass = (
        logloss_gain_nats >= LOGLOSS_MIN_GAIN_NATS
        and logloss_p_value < LOGLOSS_MAX_P
    )
    return "PASS" if (mae_pass and logloss_pass) else "KILL"


def verdict_for(
    n_shared: int,
    rel_improvement: float,
    abs_improvement: float,
    t_log_loss: float,
    base_rate_log_loss: float,
) -> str:
    """The kill-bar verdict as a pure function (boundary-tested)."""
    if n_shared < MIN_SHARED_CASES:
        return "INSUFFICIENT_N"
    mae_pass = (
        rel_improvement >= MIN_REL_IMPROVEMENT
        and abs_improvement >= MIN_ABS_IMPROVEMENT_MIN
    )
    logloss_pass = t_log_loss < base_rate_log_loss
    return "PASS" if (mae_pass and logloss_pass) else "KILL"


def _log_loss(prob_by_band: dict[str, float], realized: str) -> float:
    p = min(max(float(prob_by_band.get(realized, 0.0)), _EPS), 1.0)
    return -math.log(p)


@dataclass
class GradeReport:
    n_cases: int
    n_shared: int
    n_abstentions: int
    abstention_rate: float
    s_mae: Optional[float]
    t_mae: Optional[float]
    rel_improvement: Optional[float]
    abs_improvement: Optional[float]
    t_log_loss: Optional[float]
    base_rate_log_loss: Optional[float]
    verdict: str
    combiner_mae: Optional[float] = None
    combiner_weight: Optional[float] = None
    detail: pd.DataFrame = field(default=None, repr=False)
    logloss_gain_nats: Optional[float] = None
    logloss_p_value: Optional[float] = None
    logloss_n_clusters: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "n_cases": self.n_cases,
            "n_shared": self.n_shared,
            "n_abstentions": self.n_abstentions,
            "abstention_rate": self.abstention_rate,
            "s_mae_minutes": self.s_mae,
            "t_mae_minutes": self.t_mae,
            "rel_improvement": self.rel_improvement,
            "abs_improvement_minutes": self.abs_improvement,
            "t_log_loss": self.t_log_loss,
            "base_rate_log_loss": self.base_rate_log_loss,
            "logloss_gain_nats": self.logloss_gain_nats,
            "logloss_p_value": self.logloss_p_value,
            "logloss_n_clusters": self.logloss_n_clusters,
            "combiner_mae_minutes": self.combiner_mae,
            "combiner_weight": self.combiner_weight,
            "verdict": self.verdict,
        }


def grade(
    test_cases: pd.DataFrame,
    arm_s_predictions: pd.DataFrame,
    t_predictions: pd.DataFrame,
    calibration: Calibration,
    combiner_weight: Optional[float] = None,
    graded: bool = False,
) -> GradeReport:
    """Grade Arm T against Arm S on shared test cases."""
    s = normalize_arm_s(arm_s_predictions, test_cases)
    df = test_cases.merge(s, on="case_id", how="left").merge(
        t_predictions, on="case_id", how="left", suffixes=("", "_t")
    )
    n_cases = int(len(df))
    n_abstentions = int(df["abstention"].fillna(True).sum())

    shared = df[
        df["s_pred_minutes"].notna() & ~df["abstention"].fillna(True)
    ].copy()
    n_shared = int(len(shared))

    if n_shared == 0:
        return GradeReport(
            n_cases=n_cases, n_shared=0, n_abstentions=n_abstentions,
            abstention_rate=(n_abstentions / n_cases if n_cases else 0.0),
            s_mae=None, t_mae=None, rel_improvement=None, abs_improvement=None,
            t_log_loss=None, base_rate_log_loss=None,
            verdict="INSUFFICIENT_N", detail=shared,
            logloss_gain_nats=None, logloss_p_value=None, logloss_n_clusters=None,
        )

    shared["t_pred_minutes"] = [
        band_mapped_minutes(
            row.baseline_minutes,
            {b: getattr(row, f"p_{b}") for b in BANDS},
            calibration,
        )
        for row in shared.itertuples(index=False)
    ]
    shared["realized_band"] = [
        realized_band(r, m)
        for r, m in zip(shared["realized_ratio"], shared["realized_minutes"])
    ]

    s_mae = float((shared["s_pred_minutes"] - shared["realized_minutes"]).abs().mean())
    t_mae = float((shared["t_pred_minutes"] - shared["realized_minutes"]).abs().mean())
    abs_improvement = s_mae - t_mae
    rel_improvement = abs_improvement / s_mae if s_mae > 0 else 0.0

    t_ll_series = shared.apply(
        lambda row: _log_loss({b: row[f"p_{b}"] for b in BANDS}, row["realized_band"]),
        axis=1,
    )
    base_ll_series = shared.apply(
        lambda row: _log_loss(calibration.base_rates, row["realized_band"]), axis=1
    )
    shared["t_log_loss_i"] = t_ll_series.to_numpy(dtype=float)
    shared["base_log_loss_i"] = base_ll_series.to_numpy(dtype=float)

    t_ll = float(shared["t_log_loss_i"].mean())
    base_ll = float(shared["base_log_loss_i"].mean())

    ll_gain, ll_p, ll_clusters = paired_logloss_test(
        shared["t_log_loss_i"].to_numpy(dtype=float),
        shared["base_log_loss_i"].to_numpy(dtype=float),
        shared["game_date"],
    )

    if graded:
        verdict = verdict_for_graded(
            n_shared, rel_improvement, abs_improvement, ll_gain, ll_p
        )
    else:
        verdict = verdict_for(
            n_shared, rel_improvement, abs_improvement, t_ll, base_ll
        )

    combiner_mae = None
    if combiner_weight is not None:
        combined = [
            combine(s_, t_, combiner_weight)
            for s_, t_ in zip(shared["s_pred_minutes"], shared["t_pred_minutes"])
        ]
        import numpy as np
        combiner_mae = float(
            np.abs(np.asarray(combined) - shared["realized_minutes"].to_numpy()).mean()
        )

    return GradeReport(
        n_cases=n_cases,
        n_shared=n_shared,
        n_abstentions=n_abstentions,
        abstention_rate=(n_abstentions / n_cases if n_cases else 0.0),
        s_mae=s_mae,
        t_mae=t_mae,
        rel_improvement=rel_improvement,
        abs_improvement=abs_improvement,
        t_log_loss=t_ll,
        base_rate_log_loss=base_ll,
        verdict=verdict,
        combiner_mae=combiner_mae,
        combiner_weight=combiner_weight,
        detail=shared,
        logloss_gain_nats=ll_gain,
        logloss_p_value=ll_p,
        logloss_n_clusters=ll_clusters,
    )
