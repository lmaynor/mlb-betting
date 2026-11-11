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
from typing import Optional

import pandas as pd

from .arm_t import Calibration, band_mapped_minutes, combine, normalize_arm_s, realized_band
from .decisions_client import BANDS

MIN_SHARED_CASES = 150
MIN_REL_IMPROVEMENT = 0.10
MIN_ABS_IMPROVEMENT_MIN = 0.4
_EPS = 1e-15


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

    t_ll = float(
        shared.apply(
            lambda row: _log_loss({b: row[f"p_{b}"] for b in BANDS}, row["realized_band"]),
            axis=1,
        ).mean()
    )
    base_ll = float(
        shared.apply(
            lambda row: _log_loss(calibration.base_rates, row["realized_band"]), axis=1
        ).mean()
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
        verdict=verdict_for(n_shared, rel_improvement, abs_improvement, t_ll, base_ll),
        combiner_mae=combiner_mae,
        combiner_weight=combiner_weight,
        detail=shared,
    )
