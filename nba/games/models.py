"""
nba.games.models -- NBA moneyline, spread and totals models.

  spread  : regress the home margin (ridge on `features.margin_matrix`); P(home covers L) = Phi((mu + L) / sigma), sigma =
            training residual sd (~12-13 pts)
  totals  : regress the game total (ridge on `features.total_matrix`); P(over T) = 1 - Phi((T - mu) / sigma)
  moneyline: L2 logistic on the margin matrix, and (as a cross-check) Phi(mu_margin / sigma) from the spread model

Ridge/logistic are deliberate: ~25k games and noisy features, and the previous HistGBM moneyline lost to the line
(handoffs/handoff_nba_backtest_2026-06-15.md). A HistGradientBoosting variant (`kind="hgb"`) is kept for comparison only.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from nba.games import features as F


def _reg(kind: str):
    if kind == "hgb":
        return HistGradientBoostingRegressor(max_depth=3, learning_rate=0.04, max_iter=250, min_samples_leaf=80,
                                             l2_regularization=5.0, random_state=0)
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=300.0))


def _clf(kind: str):
    if kind == "hgb":
        return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.04, max_iter=250, min_samples_leaf=80,
                                              l2_regularization=5.0, random_state=0)
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.05, max_iter=500))


@dataclass
class GameModels:
    kind: str
    margin: object
    margin_sigma: float
    total: object
    total_sigma: float
    ml: object
    avail: bool = False

    def predict(self, f: pd.DataFrame) -> pd.DataFrame:
        Xm, Xt = F.margin_matrix(f, self.avail), F.total_matrix(f, self.avail)
        mu_m, mu_t = self.margin.predict(Xm), self.total.predict(Xt)
        return pd.DataFrame({
            "mu_margin": mu_m, "sigma_margin": self.margin_sigma, "mu_total": mu_t, "sigma_total": self.total_sigma,
            "p_ml": self.ml.predict_proba(Xm)[:, 1], "p_ml_margin": norm.cdf(mu_m / self.margin_sigma)}, index=f.index)


def fit(train: pd.DataFrame, kind: str = "ridge", avail: bool = False) -> GameModels:
    """`train` = feature table rows with targets (margin, total, home_win). Rows with no history yet are dropped."""
    t = train.dropna(subset=["h_e_ortg", "a_e_ortg", "lg_total"])
    if len(t) < 200:
        raise ValueError(f"only {len(t)} usable training games; refusing to fit (is the history mirror empty?)")
    Xm, Xt = F.margin_matrix(t, avail), F.total_matrix(t, avail)
    m = _reg(kind).fit(Xm, t["margin"])
    tt = _reg(kind).fit(Xt, t["total"])
    ml = _clf(kind).fit(Xm, t["home_win"])
    return GameModels(kind, m, float(np.std(t["margin"] - m.predict(Xm))), tt,
                      float(np.std(t["total"] - tt.predict(Xt))), ml, avail)


# ---- pricing helpers (also used by the evaluator) -------------------------------------------------------------------

def p_home_cover(mu_margin, sigma, home_line):
    """home_line = home handicap as quoted (home -2.5 -> -2.5). Continuous model, so a push has probability 0 here."""
    return norm.cdf((np.asarray(mu_margin) + np.asarray(home_line)) / sigma)


def p_over(mu_total, sigma, total_line):
    return 1.0 - norm.cdf((np.asarray(total_line) - np.asarray(mu_total)) / sigma)
