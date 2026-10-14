"""nba.props.train -- per-stat distributional model (XGBoost mean + heteroskedastic variance + count family).

For each stat in {points, rebounds, assists, threes, pra}:
  1. MEAN   : XGBoost (count:poisson by default: log link, so minutes x rate effects are multiplicative), early-stopped on
              the season immediately BEFORE the test season, then REFIT on train + validation with a fixed round count.
  2. SPREAD : variance of the outcome around the mean, estimated from the validation season's OUT-OF-SAMPLE residuals
              (never from training rows, whose residuals are shrunk by fitting). Two estimators:
                'binned' -- Var = phi(mu) * mu, phi by quantile bins of the predicted mean (12 numbers);
                'gbm'    -- a small XGBoost on (y-mu)^2/mu with the full feature set, so the spread WIDENS where
                            minutes/availability are uncertain (high minutes std, recent absences, a new team, ...).
  3. FAMILY : NegBin or discretised Gamma, whichever has the better validation log score for that stat.
Walk-forward by season: the model that scores season T never saw season T, and the variance/family were chosen on T-1.

ARTIFACT CONTRACT (CONTEXT s5): xgb_nba_props_{stat}_v1.json (+ _var_ booster) and model_meta_nba_props_{stat}_v1.json
with version, features, feature_means, best_iteration (the refit round count), plus family / var_kind / phi_table and the
out-of-sample metrics. Written to a LOCAL dir by default; GCS only behind the explicit --upload flag (not exercised in
this PR). Features may be NaN: XGBoost handles missing natively and the scorer feeds the same NaNs.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from nba.props import config as C
from nba.props import dist as Dd
from nba.props.features import TARGET_SHORT

log = logging.getLogger("nba.props.train")

MEAN_PARAMS = dict(objective="count:poisson", eta=0.05, max_depth=4, min_child_weight=40, subsample=0.8,
                   colsample_bytree=0.6, reg_lambda=5.0, tree_method="hist", nthread=max(1, (os.cpu_count() or 2) - 1),
                   eval_metric="poisson-nloglik", seed=7)
VAR_PARAMS = dict(objective="reg:squarederror", eta=0.05, max_depth=3, min_child_weight=150, subsample=0.8,
                  colsample_bytree=0.6, reg_lambda=20.0, tree_method="hist", nthread=MEAN_PARAMS["nthread"], seed=7)
VAR_ROUNDS = 150
MAX_ROUNDS = 2500
EARLY_STOP = 60
PHI_FLOOR = {"nbinom": 1.0, "gamma": 0.15}
PHI_CAP = 25.0


@dataclass
class StatModel:
    stat: str
    features: list
    booster: xgb.Booster
    n_rounds: int
    family: str
    phi_table: dict
    var_booster: xgb.Booster | None = None
    var_kind: str = "gbm"
    feature_means: dict = field(default_factory=dict)
    info: dict = field(default_factory=dict)
    pit_table: dict | None = None          # PIT recalibration map fitted on the validation season (dist.fit_pit_table)

    def _dm(self, X: pd.DataFrame) -> xgb.DMatrix:
        return xgb.DMatrix(X.reindex(columns=self.features).astype("float32"), feature_names=self.features)

    def predict_mu(self, X: pd.DataFrame) -> np.ndarray:
        # explicit iteration_range -- the repo's safe pattern (never None, which crashes xgboost >= 2.0)
        return self.booster.predict(self._dm(X), iteration_range=(0, self.n_rounds))

    def predict_var(self, X: pd.DataFrame, mu: np.ndarray, kind: str | None = None) -> np.ndarray:
        kind = kind or self.var_kind
        floor = PHI_FLOOR[self.family]
        mu = np.maximum(mu, Dd.MU_MIN)
        if kind == "gbm" and self.var_booster is not None:
            Z = X.reindex(columns=self.features).astype("float32").assign(mu_hat=mu.astype("float32"))
            phi = self.var_booster.predict(xgb.DMatrix(Z, feature_names=list(Z.columns)))
            return np.clip(phi, floor, PHI_CAP) * mu
        return Dd.var_from_table(mu, self.phi_table, phi_floor=floor)

    def predict(self, X: pd.DataFrame, kind: str | None = None):
        mu = self.predict_mu(X)
        return mu, self.predict_var(X, mu, kind)

    def p_over(self, line, mu, var):
        """Calibrated P(Y > line) -- the parametric family passed through the PIT recalibration map."""
        return Dd.p_over_cal(line, mu, var, self.family, self.pit_table)

    def p_push(self, line, mu, var):
        return Dd.p_push_cal(line, mu, var, self.family, self.pit_table)

    def meta(self) -> dict:
        return {"version": C.MODEL_VERSION, "system": "NBA_PROPS", "stat": self.stat, "features": self.features,
                "feature_means": self.feature_means, "best_iteration": int(self.n_rounds), "family": self.family,
                "var_kind": self.var_kind, "phi_table": self.phi_table, "pit_table": self.pit_table,
                "has_var_booster": self.var_booster is not None,
                "support_max": C.SUPPORT_MAX[self.stat], "log_only": True, **self.info}

    def save(self, out_dir) -> dict:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        names = C.artifact_names(self.stat)
        self.booster.save_model(str(d / names["booster"]))
        if self.var_booster is not None:
            self.var_booster.save_model(str(d / names["var"]))
        (d / names["meta"]).write_text(json.dumps(self.meta(), indent=1, default=_json_default))
        return {k: str(d / v) for k, v in names.items()}

    @classmethod
    def load(cls, stat: str, in_dir) -> "StatModel":
        d = Path(in_dir)
        names = C.artifact_names(stat)
        meta = json.loads((d / names["meta"]).read_text())
        b = xgb.Booster()
        b.load_model(str(d / names["booster"]))
        vb = None
        if meta.get("has_var_booster") and (d / names["var"]).exists():
            vb = xgb.Booster()
            vb.load_model(str(d / names["var"]))
        info = {k: v for k, v in meta.items() if k not in ("version", "system", "stat", "features", "feature_means",
                                                           "best_iteration", "family", "var_kind", "phi_table",
                                                           "pit_table", "has_var_booster", "support_max", "log_only")}
        return cls(stat, meta["features"], b, int(meta["best_iteration"]), meta["family"], meta["phi_table"], vb,
                   meta.get("var_kind", "binned"), meta.get("feature_means", {}), info, meta.get("pit_table"))


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.ndarray,)):
        return o.tolist()
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    raise TypeError(type(o))


def target_of(meta: pd.DataFrame, stat: str) -> np.ndarray:
    return meta[stat].to_numpy(dtype=float)


def mean_score(y, mu, var, family) -> float:
    return float(-Dd.logpmf(y, mu, var, family).mean())


def fit_stat(Xtr: pd.DataFrame, ytr, Xva: pd.DataFrame, yva, stat: str, *, params: dict | None = None,
             refit: bool = True, family: str | None = None, var_kind: str = "gbm", weights=None,
             info: dict | None = None, max_rounds: int = MAX_ROUNDS) -> StatModel:
    """Train one stat's model. `family=None` picks NegBin vs Gamma by validation log score."""
    params = {**MEAN_PARAMS, **(params or {})}
    feats = list(Xtr.columns)
    dtr = xgb.DMatrix(Xtr.astype("float32"), label=ytr, weight=weights, feature_names=feats)
    dva = xgb.DMatrix(Xva[feats].astype("float32"), label=yva, feature_names=feats)
    b = xgb.train(params, dtr, max_rounds, evals=[(dva, "val")], early_stopping_rounds=EARLY_STOP, verbose_eval=False)
    best = int(b.best_iteration) + 1
    mu_va = b.predict(dva, iteration_range=(0, best))
    table = Dd.fit_phi_table(mu_va, yva)
    fams = [family] if family else list(Dd.FAMILIES)
    scores = {f: mean_score(yva, mu_va, Dd.var_from_table(mu_va, table, PHI_FLOOR[f]), f) for f in fams}
    fam = min(scores, key=scores.get)
    # heteroskedastic spread from the validation season's out-of-sample residuals
    Zva = Xva[feats].astype("float32").assign(mu_hat=np.maximum(mu_va, Dd.MU_MIN).astype("float32"))
    t = (np.asarray(yva, dtype=float) - mu_va) ** 2 / np.maximum(mu_va, Dd.MU_MIN)
    vb = xgb.train(VAR_PARAMS, xgb.DMatrix(Zva, label=t, feature_names=list(Zva.columns)), VAR_ROUNDS)
    # PIT recalibration map, fitted on the validation season with the SAME spread the model will use at prediction time
    mu_c = np.maximum(mu_va, Dd.MU_MIN)
    if var_kind == "gbm":
        var_va = np.clip(vb.predict(xgb.DMatrix(Zva, feature_names=list(Zva.columns))), PHI_FLOOR[fam], PHI_CAP) * mu_c
    else:
        var_va = Dd.var_from_table(mu_c, table, PHI_FLOOR[fam])
    pit = Dd.fit_pit_table(yva, mu_va, var_va, fam)
    final = b
    n_rounds = best
    if refit:
        Xall = pd.concat([Xtr, Xva[feats]])
        yall = np.concatenate([np.asarray(ytr, dtype=float), np.asarray(yva, dtype=float)])
        w_all = None if weights is None else np.concatenate([np.asarray(weights), np.ones(len(yva))])
        n_rounds = max(50, int(round(best * 1.15)))
        final = xgb.train({k: v for k, v in params.items() if k != "eval_metric"},
                          xgb.DMatrix(Xall.astype("float32"), label=yall, weight=w_all, feature_names=feats), n_rounds)
    means = {c: (None if not np.isfinite(v) else float(v)) for c, v in Xtr.astype(float).mean().items()}
    return StatModel(stat, feats, final, n_rounds, fam, table, vb, var_kind, means,
                     {"val_logscore": scores, "val_best_iteration": best, **(info or {})}, pit)


# ----------------------------------------------------------------------------- walk-forward
def walk_forward(X: pd.DataFrame, meta: pd.DataFrame, stats=None, test_seasons=(2022, 2023, 2024, 2025),
                 params: dict | None = None, var_kind: str = "gbm", progress=print, return_tables: bool = False):
    """Rolling-origin evaluation predictions. For each test season T: train on seasons <= T-2, validate (early stop,
    spread, family) on T-1, refit on <= T-1, predict ALL non-preseason rows of T.

    -> long frame: stat, season, row (position in X), mu, var_binned, var_gbm, family.
    With return_tables=True -> (frame, {(stat, season): pit_table}) so the recalibration can be applied out of sample."""
    stats = stats or C.STATS
    out = []
    tables: dict = {}
    seasons = meta["season_year"].to_numpy()
    for T in test_seasons:
        tr = seasons <= T - 2
        va = seasons == T - 1
        te = seasons == T
        if not (tr.any() and va.any() and te.any()):
            progress(f"skip T={T}: empty split")
            continue
        for stat in stats:
            y = target_of(meta, stat)
            m = fit_stat(X[tr], y[tr], X[va], y[va], stat, params=params, var_kind=var_kind)
            mu, v_g = m.predict(X[te], "gbm")
            v_b = m.predict_var(X[te], mu, "binned")
            out.append(pd.DataFrame({"stat": stat, "season": T, "row": np.where(te)[0], "mu": mu, "var_binned": v_b,
                                     "var_gbm": v_g, "family": m.family}))
            tables[(stat, T)] = m.pit_table
            progress(f"T={T} {stat}: rounds={m.n_rounds} family={m.family} val={m.info['val_logscore']}")
    df = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    return (df, tables) if return_tables else df


def train_final(X: pd.DataFrame, meta: pd.DataFrame, out_dir, stats=None, last_season: int | None = None,
                params: dict | None = None, progress=print) -> dict:
    """Production-recipe fit: train on seasons <= last-1, validate on `last` (the newest FULL season), refit on both.
    Writes the artifact files for every stat to `out_dir` (local)."""
    stats = stats or C.STATS
    seasons = meta["season_year"].to_numpy()
    last = int(last_season if last_season is not None else seasons.max())
    tr, va = seasons <= last - 1, seasons == last
    written = {}
    for stat in stats:
        y = target_of(meta, stat)
        m = fit_stat(X[tr], y[tr], X[va], y[va], stat, params=params,
                     info={"trained_through_season": last, "n_train": int(tr.sum()), "n_val": int(va.sum()),
                           "built_from": "SportsBlaze NBA box scores (nba/props/features.py)"})
        written[stat] = m.save(out_dir)
        progress(f"{stat}: family={m.family} rounds={m.n_rounds} -> {written[stat]['booster']}")
    return written


def upload_artifacts(out_dir, prefix: str = C.GCS_MODEL_PREFIX) -> list:
    """EXPLICIT-FLAG ONLY. Uploads the local artifacts via mlb_core.storage (not exercised in the PR that added it)."""
    from mlb_core import storage
    keys = []
    for p in sorted(Path(out_dir).glob("*.json")):
        key = f"{prefix}/{p.name}"
        storage.upload_model(p, key)
        keys.append(key)
    return keys


def fetch_models(dest, prefix: str = C.GCS_MODEL_PREFIX, stats=None) -> Path:
    """EXPLICIT-FLAG ONLY (score.py --models-gcs). Materialises the artifacts from mlb_core.storage (GCS when a bucket is
    configured) into a local dir for StatModel.load. A missing booster/meta raises: the scorer must refuse, not guess."""
    from mlb_core import storage
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for stat in stats or C.STATS:
        names = C.artifact_names(stat)
        for kind in ("booster", "meta", "var"):
            key = f"{prefix}/{names[kind]}"
            if kind == "var" and not storage.exists(key):
                continue                                          # the spread booster is optional (binned fallback)
            (dest / names[kind]).write_bytes(storage.read_bytes(key))
    return dest


def main(argv=None) -> int:
    from nba.props import data as D
    from nba.props.features import FeatureContext, build_training_frame
    ap = argparse.ArgumentParser(description="Train the NBA props models (local artifacts by default).")
    ap.add_argument("--local-dir", help="dir with player_boxscores_master.csv, games_master.csv, raw/schedule_*.json "
                                        "(default: read via mlb_core.storage)")
    ap.add_argument("--out-dir", default=C.DEFAULT_ARTIFACT_DIR)
    ap.add_argument("--stats", nargs="*", default=C.STATS)
    ap.add_argument("--last-season", type=int, default=None, help="validation season (default: newest in the data)")
    ap.add_argument("--upload", action="store_true", help="also upload artifacts to GCS (explicit; off by default)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pb, gm = D.load_masters(a.local_dir)
    rows, tg = D.prepare(pb, gm, D.gather_tips(gm, a.local_dir))
    X, meta = build_training_frame(rows, tg)
    X = X.reset_index(drop=True)
    meta = meta.reset_index(drop=True)
    meta["season_type"] = meta["season_type"].astype(str)
    log.info("training frame %s; history through %s", X.shape, D.history_status(rows))
    written = train_final(X, meta, a.out_dir, a.stats, a.last_season)
    if a.upload:
        log.info("uploaded: %s", upload_artifacts(a.out_dir))
    print(json.dumps(written, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
