"""NFL player-prop models: XGBoost mean model + a distributional layer that turns mu into P(over a line).

Pattern follows mlb/training/retrain_batter_hits_v1.py + mlb/runners/run_batter_hits.py: XGBoost predicts the
conditional MEAN, a dispersion parameter fitted on OUT-OF-SAMPLE (walk-forward) residuals turns it into a
distribution, and a meta JSON records features / feature_means / dispersion / version so a runner never hardcodes
a feature list.

Per stat (see SPECS)
    receptions, pass_tds      count:poisson  -> NegBin(mu, alpha)           (var = mu + alpha*mu^2)
    pass_attempts, completions, rush_attempts   same count:poisson -> NegBin path (added 2026-10-14, unvalidated)
    anytime_td                count:poisson on td_count (rush+rec TDs) -> NegBin; "anytime" = P(count >= 1) =
                              P(over 0.5)
    pass_yds, rush_yds,       reg:tweedie (variance power 1.5: compound Poisson-gamma -- exact zeros plus a
    rec_yds                   right-skewed positive part, log link keeps mu > 0) -> hurdle-gamma (see below)

Why hurdle-gamma for yards
    Yards are non-negative, right-skewed, roughly proportional-spread (CV falls slowly with volume), with a
    real point mass at 0 (receivers with a target or two who catch nothing) that a plain NegBin/gamma cannot
    represent. Model: P(Y=0) = pi(mu) = sigmoid(a + b*log mu); positive part ~ Gamma(shape k, mean mu/(1-pi)),
    discretised with a continuity correction (P(Y <= j) = G(j + 0.5)), so E[Y] stays = mu. The gamma shape varies
    with volume: k(mu) = exp(log_k + kb*log(mu/50)). (a, b, log_k, kb) are fitted
    by maximum likelihood on out-of-sample residuals, never in-sample. `family="negbin"` is also supported for
    yards (used as a comparison in walkforward). Negative rushing yards are clipped to 0 for training/scoring
    (a handful of kneel-down rows).

p_over math
    `p_over(stat, mu, line, params)` -> (p_over, p_under, p_push). Half-point lines never push. For an integer
    line L: push = P(Y = L), over = P(Y > L), under = P(Y < L). Always p_over + p_under + p_push = 1.
    NOTE all probabilities are CONDITIONAL ON THE PLAYER PLAYING (see nfl/features/props_features.py docstring).

THIS MODULE SAYS NOTHING ABOUT BETTING EDGE. There are no historical NFL prop lines in this repo; calibration
and accuracy are measured against outcomes and trailing-mean baselines only.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import xgboost as xgb
from scipy import optimize, special, stats as sps

from nfl.features.props_features import ELIG, feature_columns

logger = logging.getLogger(__name__)

VERSION = "v1"
DEFAULT_MODEL_DIR = os.path.join(os.path.expanduser("~"), ".cache", "beezy", "nfl_models")


def model_dir(directory: Optional[str] = None) -> str:
    """NFL_MODEL_DIR env var, else `directory`, else ~/.cache/beezy/nfl_models. Never GCS."""
    return directory or os.environ.get("NFL_MODEL_DIR") or DEFAULT_MODEL_DIR


# ------------------------------------------------------------------------------------------------ specs
@dataclass(frozen=True)
class StatSpec:
    name: str
    target: str          # training target column in the feature frame
    kind: str            # "count" | "yards"
    objective: str
    eval_metric: str
    elig: str            # eligibility column in the feature frame
    family: str          # default distribution family; "auto" = pick by penalised OOF log-score
    clip_min: Optional[float] = 0.0


_POISSON = ("count:poisson", "poisson-nloglik")
_TWEEDIE = ("reg:tweedie", "tweedie-nloglik@1.5")
SPECS = {
    "pass_yds": StatSpec("pass_yds", "y_pass_yds", "yards", *_TWEEDIE, "elig_pass_yds", "auto"),
    "rush_yds": StatSpec("rush_yds", "y_rush_yds", "yards", *_TWEEDIE, "elig_rush_yds", "auto"),
    "rec_yds": StatSpec("rec_yds", "y_rec_yds", "yards", *_TWEEDIE, "elig_rec_yds", "auto"),
    "receptions": StatSpec("receptions", "y_receptions", "count", *_POISSON, "elig_receptions", "negbin"),
    "pass_tds": StatSpec("pass_tds", "y_pass_tds", "count", *_POISSON, "elig_pass_tds", "negbin"),
    "anytime_td": StatSpec("anytime_td", "y_td_count", "count", *_POISSON, "elig_anytime_td", "negbin"),
    # volume counts (added 2026-10-14; no walk-forward run yet). Same eligibility populations as the yardage markets.
    "pass_attempts": StatSpec("pass_attempts", "y_pass_attempts", "count", *_POISSON, "elig_pass_yds", "negbin"),
    "completions": StatSpec("completions", "y_completions", "count", *_POISSON, "elig_pass_yds", "negbin"),
    "rush_attempts": StatSpec("rush_attempts", "y_rush_attempts", "count", *_POISSON, "elig_rush_yds", "negbin"),
}
# The stats the walk-forward CLI runs by default (the original six); the volume counts are opt-in via --stats.
DEFAULT_STATS = ("pass_yds", "rush_yds", "rec_yds", "receptions", "pass_tds", "anytime_td")

XGB_PARAMS = {
    "max_depth": 3, "learning_rate": 0.03, "subsample": 0.8, "colsample_bytree": 0.5, "min_child_weight": 25,
    "reg_alpha": 1.0, "reg_lambda": 8.0, "gamma": 0.3, "seed": 42, "nthread": 4, "tree_method": "hist",
}
NUM_BOOST_ROUND = 1500
EARLY_STOPPING_ROUNDS = 60
MU_FLOOR = 1e-3


# ------------------------------------------------------------------------------------------------ distributions
def _sigmoid(x):
    return special.expit(x)


def _as_arrays(mu, line):
    mu = np.clip(np.asarray(mu, dtype=float), MU_FLOOR, None)
    line = np.asarray(line, dtype=float)
    mu, line = np.broadcast_arrays(mu, line)
    return mu, line


def negbin_cdf(k, mu, alpha: float):
    """P(Y <= k), NegBin with mean mu and var = mu + alpha*mu^2 (alpha -> 0 is Poisson). k may be non-integer
    (floored) and negative (-> 0)."""
    k = np.floor(np.asarray(k, dtype=float))
    mu = np.clip(np.asarray(mu, dtype=float), MU_FLOOR, None)
    if alpha < 1e-6:
        out = sps.poisson.cdf(k, mu)
    else:
        n = 1.0 / alpha
        out = sps.nbinom.cdf(k, n, n / (n + mu))
    return np.where(k < 0, 0.0, out)


HG_REF_MU = 50.0


def _hg_parts(mu, params):
    """(pi, shape, scale) of the hurdle-gamma at mean mu. shape(mu) = exp(log_k + kb*log(mu/50)): the coefficient
    of variation of NFL yardage FALLS with volume (Tweedie-like var ~ mu^p, p<2), so a single constant shape
    over-disperses starters and under-disperses fringe players."""
    lm = np.log(np.clip(mu, MU_FLOOR, None))
    pi = _sigmoid(params["a"] + params["b"] * lm)
    pi = np.clip(pi, 1e-9, 0.95)
    shape = np.clip(np.exp(params["log_k"] + params.get("kb", 0.0) * (lm - np.log(HG_REF_MU))), 0.3, 120.0)
    scale = (mu / (1.0 - pi)) / shape
    return pi, shape, scale


def hgamma_cdf(k, mu, params):
    """P(Y <= k) for integer k under the hurdle-gamma (continuity-corrected) yards model."""
    k = np.floor(np.asarray(k, dtype=float))
    mu = np.clip(np.asarray(mu, dtype=float), MU_FLOOR, None)
    pi, shape, scale = _hg_parts(mu, params)
    G = sps.gamma.cdf(np.maximum(k, 0) + 0.5, shape, scale=scale)
    out = np.where(k < 0, 0.0, np.where(k < 1, pi, pi + (1.0 - pi) * G))
    return out


def _normal_sd(mu, params):
    return np.exp(params["s0"]) * (np.clip(mu, MU_FLOOR, None) / 100.0) ** params["sp"]


def normal_cdf(k, mu, params):
    """P(Y <= k) for a discretised, zero-truncated Normal(mu, sd(mu)): everything below -0.5 lumps into Y = 0.
    sd(mu) = exp(s0) * (mu/100)^sp. Symmetric/left-skew friendly (unlike gamma/NegBin) -- QB passing yardage has a
    heavy LEFT tail (early exits, blowouts) and P(Y > mean) > 0.5."""
    k = np.floor(np.asarray(k, dtype=float))
    mu = np.clip(np.asarray(mu, dtype=float), MU_FLOOR, None)
    out = sps.norm.cdf((np.maximum(k, 0) + 0.5 - mu) / _normal_sd(mu, params))
    return np.where(k < 0, 0.0, out)


def normal_logpmf(y, mu, params):
    y = np.clip(np.round(np.asarray(y, float)), 0, None)
    mu = np.clip(np.asarray(mu, float), MU_FLOOR, None)
    sd = _normal_sd(mu, params)
    hi = sps.norm.cdf((y + 0.5 - mu) / sd)
    lo = np.where(y <= 0, 0.0, sps.norm.cdf((y - 0.5 - mu) / sd))
    return np.log(np.clip(hi - lo, 1e-300, None))


def fit_normal(mu, y) -> dict:
    mu = np.clip(np.asarray(mu, float), MU_FLOOR, None)
    y = np.clip(np.round(np.asarray(y, float)), 0, None)

    def nll(t):
        return -float(np.sum(normal_logpmf(y, mu, {"s0": t[0], "sp": t[1]})))

    res = optimize.minimize(nll, x0=np.array([np.log(max(float(np.std(y - mu)), 1.0)), 0.5]), method="Nelder-Mead",
                            options={"xatol": 1e-3, "fatol": 1e-3, "maxiter": 800})
    return {"family": "normal", "s0": float(res.x[0]), "sp": float(np.clip(res.x[1], -0.5, 1.5))}


def dist_cdf(family: str, k, mu, params):
    if family == "negbin":
        return negbin_cdf(k, mu, params["alpha"])
    if family == "hgamma":
        return hgamma_cdf(k, mu, params)
    if family == "normal":
        return normal_cdf(k, mu, params)
    raise ValueError(f"unknown distribution family {family!r}")


def p_over(stat: str, mu, line, params: dict):
    """(p_over, p_under, p_push) for P(stat > line), P(stat < line), P(stat == line).

    `params` is the model's dispersion block: {"family": "negbin"|"hgamma", ...family params}. `stat` is accepted
    for symmetry / validation; the family in `params` decides the maths."""
    if stat not in SPECS:
        raise ValueError(f"unknown stat {stat!r}; known: {sorted(SPECS)}")
    family = params["family"]
    mu_a, line_a = _as_arrays(mu, line)
    is_int = np.isclose(line_a, np.round(line_a))
    under_k = np.where(is_int, np.round(line_a) - 1.0, np.floor(line_a))
    cdf_u = dist_cdf(family, under_k, mu_a, params)
    cdf_l = np.where(is_int, dist_cdf(family, np.round(line_a), mu_a, params), cdf_u)
    push = np.clip(cdf_l - cdf_u, 0.0, 1.0)
    over = np.clip(1.0 - cdf_l, 0.0, 1.0)
    under = np.clip(cdf_u, 0.0, 1.0)
    if np.ndim(mu) == 0 and np.ndim(line) == 0:
        return float(over), float(under), float(push)
    return over, under, push


def _nll_negbin(mu, y, alpha):
    n = 1.0 / max(alpha, 1e-6)
    return -np.sum(sps.nbinom.logpmf(y, n, n / (n + mu)))


def fit_negbin_alpha(mu, y) -> dict:
    """MLE of the NegBin dispersion on (out-of-sample) predictions. alpha in [1e-6, 5]."""
    mu = np.clip(np.asarray(mu, float), MU_FLOOR, None)
    y = np.round(np.asarray(y, float))
    res = optimize.minimize_scalar(lambda la: _nll_negbin(mu, y, np.exp(la)), bounds=(np.log(1e-6), np.log(5.0)),
                                   method="bounded")
    return {"family": "negbin", "alpha": float(np.exp(res.x))}


def hgamma_logpmf(y, mu, params):
    y = np.round(np.asarray(y, float))
    mu = np.clip(np.asarray(mu, float), MU_FLOOR, None)
    pi, shape, scale = _hg_parts(mu, params)
    lo = sps.gamma.cdf(np.maximum(y - 0.5, 0.0), shape, scale=scale)
    hi = sps.gamma.cdf(y + 0.5, shape, scale=scale)
    # positive part: y == 1 absorbs everything below 1.5 (the hurdle already took exact zeros)
    pos = np.where(y <= 1, hi, hi - lo)
    pmf = np.where(y <= 0, pi, (1.0 - pi) * pos)
    return np.log(np.clip(pmf, 1e-300, None))


def fit_hgamma(mu, y) -> dict:
    mu = np.clip(np.asarray(mu, float), MU_FLOOR, None)
    y = np.clip(np.round(np.asarray(y, float)), 0, None)

    def nll(theta):
        p = {"a": theta[0], "b": theta[1], "log_k": theta[2], "kb": theta[3]}
        return -float(np.sum(hgamma_logpmf(y, mu, p)))

    best = None
    for k0 in (0.8, 2.0):
        res = optimize.minimize(nll, x0=np.array([-2.0, -0.5, np.log(k0), 0.3]), method="Nelder-Mead",
                                options={"xatol": 1e-3, "fatol": 1e-3, "maxiter": 1200})
        if best is None or res.fun < best.fun:
            best = res
    a, b, log_k, kb = best.x
    return {"family": "hgamma", "a": float(a), "b": float(b), "log_k": float(np.clip(log_k, np.log(0.3), np.log(120))),
            "kb": float(np.clip(kb, -1.5, 3.0))}


def fit_dispersion(family: str, mu, y) -> dict:
    if family == "negbin":
        return fit_negbin_alpha(mu, y)
    if family == "hgamma":
        return fit_hgamma(mu, y)
    if family == "normal":
        return fit_normal(mu, y)
    raise ValueError(family)


def row_logscore(family: str, mu, y, params) -> np.ndarray:
    """Per-row log pmf of integer-rounded y under the family."""
    y = np.round(np.asarray(y, float))
    mu = np.clip(np.asarray(mu, float), MU_FLOOR, None)
    if family == "negbin":
        n = 1.0 / max(params["alpha"], 1e-6)
        return sps.nbinom.logpmf(y, n, n / (n + mu))
    if family == "hgamma":
        return hgamma_logpmf(np.clip(y, 0, None), mu, params)
    if family == "normal":
        return normal_logpmf(y, mu, params)
    raise ValueError(family)


_N_PARAMS = {"negbin": 1, "hgamma": 4, "normal": 2}


def select_family(candidates, mu, y):
    """Pick the family with the best penalised (BIC) mean log-score on OOF residuals. Returns (params, all_fits)."""
    fits, scores = {}, {}
    n = max(len(mu), 1)
    for fam in candidates:
        fits[fam] = fit_dispersion(fam, mu, y)
        scores[fam] = float(np.mean(row_logscore(fam, mu, y, fits[fam]))) - 0.5 * _N_PARAMS[fam] * np.log(n) / n
    best = max(scores, key=scores.get)
    return fits[best], fits, scores


def mean_log_score(family: str, mu, y, params) -> float:
    return float(np.mean(row_logscore(family, mu, y, params)))


# ------------------------------------------------------------------------------------------------ training
@dataclass
class NFLPropModel:
    stat: str
    booster: xgb.Booster
    meta: dict

    @property
    def features(self) -> list:
        return self.meta["features"]

    @property
    def dispersion(self) -> dict:
        return self.meta["dispersion"]


def training_rows(df: pd.DataFrame, stat: str) -> pd.DataFrame:
    """Eligible, played, labelled rows for `stat` (never upcoming / unlabelled rows)."""
    spec = SPECS[stat]
    m = (df[spec.elig] == 1) & df[spec.target].notna() & ~df["is_upcoming"].astype(bool)
    return df[m]


def _y(df: pd.DataFrame, spec: StatSpec) -> np.ndarray:
    y = df[spec.target].to_numpy(dtype=float)
    return np.clip(y, spec.clip_min, None) if spec.clip_min is not None else y


def _dmat(df: pd.DataFrame, features: list, y: Optional[np.ndarray] = None) -> xgb.DMatrix:
    X = df.reindex(columns=features).apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float32)
    return xgb.DMatrix(X, label=y, feature_names=features, missing=np.nan)


def _xgb_params(spec: StatSpec, overrides: Optional[dict] = None) -> dict:
    p = dict(XGB_PARAMS, objective=spec.objective, eval_metric=spec.eval_metric)
    if spec.objective == "reg:tweedie":
        p["tweedie_variance_power"] = 1.5
    if overrides:
        p.update(overrides)
    return p


def _fit(spec, tr: pd.DataFrame, features, rounds: int, overrides=None, valid: Optional[pd.DataFrame] = None):
    dtr = _dmat(tr, features, _y(tr, spec))
    params = _xgb_params(spec, overrides)
    if valid is not None:
        dv = _dmat(valid, features, _y(valid, spec))
        bst = xgb.train(params, dtr, num_boost_round=rounds, evals=[(dv, "valid")],
                        early_stopping_rounds=EARLY_STOPPING_ROUNDS, verbose_eval=False)
        return bst, int(bst.best_iteration) + 1
    return xgb.train(params, dtr, num_boost_round=rounds, verbose_eval=False), rounds


def _predict(bst: xgb.Booster, df: pd.DataFrame, features: list, best_iter: Optional[int] = None) -> np.ndarray:
    d = _dmat(df, features)
    kw = {"iteration_range": (0, best_iter)} if best_iter else {}
    return np.clip(bst.predict(d, **kw), MU_FLOOR, None)


def oof_predictions(tr: pd.DataFrame, spec: StatSpec, features: list, rounds: int, n_folds: int = 2,
                    overrides=None) -> pd.DataFrame:
    """Walk-forward out-of-sample mu for the last `n_folds` seasons of `tr` (each fold trained ONLY on earlier
    seasons). These are the residuals the dispersion is fitted on -- never in-sample predictions."""
    seasons = sorted(tr["season"].unique())
    folds = [s for s in seasons[1:]][-n_folds:]
    parts = []
    for f in folds:
        a, b = tr[tr["season"] < f], tr[tr["season"] == f]
        if len(a) < 200 or len(b) < 50:
            continue
        bst, _ = _fit(spec, a, features, rounds, overrides)
        parts.append(pd.DataFrame({"season": f, "mu": _predict(bst, b, features), "y": _y(b, spec)}))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["season", "mu", "y"])


def train_stat(df: pd.DataFrame, stat: str, family: Optional[str] = None, overrides: Optional[dict] = None,
               oof_folds: int = 4, include_experimental: bool = False,
               include_pbp_ctx: bool = False) -> NFLPropModel:
    """Fit the mean model on all eligible rows of `df` and the dispersion on walk-forward OOF residuals.

    Number of boosting rounds comes from early stopping on the LAST training season (trained on the earlier
    ones); the final model refits on everything with 1.15x those rounds. Nothing after the last training season
    is ever touched.
    """
    spec = SPECS[stat]
    family = family or spec.family
    if spec.kind == "count" and family == "auto":
        family = "negbin"
    tr = training_rows(df, stat)
    if len(tr) < 300:
        raise ValueError(f"{stat}: only {len(tr)} eligible labelled rows -- not enough to train")
    features = [f for f in feature_columns(df, include_experimental=include_experimental,
                                           include_pbp_ctx=include_pbp_ctx) if tr[f].notna().any()]
    seasons = sorted(tr["season"].unique())
    if len(seasons) >= 2:
        last = seasons[-1]
        _, best = _fit(spec, tr[tr["season"] < last], features, NUM_BOOST_ROUND, overrides, valid=tr[tr["season"] == last])
    else:
        best = 200
    rounds = max(40, int(best * 1.15))
    bst, _ = _fit(spec, tr, features, rounds, overrides)
    oof = oof_predictions(tr, spec, features, best, n_folds=oof_folds, overrides=overrides)
    all_fits, fam_scores = None, None
    if len(oof) >= 300:
        mu_o, y_o = oof["mu"].to_numpy(), oof["y"].to_numpy()
        if family == "auto":
            disp, all_fits, fam_scores = select_family(("hgamma", "normal", "negbin"), mu_o, y_o)
        else:
            disp = fit_dispersion(family, mu_o, y_o)
        oof_bias = float(oof["y"].sum() / max(oof["mu"].sum(), 1e-9))
    else:  # tiny training window: fall back to a conservative moment-matched default (flagged in meta)
        disp = {"family": "negbin", "alpha": 0.2} if spec.kind == "count" else {"family": "hgamma", "a": -2.0,
                                                                                 "b": -0.5, "log_k": float(np.log(1.2)),
                                                                                 "kb": 0.3}
        oof_bias = float("nan")
        logger.warning("%s: <300 OOF rows -- using default dispersion", stat)
    alt_disp = all_fits                                  # every candidate family fitted on the same OOF (yards)
    X = tr[features].apply(pd.to_numeric, errors="coerce")
    meta = {
        "version": VERSION, "stat": stat, "model_type": f"nfl_{stat}_{spec.objective.replace(':', '_')}",
        "objective": spec.objective, "target": spec.target, "kind": spec.kind, "eligibility_col": spec.elig,
        "eligibility_rule": {k: list(v) for k, v in ELIG.items()},
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_seasons": [int(s) for s in seasons], "n_train": int(len(tr)), "n_oof": int(len(oof)),
        "best_iteration": int(best), "num_boost_round": int(rounds), "oof_bias_ratio": oof_bias,
        "features": features,
        "feature_means": {f: (None if pd.isna(X[f].mean()) else float(X[f].mean())) for f in features},
        "dispersion": disp, "alt_dispersions": alt_disp, "family_scores": fam_scores,
        "xgb_params": _xgb_params(spec, overrides),
        "population_note": "conditional on the player playing offense; no historical prop lines -- not an edge model",
    }
    return NFLPropModel(stat, bst, meta)


def predict_mu(model: NFLPropModel, df: pd.DataFrame, fill_missing_cols: bool = True) -> np.ndarray:
    """Expected stat per row. Columns absent from `df` are filled with the training mean (with a warning) when
    fill_missing_cols, else raise. NaN VALUES are left to XGBoost's native missing-value routing (as in training)."""
    missing = [f for f in model.features if f not in df.columns]
    if missing:
        if not fill_missing_cols:
            raise KeyError(f"features missing from frame: {missing[:8]}...")
        logger.warning("%s: %d feature column(s) absent, filled with training means: %s", model.stat, len(missing),
                       missing[:5])
        df = df.copy()
        for f in missing:
            df[f] = model.meta["feature_means"].get(f)
    bi = int(model.meta["num_boost_round"])
    return _predict(model.booster, df, model.features, bi)


def predict_prob(model: NFLPropModel, df: pd.DataFrame, line) -> pd.DataFrame:
    """Per-row mu and (p_over, p_under, p_push) vs `line` (scalar or per-row array)."""
    mu = predict_mu(model, df)
    po, pu, pp = p_over(model.stat, mu, line, model.dispersion)
    return pd.DataFrame({"mu": mu, "p_over": po, "p_under": pu, "p_push": pp}, index=df.index)


# ------------------------------------------------------------------------------------------------ persistence
def _paths(stat: str, directory: Optional[str], version: str = VERSION):
    d = model_dir(directory)
    return d, os.path.join(d, f"xgb_nfl_{stat}_{version}.json"), os.path.join(d, f"model_meta_nfl_{stat}_{version}.json")


def save_model(model: NFLPropModel, directory: Optional[str] = None) -> str:
    d, bpath, mpath = _paths(model.stat, directory, model.meta.get("version", VERSION))
    os.makedirs(d, exist_ok=True)
    model.booster.save_model(bpath)
    with open(mpath, "w") as fh:
        json.dump(model.meta, fh, indent=2)
    return d


def load_model(stat: str, directory: Optional[str] = None, version: str = VERSION) -> NFLPropModel:
    _, bpath, mpath = _paths(stat, directory, version)
    with open(mpath) as fh:
        meta = json.load(fh)
    bst = xgb.Booster()
    bst.load_model(bpath)
    return NFLPropModel(stat, bst, meta)
