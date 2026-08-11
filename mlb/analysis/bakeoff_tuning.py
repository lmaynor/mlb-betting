"""
mlb.analysis.bakeoff_tuning -- real, walk-forward-safe per-system hyperparameter search
for the model bake-off's "xgb_optuna" candidate.

`mlb/training/tune_hyperparams.py` already has a working Optuna tuner, but two things
make it unsafe to plug into the bake-off as-is:
  1. Its `SYSTEM_CONFIG` dict duplicates `walkforward._resolve_contract`'s job (feature
     list / target / objective per system) -- a second, independently-maintained copy
     of the production contract (CONTEXT.md backlog E14). This module reuses
     `walkforward._resolve_contract` / `_prepare` directly instead, so there is exactly
     one source of truth for what a system trains on.
  2. Its inner CV folds by CALENDAR YEAR across the FULL dataset -- it does not respect
     an externally-imposed walk-forward cutoff. Handed a bake-off's pre-cutoff slice
     (which may span under 2 distinct years), that folding degenerates silently: it can
     return a constant sentinel for every trial with no error, so the "search" would
     just be whatever Optuna's sampler picks first. This module folds by CALENDAR MONTH,
     carved ONLY from `game_date < cutoff` rows -- the holdout never enters this module.

Reused as-is from tune_hyperparams.py: `SEARCH_SPACE` (the hyperparameter ranges) and
`_suggest_params` (mechanical trial -> params dict, no SYSTEM_CONFIG coupling) -- same
cross-module reuse pattern this whole `mlb/analysis/` family already uses for
`walkforward._prepare` / `_train_pre_cutoff`.

The search optimizes plain AUC (binary, maximize) / MAE (count, minimize) via inner CV --
deliberately NOT CLV/ROI. Running a full gated backtest per Optuna trial would be far more
expensive and would leak bet-level selection into hyperparameter choice. A system can score
better here and still be a bake-off NO_EDGE -- that's the correct outcome when the real
bottleneck is the market/data side, not model fit (see docs/solutions/logic-errors/
backtest-roi-vs-clv-soft-line-artifact.md).

Usage (called by model_bakeoff.py / hr_model_bakeoff.py; also runnable standalone):
    export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
    PYTHONPATH=. python3 -m mlb.analysis.bakeoff_tuning --system OUTS --cutoff 2026-06-01 \
        --n-trials 5 --n-folds 2
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from mlb.analysis import walkforward as wf
from mlb.training.tune_hyperparams import SEARCH_SPACE, FIXED_PARAMS, _suggest_params  # noqa: F401 (SEARCH_SPACE re-exported for callers)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bakeoff_tuning")


# ── per-fold train/score (single early-stopped pass -- NOT the two-pass production
#    trainer; a second full-refit per fold per trial would be wasted work in a search
#    loop whose model is discarded either way) ──────────────────────────────────────

def _fold_fit_score(params: dict, tr_inner: pd.DataFrame, val_inner: pd.DataFrame,
                    test_df: pd.DataFrame, feats: list, target: str, kind: str,
                    n_round: int, early: int) -> float | None:
    import xgboost as xgb

    X_tr = tr_inner[feats].apply(pd.to_numeric, errors="coerce")
    y_tr = tr_inner[target].astype(float)
    X_val = val_inner[feats].apply(pd.to_numeric, errors="coerce")
    y_val = val_inner[target].astype(float)
    X_te = test_df[feats].apply(pd.to_numeric, errors="coerce")
    y_te = test_df[target].astype(float)

    p = dict(params)
    if kind == "binary" and "scale_pos_weight" not in p:
        # matches walkforward._train_pre_cutoff exactly -- the search must see the same
        # rare-positive weighting the real candidate will train with (HR ~7% base rate).
        pos = float(y_tr.mean())
        if 0 < pos < 0.20:
            p["scale_pos_weight"] = round((1 - pos) / pos, 2)

    dtr = xgb.DMatrix(X_tr, label=y_tr, feature_names=feats)
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=feats)
    dte = xgb.DMatrix(X_te, label=y_te, feature_names=feats)
    try:
        b = xgb.train(p, dtr, num_boost_round=n_round, evals=[(dval, "val")],
                      early_stopping_rounds=early, verbose_eval=False)
    except Exception as e:  # noqa: BLE001 -- a bad hyperparameter draw must not kill the study
        logger.warning(f"[tune] fold fit failed: {type(e).__name__}: {e}")
        return None

    # CONTEXT.md iteration_range contract: never pass iteration_range=None.
    best = int(getattr(b, "best_iteration", n_round - 1)) + 1
    pred = b.predict(dte, iteration_range=(0, best))

    if kind == "binary":
        from sklearn.metrics import roc_auc_score
        try:
            return float(roc_auc_score(y_te, pred))
        except Exception:  # noqa: BLE001 -- e.g. single-class test fold
            return None
    return float(np.mean(np.abs(y_te.values - pred)))


def _usable_folds(months: pd.Series, n_folds: int) -> tuple[list, int]:
    """Distinct pre-cutoff calendar months + how many trailing ones are usable as test
    folds. Needs >=2 months of train history left after carving out the requested folds,
    or the earliest fold would train on almost nothing."""
    periods = sorted(months.unique())
    folds = min(n_folds, max(0, len(periods) - 2))
    return periods, folds


def _cv_score_walkforward(params: dict, tr_full: pd.DataFrame, months: pd.Series,
                          periods: list, folds: int, feats: list, c: dict) -> float | None:
    """Inner CV, folded by calendar month, carved ONLY from tr_full (already
    game_date < the outer walk-forward cutoff). Returns the mean fold metric, or None
    if every fold was too thin / failed to fit (never a fabricated sentinel score)."""
    target, kind, n_round, early = c["target"], c["kind"], c["n_round"], c["early"]
    scores = []
    for i in range(folds):
        test_period = periods[-(i + 1)]
        train_df = tr_full[months < test_period].reset_index(drop=True)
        test_df = tr_full[months == test_period].reset_index(drop=True)
        if len(train_df) < 100 or len(test_df) < 20:
            continue
        nval = max(20, len(train_df) // 10)
        if len(train_df) - nval < 50:
            continue
        tr_inner = train_df.iloc[:-nval].reset_index(drop=True)
        val_inner = train_df.iloc[-nval:].reset_index(drop=True)
        score = _fold_fit_score(params, tr_inner, val_inner, test_df, feats,
                                target, kind, n_round, early)
        if score is not None:
            scores.append(score)
    return float(np.mean(scores)) if scores else None


# ── driver ─────────────────────────────────────────────────────────────────────

def tune_system_walkforward(system: str, cutoff: str, n_trials: int = 30, n_folds: int = 3,
                            prep: tuple | None = None, seed: int = 42) -> tuple[dict, dict]:
    """Optuna-search XGB_PARAMS for `system`, using ONLY game_date < cutoff rows (the
    holdout the bake-off will score never enters this function). Returns
    (xgb_params, meta) -- xgb_params is ready to hand to walkforward._train_pre_cutoff
    as a `params` override (same shape/keys as the system's production XGB_PARAMS).

    Falls back to the system's unmodified production params (with a `status` flag in
    meta explaining why) rather than raising, on either "not enough pre-cutoff history
    for even 1 fold" or "every trial pruned" -- a single system's tuning trouble must
    never take down the other five systems' bake-off run.
    """
    import optuna

    _spec, c, df, feats = prep or wf._prepare(system, quiet=True)
    tr_full = df[df["game_date"] < cutoff].reset_index(drop=True)
    months = tr_full["game_date"].dt.to_period("M")
    periods, folds = _usable_folds(months, n_folds)

    if folds < 1:
        logger.warning(f"[{system}][tune] only {len(periods)} pre-cutoff month(s) before "
                       f"{cutoff} -- skipping search, using production params")
        return dict(c["params"]), {"status": "skipped_insufficient_history", "system": system,
                                   "cutoff": cutoff, "n_months": len(periods)}

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    direction = "maximize" if c["kind"] == "binary" else "minimize"

    def objective_fn(trial):
        trial_params = {**c["params"], **_suggest_params(trial)}
        score = _cv_score_walkforward(trial_params, tr_full, months, periods, folds, feats, c)
        if score is None:
            raise optuna.TrialPruned()
        return score

    def _log_trial(_study, trial):
        val = trial.value if trial.value is not None else float("nan")
        logger.info(f"[{system}][tune] trial {trial.number + 1}/{n_trials} score={val:.4f}")

    study = optuna.create_study(direction=direction, sampler=optuna.samplers.TPESampler(seed=seed))
    logger.info(f"[{system}][tune] {n_trials} trials x {folds} month-folds "
               f"({len(tr_full):,} pre-cutoff rows, <{cutoff})")
    study.optimize(objective_fn, n_trials=n_trials, show_progress_bar=False, callbacks=[_log_trial])

    completed = [t for t in study.trials if t.value is not None]
    if not completed:
        logger.warning(f"[{system}][tune] all {n_trials} trials pruned -- "
                       f"using production params")
        return dict(c["params"]), {"status": "all_trials_pruned", "system": system, "cutoff": cutoff}

    xgb_params = {**c["params"], **FIXED_PARAMS, **study.best_params}
    meta = {
        "status": "ok", "system": system, "cutoff": cutoff,
        "tuned_at": datetime.now(timezone.utc).isoformat(),
        "n_trials": n_trials, "n_trials_completed": len(completed),
        "n_folds": folds, "direction": direction,
        "best_score": round(float(study.best_value), 4),
        "best_params": dict(study.best_params),
    }
    logger.info(f"[{system}][tune] done: best_score={meta['best_score']} "
               f"({len(completed)}/{n_trials} trials completed) params={meta['best_params']}")
    return xgb_params, meta


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Walk-forward-safe Optuna tuning for one bake-off system")
    p.add_argument("--system", required=True, help=", ".join(wf.WF_SYS))
    p.add_argument("--cutoff", required=True, help="train<cutoff (holdout never touched)")
    p.add_argument("--n-trials", type=int, default=30)
    p.add_argument("--n-folds", type=int, default=3)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)

    params, meta = tune_system_walkforward(args.system, args.cutoff, args.n_trials,
                                           args.n_folds, seed=args.seed)
    print(f"\nstatus={meta.get('status')}")
    print(f"params={params}")
    print(f"meta={meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
