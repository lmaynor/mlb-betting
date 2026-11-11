"""
mlb_core.risk.residual -- market-anchored RESIDUAL training (Argon Pack 4 direction D2, 2026-11-10).

Doctrine (binding): every prop model shifts from standalone outcome estimation to market-anchored
residual modeling. The de-vigged consensus probability enters as a FIXED OFFSET in log-odds space,

    logit(p) = logit(p_market)  +  f(features)          <- the learner fits ONLY f (the residual)

The offset's coefficient is 1.0 by construction and is never a learned parameter: neither learner in
this module has a parameter that multiplies logit(p_market). (CLV-as-training-target was considered
and REJECTED by the same direction consult -- nothing here trains on line movement or CLV.)

Learners:
  * OffsetLogit -- penalized (ridge) logistic regression fit by damped Newton / IRLS on the residual.
    This is the "penalized logit" half of Argon's "LightGBM / penalized logit" (lightgbm is not in the
    repo's environments; the penalized logit is the portable default and the one wired for serving).
  * OffsetGBDT -- shallow XGBoost with base_margin = logit(p_market), for offline studies where
    xgboost is present (mirrors mlb_core.odds.props_residual.fit_gbdt). Lazy import; never required.

Opt-in: residual training is OFF by default for every (sport, stat). A sport/stat opts in explicitly
via opt_in() / RESIDUAL_OPT_IN (populated by the training job that produced a validated artifact), so
existing prop models and serving paths are untouched until their own direction promotes them. The
offline study harness in mlb_core.odds.props_residual keeps its own protocol; this module is the
shared, sport-agnostic plumbing its findings graduate into.

Artifacts are compact plain-dict models (to_dict / from_dict): a residual model is a weight vector
plus feature standardization, small enough to push as a JSON artifact -- no heavy training on GCP.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

_EPS = 1e-6


def logit(p):
    """Log-odds of a probability (scalar or array), clipped away from 0 / 1."""
    p = np.clip(np.asarray(p, dtype=float), _EPS, 1.0 - _EPS)
    return np.log(p / (1.0 - p))


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.asarray(z, dtype=float)))


def devig_offset(p_market):
    """The FIXED offset for a row: log-odds of the de-vigged consensus probability."""
    return logit(p_market)


# ------------------------------------------------------------------------------------------------ opt-in registry
# (sport, stat) pairs whose models train / serve through the residual path. Empty on purpose: opting a
# stat in is a per-direction decision backed by its own vs-market study, never a global flip.
RESIDUAL_OPT_IN: set = set()


def opt_in(sport: str, stat: str) -> None:
    RESIDUAL_OPT_IN.add((str(sport).lower(), str(stat).lower()))


def opt_out(sport: str, stat: str) -> None:
    RESIDUAL_OPT_IN.discard((str(sport).lower(), str(stat).lower()))


def clear_opt_ins() -> None:
    """Tests only."""
    RESIDUAL_OPT_IN.clear()


def residual_enabled(sport: str | None, stat: str | None) -> bool:
    """True only when (sport, stat) has explicitly opted in to residual training."""
    if sport is None or stat is None:
        return False
    return (str(sport).lower(), str(stat).lower()) in RESIDUAL_OPT_IN


# ------------------------------------------------------------------------------------------------ features
def residual_features(p_model, p_market, season_avg_minus_line=None, line=None, sd=None,
                      p_sharp=None) -> dict:
    """The D2 feature set for one prop row, all market-relative (unit-free):

      model_vs_line_z   -- (model mean - line) / sd when a mean is available, else the logit gap scaled;
                           here built from probabilities: logit(p_model) - logit(p_market) is `gap_logodds`,
                           and model_vs_line_z is that gap in probability points over the market sd.
      gap_logodds       -- logit(p_model) - logit(p_market): how far the standalone model sits from the line.
      season_avg_minus_modal_line -- season average minus the modal (consensus) line, in sd units when sd given.
      variance_ratio    -- (model-implied sd / market-implied sd) is not observable from two probabilities, so
                           this carries the probability-space dispersion proxy |p_model - p_market| / sqrt(p_market(1-p_market)).
      sharp_skew        -- logit(p_sharp) - logit(p_market) when a sharp book quotes, else 0.0.

    Pure function of the row's numbers; missing inputs degrade to 0.0 (no signal), never to a guess."""
    pm = float(np.clip(p_market, _EPS, 1.0 - _EPS)) if p_market is not None else 0.5
    gap = 0.0
    if p_model is not None and np.isfinite(float(p_model)):
        gap = float(logit(float(np.clip(p_model, _EPS, 1.0 - _EPS))) - logit(pm))
    mkt_sd = math.sqrt(max(pm * (1.0 - pm), 1e-9))
    z = 0.0
    if season_avg_minus_line is not None and line is not None and sd:
        z = float(np.clip((float(season_avg_minus_line) - float(line)) / float(sd), -4.0, 4.0))
    var_ratio = abs(float(p_model) - pm) / mkt_sd if p_model is not None and np.isfinite(float(p_model)) else 0.0
    skew = 0.0
    if p_sharp is not None and np.isfinite(float(p_sharp)):
        skew = float(logit(float(np.clip(p_sharp, _EPS, 1.0 - _EPS))) - logit(pm))
    return {"gap_logodds": gap, "model_vs_line_z": gap / max(mkt_sd, 1e-9) * 0.25,
            "season_avg_minus_modal_line_z": z, "variance_ratio": float(var_ratio), "sharp_skew": skew}


# ------------------------------------------------------------------------------------------------ penalized logit on the residual
@dataclass
class OffsetLogit:
    """Penalized logistic regression whose ONLY market term is the fixed offset.

    `coef` multiplies the standardized FEATURES (the residual); `intercept` is a residual centering
    term (Argon's rolling log-odds centering lives here). There is deliberately NO coefficient on
    logit(p_market): offset_coef is the constant 1.0, exposed read-only so tests can prove it."""
    feature_names: list
    coef: np.ndarray
    intercept: float = 0.0
    feature_mean: np.ndarray = field(default=None)
    feature_sd: np.ndarray = field(default=None)
    l2: float = 10.0

    @property
    def offset_coef(self) -> float:
        return 1.0

    def _design(self, X) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        return (X - self.feature_mean) / self.feature_sd

    def residual_margin(self, X) -> np.ndarray:
        """f(features): the learned residual in log-odds space (never includes the offset)."""
        return self.intercept + self._design(X) @ self.coef

    def predict(self, X, p_market) -> np.ndarray:
        """sigmoid( logit(p_market) [FIXED offset] + residual_margin(X) )."""
        return sigmoid(devig_offset(p_market) + self.residual_margin(X))

    def to_dict(self) -> dict:
        return {"kind": "offset_logit", "feature_names": list(self.feature_names),
                "coef": [float(c) for c in self.coef], "intercept": float(self.intercept),
                "feature_mean": [float(v) for v in self.feature_mean],
                "feature_sd": [float(v) for v in self.feature_sd], "l2": float(self.l2),
                "offset": "logit(p_market)", "offset_coef": 1.0}

    @classmethod
    def from_dict(cls, d: dict) -> "OffsetLogit":
        if d.get("offset_coef") not in (1.0, 1):
            raise ValueError("residual artifact does not carry the fixed offset (offset_coef must be 1.0)")
        return cls(feature_names=list(d["feature_names"]), coef=np.asarray(d["coef"], dtype=float),
                   intercept=float(d["intercept"]), feature_mean=np.asarray(d["feature_mean"], dtype=float),
                   feature_sd=np.asarray(d["feature_sd"], dtype=float), l2=float(d.get("l2", 10.0)))


def fit_offset_logit(X, y, p_market, feature_names=None, l2: float = 10.0, max_iter: int = 100) -> OffsetLogit:
    """Fit the residual by damped Newton (IRLS) with the market logit as a FIXED offset.

    X: (n, k) features; y: 0/1 outcomes; p_market: de-vigged consensus P(over) per row. The ridge
    penalty applies to the residual coefficients (and a token 1e-3 to the residual intercept); the
    offset is added to every linear predictor verbatim and is never differentiated as a parameter."""
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    y = np.asarray(y, dtype=float)
    offset = np.asarray(devig_offset(p_market), dtype=float)
    mu, sd = X.mean(0), np.maximum(X.std(0), 1e-6)
    Xs = (X - mu) / sd
    n_feat = X.shape[1]
    pen = np.concatenate([[1e-3], np.full(n_feat, float(l2))])
    Xb = np.column_stack([np.ones(len(Xs)), Xs])
    w = np.zeros(n_feat + 1)

    def obj(wv):
        eta = offset + Xb @ wv
        return float(np.sum(np.logaddexp(0.0, eta) - y * eta) + 0.5 * np.sum(pen * wv ** 2))

    cur = obj(w)
    for _ in range(max_iter):
        p = sigmoid(offset + Xb @ w)
        grad = Xb.T @ (y - p) - pen * w
        H = (Xb * (p * (1.0 - p))[:, None]).T @ Xb + np.diag(pen)
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(H, grad, rcond=None)[0]
        t = 1.0
        new = cur
        while t > 1e-4:
            new = obj(w + t * step)
            if new <= cur + 1e-12:
                break
            t *= 0.5
        w = w + t * step
        if abs(cur - new) < 1e-10 * max(1.0, abs(cur)):
            cur = new
            break
        cur = new
    names = list(feature_names) if feature_names is not None else [f"x{i}" for i in range(n_feat)]
    return OffsetLogit(feature_names=names, coef=w[1:].copy(), intercept=float(w[0]),
                       feature_mean=mu, feature_sd=sd, l2=float(l2))


# ------------------------------------------------------------------------------------------------ GBDT on the residual (offline)
class OffsetGBDT:
    """Shallow XGBoost residual learner: trees split only on the closing-line error because the
    market logit is the DMatrix base_margin (the fixed offset), exactly as in
    mlb_core.odds.props_residual.fit_gbdt. Lazy xgboost import."""

    def __init__(self, bst, feature_names):
        self.bst = bst
        self.feature_names = list(feature_names)

    @property
    def offset_coef(self) -> float:
        return 1.0

    def _dm(self, X, p_market=None):
        import xgboost as xgb
        X = np.asarray(X, dtype=float)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        kw = {} if p_market is None else {"base_margin": np.asarray(devig_offset(p_market), dtype=float)}
        return xgb.DMatrix(X, feature_names=list(self.feature_names), **kw)

    def residual_margin(self, X) -> np.ndarray:
        # base_margin = 0 -> the trees' raw margin IS the learned residual.
        return np.asarray(self.bst.predict(self._dm(X, np.full(len(np.atleast_2d(X)), 0.5)), output_margin=True), dtype=float)

    def predict(self, X, p_market) -> np.ndarray:
        return np.asarray(self.bst.predict(self._dm(X, p_market)), dtype=float)

    def to_dict(self) -> dict:
        raise NotImplementedError("GBDT artifacts ship as booster files (bst.save_model), not dicts; "
                                  "the dict artifact contract is the OffsetLogit's")


def fit_offset_gbdt(X, y, p_market, feature_names=None, max_depth: int = 2, rounds: int = 200,
                    eta: float = 0.02, seed: int = 7) -> OffsetGBDT:
    """Fit the GBDT residual learner with base_margin = logit(p_market) (the fixed offset)."""
    import xgboost as xgb
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    names = list(feature_names) if feature_names is not None else [f"x{i}" for i in range(X.shape[1])]
    dm = xgb.DMatrix(X, label=np.asarray(y, dtype=float),
                     base_margin=np.asarray(devig_offset(p_market), dtype=float), feature_names=names)
    params = {"objective": "binary:logistic", "eta": eta, "max_depth": max_depth, "min_child_weight": 60.0,
              "subsample": 0.7, "colsample_bytree": 0.8, "reg_lambda": 100.0, "tree_method": "hist",
              "seed": seed, "verbosity": 0}
    bst = xgb.train(params, dm, num_boost_round=rounds)
    return OffsetGBDT(bst, names)


# ------------------------------------------------------------------------------------------------ opt-in training entry point
def fit_residual_model(X, y, p_market, sport: str, stat: str, feature_names=None, learner: str = "logit",
                       **kw):
    """Train a residual model for (sport, stat). REFUSES unless the stat has opted in: residual
    training is a per-stat promotion backed by a vs-market study, not a default. Returns the fitted
    model (OffsetLogit by default, OffsetGBDT when learner='gbdt')."""
    if not residual_enabled(sport, stat):
        raise ValueError(f"residual training is not opted in for ({sport}, {stat}); "
                         "call residual.opt_in(sport, stat) from the promoting direction first")
    if learner == "logit":
        return fit_offset_logit(X, y, p_market, feature_names=feature_names, **kw)
    if learner == "gbdt":
        return fit_offset_gbdt(X, y, p_market, feature_names=feature_names, **kw)
    raise ValueError(f"unknown residual learner {learner!r} (expected 'logit' or 'gbdt')")
