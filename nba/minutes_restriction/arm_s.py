"""nba.minutes_restriction.arm_s -- the naive arm and the Arm S structured model, walk-forward.

Protocol (pre-registered, spike section 4):

  * Dev seasons 2021-22 .. 2023-24 (season_start 2021..2023) are the ONLY seasons any fit may see.
    Test seasons 2024-25 and 2025-26 are evaluated separately (and pooled by grade.py).
  * The model is fit ONCE on the dev seasons and applied unchanged to both test seasons. An
    expanding-window refit (fitting 2024-25 before grading 2025-26) was considered and rejected
    for this harness: it would put a test season inside a later fit set, which breaks the clean
    "no test-season case appears in any fit set" property the tests pin. If a future run wants the
    refit, it is a protocol change, not a flag flip.
  * No tuning against test seasons: the model is a fixed Ridge(alpha=RIDGE_ALPHA) on the ratio
    with standardized features -- chosen a priori, deterministic (closed-form solve, no sampling;
    SEED is recorded for provenance and for any future stochastic variant).
  * The naive arm predicts the baseline itself (ratio 1.0): realized = baseline.

With fewer than MIN_TRAIN_ROWS dev cases the Ridge is not fit; the model falls back to the dev
mean realized ratio (still a structured prediction: the population haircut, shrunk onto baseline).
Predictions are clipped to RATIO_CLIP so a linear extrapolation can never predict negative or
absurd minutes.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from nba.minutes_restriction.features import FEATURE_COLUMNS, build_features

DEV_SEASONS = (2021, 2022, 2023)          # season_start years: 2021-22 .. 2023-24
TEST_SEASONS = (2024, 2025)               # 2024-25, 2025-26
RIDGE_ALPHA = 10.0
RATIO_CLIP = (0.2, 1.35)
MIN_TRAIN_ROWS = 10
SEED = 20261111

PREDICTION_COLUMNS = [
    "case_id", "player", "team", "game_date", "season_start", "season_label", "case_class",
    "games_missed", "missed_band", "is_injury_return", "is_game_two", "baseline_minutes",
    "realized_minutes", "realized_ratio", "naive_minutes", "pred_ratio", "pred_minutes",
]


def dev_fit_frame(cases: pd.DataFrame, dev_seasons=DEV_SEASONS) -> pd.DataFrame:
    """The exact rows a fit may see: dev seasons only. The walk-forward honesty test pins that this
    frame is disjoint from every test season."""
    if cases is None or not len(cases):
        return cases.iloc[0:0].copy() if cases is not None else pd.DataFrame()
    return cases[cases["season_start"].isin(list(dev_seasons))].copy()


class ArmSModel:
    """Ridge regression on realized_ratio over build_features, with a mean-ratio fallback."""

    def __init__(self, alpha: float = RIDGE_ALPHA):
        self.alpha = float(alpha)
        self._pipeline = None
        self._mean_ratio: float | None = None
        self.train_seasons_: list = []
        self.n_train_: int = 0
        self.used_fallback_: bool = False

    def fit(self, cases: pd.DataFrame) -> "ArmSModel":
        if cases is None or not len(cases):
            raise ValueError("ArmSModel.fit: no training cases")
        y = pd.to_numeric(cases["realized_ratio"], errors="coerce").to_numpy(dtype=float)
        ok = np.isfinite(y)
        if ok.sum() == 0:
            raise ValueError("ArmSModel.fit: no finite realized_ratio in training cases")
        self.train_seasons_ = sorted(int(s) for s in pd.unique(cases["season_start"]))
        self.n_train_ = int(ok.sum())
        self._mean_ratio = float(np.mean(y[ok]))
        if self.n_train_ < MIN_TRAIN_ROWS:
            self._pipeline = None
            self.used_fallback_ = True
            return self
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        X = build_features(cases).to_numpy(dtype=float)[ok]
        self._pipeline = make_pipeline(StandardScaler(), Ridge(alpha=self.alpha))
        self._pipeline.fit(X, y[ok])
        self.used_fallback_ = False
        return self

    def predict_ratio(self, cases: pd.DataFrame) -> np.ndarray:
        if self._mean_ratio is None:
            raise RuntimeError("ArmSModel.predict_ratio called before fit")
        if cases is None or not len(cases):
            return np.array([], dtype=float)
        if self._pipeline is None:
            raw = np.full(len(cases), self._mean_ratio, dtype=float)
        else:
            raw = self._pipeline.predict(build_features(cases).to_numpy(dtype=float))
        return np.clip(np.asarray(raw, dtype=float), RATIO_CLIP[0], RATIO_CLIP[1])


def walk_forward(cases: pd.DataFrame, *, dev_seasons=DEV_SEASONS,
                 test_seasons=TEST_SEASONS) -> pd.DataFrame:
    """Fit Arm S on dev seasons only; predict the naive and Arm S arms for every test-season case.

    -> predictions frame (PREDICTION_COLUMNS) with attrs:
       fit_seasons (the seasons the single fit saw), n_train, used_fallback.
    Raises ValueError when there is nothing to fit or nothing to grade: fail closed, never a
    silently empty verdict.
    """
    train = dev_fit_frame(cases, dev_seasons)
    if not len(train):
        raise ValueError(f"walk_forward: no dev-season cases to fit (dev={list(dev_seasons)})")
    model = ArmSModel().fit(train)
    if cases is None or not len(cases):
        raise ValueError("walk_forward: no cases at all")
    test = cases[cases["season_start"].isin(list(test_seasons))].copy()
    if not len(test):
        raise ValueError(f"walk_forward: no test-season cases to grade (test={list(test_seasons)})")
    pred_ratio = model.predict_ratio(test)
    baseline = pd.to_numeric(test["baseline_minutes"], errors="coerce").to_numpy(dtype=float)
    out = pd.DataFrame({
        "case_id": test["case_id"].values,
        "player": test["player"].values,
        "team": test["team"].values,
        "game_date": test["game_date"].values,
        "season_start": test["season_start"].astype(int).values,
        "season_label": test["season_label"].values,
        "case_class": test["case_class"].values,
        "games_missed": test["games_missed"].astype(int).values,
        "missed_band": test["missed_band"].values,
        "is_injury_return": test["is_injury_return"].astype(bool).values,
        "is_game_two": test["is_game_two"].astype(bool).values,
        "baseline_minutes": baseline,
        "realized_minutes": pd.to_numeric(test["realized_minutes"], errors="coerce").to_numpy(dtype=float),
        "realized_ratio": pd.to_numeric(test["realized_ratio"], errors="coerce").to_numpy(dtype=float),
        "naive_minutes": baseline,
        "pred_ratio": pred_ratio,
        "pred_minutes": pred_ratio * baseline,
    }, columns=PREDICTION_COLUMNS)
    out.attrs["fit_seasons"] = model.train_seasons_
    out.attrs["n_train"] = model.n_train_
    out.attrs["used_fallback"] = model.used_fallback_
    return out.reset_index(drop=True)
