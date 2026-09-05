"""
mlb.analysis._bakeoff_common -- shared training/calibration/CLI scaffold for
mlb.analysis.model_bakeoff (all trainable systems: K, OUTS, BATTER_HITS,
BATTER_TB, SB, HR, GAME) and mlb.analysis.hr_model_bakeoff (HR only, plus its
own HR-specific model candidate _xhr_poisson_predict and yes/no
selection-split scorecard breakdown).

Extracted 2026-09-04: hr_model_bakeoff.py used to re-implement almost this
entire scaffold (model trainers, calibration, tuned-param resolution,
--resume/--persist run_meta wiring) with only cosmetic renames from
model_bakeoff.py. This module holds the byte-for-byte-duplicated pieces so
both scripts import one copy instead of two. It has no CLI of its own and is
never run directly.

Where the two callers' ORIGINAL code differed in actual behavior (not just
naming) -- e.g. whether train-set predictions are also computed, which label
column/coercion is used for calibration fitting, or how many result fields are
tracked -- the functions below take an explicit parameter so each caller can
reproduce its own original output exactly. See the NOTE comments on `_xgb`,
`_sk_fit_predict`, and `_calibrate_core`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from mlb.analysis import walkforward as wf
from mlb.analysis import backtest_market as bt
from mlb.analysis import bakeoff_tuning
from mlb.analysis import bakeoff_persist


# ── model-trainer helpers ("scaffold") ─────────────────────────────────────────

def _means(tr, feats):
    return {f: float(pd.to_numeric(tr[f], errors="coerce").mean()) for f in feats}


def _xgb(tr, ho, feats, c_variant, predict_train=True):
    """Train an XGB variant via the production path; predict ho (and, if
    predict_train, tr too). Works for count:poisson and binary:logistic alike
    (objective from c.params).

    NOTE: predict_train reproduces a real difference between the two original
    callers, not a cosmetic one -- model_bakeoff.py's count-kind models need the
    train-set predictions to fit an NB dispersion alpha (_nb_alpha), so it always
    wants both (predict_train=True, the default, matching its original `_xgb`).
    hr_model_bakeoff.py is binary-only and never fits an NB alpha, so it only ever
    needs the holdout predictions (predict_train=False, matching its original
    `_xgb_predict`, which never scored the train slice at all).
    """
    import xgboost as xgb
    booster, best = wf._train_pre_cutoff(tr, feats, c_variant)
    means = _means(tr, feats)

    def _pred(frame):
        X = frame[feats].apply(pd.to_numeric, errors="coerce")
        for f in feats:
            X[f] = X[f].fillna(means[f])
        return booster.predict(xgb.DMatrix(X.astype(float), feature_names=feats),
                               iteration_range=(0, best))
    if not predict_train:
        return _pred(ho)
    return _pred(ho), _pred(tr)


def _sk_fit_predict(tr, ho, feats, y_tr, est, is_classifier, scale, predict_train=True):
    """Median-impute (+ optional scale), fit an sklearn estimator, predict ho (and,
    if predict_train, tr too). Returns proba[:,1] for classifiers, a >=0-clipped
    value for regressors.

    NOTE: this generalizes two original functions that differed in real ways, not
    just naming:
      - model_bakeoff.py's `_sk` supports classifiers AND regressors (is_classifier
        comes from its own `build()` callable) and always wants both tr+ho
        predictions (predict_train=True, the default, its original behavior)
        because count-kind results feed _nb_alpha.
      - hr_model_bakeoff.py's `_sk_predict` is classifier-only (callers always pass
        is_classifier=True) and only ever wants the holdout prediction
        (predict_train=False, matching its original behavior, which never scored
        the train slice).
    Both callers pass `y_tr` already coerced their own way (model_bakeoff:
    tr["__y__"].values; hr_model_bakeoff: tr["hr"].astype(int).values) -- this
    function does no label coercion of its own so that quirk is preserved exactly.
    """
    from sklearn.impute import SimpleImputer
    Xtr = tr[feats].apply(pd.to_numeric, errors="coerce")
    Xho = ho[feats].apply(pd.to_numeric, errors="coerce")
    imp = SimpleImputer(strategy="median")
    Xtr_i, Xho_i = imp.fit_transform(Xtr), imp.transform(Xho)
    if scale:
        from sklearn.preprocessing import StandardScaler
        sc = StandardScaler()
        Xtr_i, Xho_i = sc.fit_transform(Xtr_i), sc.transform(Xho_i)
    est.fit(Xtr_i, y_tr)
    if is_classifier:
        if not predict_train:
            return est.predict_proba(Xho_i)[:, 1]
        return est.predict_proba(Xho_i)[:, 1], est.predict_proba(Xtr_i)[:, 1]
    if not predict_train:
        return np.clip(est.predict(Xho_i), 0.0, None)
    return np.clip(est.predict(Xho_i), 0.0, None), np.clip(est.predict(Xtr_i), 0.0, None)


def _calibrate_core(model, tr, ho, feats, c, predict_ho_fn, label_fn):
    """Leakage-clean isotonic: fit on the last 1/8 of the train slice (model trained
    on the first 7/8), then apply to holdout.

    predict_ho_fn(model, tr_slice, ho_slice, feats, c) -> holdout-only prediction
    array; each caller unwraps its own dispatcher's return shape (model_bakeoff's
    `_predict` always returns a (ho, tr) tuple, so its wrapper takes `[0]`;
    hr_model_bakeoff's `_predict` already returns a single array, so its wrapper
    passes it through as-is).
    label_fn(frame) -> true-label array for the isotonic fit; each caller supplies
    its own coercion (model_bakeoff: frame["__y__"].values; hr_model_bakeoff:
    frame["hr"].astype(int).values) -- this function does no coercion of its own.
    """
    from sklearn.isotonic import IsotonicRegression
    tr = tr.sort_values("game_date")
    nfit = int(len(tr) * 7 / 8)
    tr_fit, tr_cal = tr.iloc[:nfit], tr.iloc[nfit:]
    if len(tr_cal) < 50:
        return predict_ho_fn(model, tr, ho, feats, c)
    cal_prob = predict_ho_fn(model, tr_fit, tr_cal, feats, c)
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(cal_prob, label_fn(tr_cal))
    raw_ho = predict_ho_fn(model, tr_fit, ho, feats, c)
    return iso.predict(raw_ho)


def _resolve_tuned_params(system, cutoff, c, prep, tune_trials, tune_folds,
                          load_tuned_from, persist_prefix, logger):
    """Resolve xgb_optuna's tuned params for one system: reuse a prior --persist
    run's params (--load-tuned-from) if present, else run a fresh per-system
    walk-forward-safe Optuna search (bakeoff_tuning.tune_system_walkforward);
    persist the result if --persist is set. Returns c updated with tuned_params.

    Byte-for-byte-identical logic shared by model_bakeoff.run_system() (system
    varies: K/OUTS/.../HR/GAME) and hr_model_bakeoff.run() (system is always "HR").
    `logger` is the caller's own module logger (its name never appears in the log
    line text -- both scripts share the same "%(asctime)s %(levelname)s %(message)s"
    format -- but is passed through so log-record identity still matches the
    original call site exactly).
    """
    loaded = bakeoff_persist.load_tuning(load_tuned_from, system) if load_tuned_from else None
    if loaded:
        tuned_params, tune_meta = loaded
        logger.info(f"[{system}][tune] loaded prior tuned params from {load_tuned_from} "
                   f"(status={tune_meta.get('status')})")
    else:
        if load_tuned_from:
            logger.warning(f"[{system}][tune] no tuned params at {load_tuned_from} "
                          f"-- searching fresh instead")
        tuned_params, tune_meta = bakeoff_tuning.tune_system_walkforward(
            system, cutoff, n_trials=tune_trials, n_folds=tune_folds, prep=prep)
    c = {**c, "tuned_params": tuned_params}
    if persist_prefix:
        bakeoff_persist.write_tuning(persist_prefix, system, tuned_params, tune_meta)
    return c


# ── scorecard helper ────────────────────────────────────────────────────────────

def _candidate_stats(cand):
    """Core per-candidate stats + the codified verdict, shared by
    model_bakeoff._bet_stats and hr_model_bakeoff._scorecard_row: bet count,
    best/conservative ROI, overall and low-edge CLV, and backtest_market.verdict's
    go/no-go. Returns unsuffixed keys (n_bets, roi_best, roi_cons, clv, clv_n,
    lo_n, lo_roi, lo_clv, verdict, verdict_reason, clv_tstat, ladder_monotonic,
    hi_n, hi_clv) -- callers rename/suffix as needed (hr_model_bakeoff historically
    displays its ROI/CLV columns with a "%" suffix; model_bakeoff does not).
    `cand` must already be checked non-empty by the caller.
    """
    n_bets = int(len(cand))
    roi_best = round(cand["roi"].mean() * 100, 2)
    roi_cons = round(cand["roi_cons"].mean() * 100, 2) if "roi_cons" in cand.columns else np.nan
    clv = cand["clv_pct"].dropna()
    clv_n = int(len(clv))
    clv_mean = round(clv.mean(), 2) if len(clv) else np.nan
    lo = cand[cand["edge"] <= bt.LOW_EDGE_MAX]
    lo_n = int(len(lo))
    lo_roi = round(lo["roi"].mean() * 100, 2) if len(lo) else np.nan
    locl = lo["clv_pct"].dropna()
    lo_clv = round(locl.mean(), 2) if len(locl) else np.nan
    v = bt.verdict(cand)
    return dict(n_bets=n_bets, roi_best=roi_best, roi_cons=roi_cons, clv=clv_mean,
               clv_n=clv_n, lo_n=lo_n, lo_roi=lo_roi, lo_clv=lo_clv,
               verdict=v["verdict"], verdict_reason=v["reason"], clv_tstat=v["clv_tstat"],
               ladder_monotonic=v["ladder_monotonic"], hi_n=v["hi_n"], hi_clv=v["hi_clv"])


# ── CLI / --resume / --persist wiring ────────────────────────────────────────────

def _restore_args_from_run_meta(args, run_meta):
    """After --resume reads a prior run's run_meta.json, restore every gate/tuning
    param from it so a resumed run stays internally consistent with the run it's
    continuing -- never mix differently-tuned/gated systems into one scorecard.
    Mutates and returns args."""
    args.cutoff = run_meta.get("cutoff", args.cutoff)
    args.until = run_meta.get("until", args.until)
    args.min_books = run_meta.get("min_books", args.min_books)
    args.max_spread = run_meta.get("max_spread", args.max_spread)
    args.calibrate = run_meta.get("calibrate", args.calibrate)
    args.tune = run_meta.get("tune", args.tune)
    args.tune_trials = run_meta.get("tune_trials", args.tune_trials)
    args.tune_folds = run_meta.get("tune_folds", args.tune_folds)
    args.load_tuned_from = run_meta.get("load_tuned_from", args.load_tuned_from)
    return args


def _write_new_run_meta(run_id, persist_prefix, args, systems_list):
    """Build + write a fresh run_meta.json (bakeoff_persist.new_run_meta +
    write_run_meta) from parsed CLI args. Byte-for-byte-identical logic shared by
    both scripts' `elif args.persist:` branch (a brand-new run) and
    hr_model_bakeoff's --create-if-missing branch (a --resume id with nothing
    persisted yet, started fresh under that exact id -- finding B4.1)."""
    run_meta = bakeoff_persist.new_run_meta(
        run_id, persist_prefix, args.cutoff, args.until, systems_list,
        tune=args.tune, tune_trials=args.tune_trials, tune_folds=args.tune_folds,
        min_books=args.min_books, max_spread=args.max_spread, calibrate=args.calibrate,
        load_tuned_from=args.load_tuned_from)
    bakeoff_persist.write_run_meta(persist_prefix, run_meta)
    return run_meta
