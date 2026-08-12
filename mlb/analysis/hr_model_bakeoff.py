"""
mlb.analysis.hr_model_bakeoff -- try several HR model families, backtest each
through the SAME gated edge-bucket/CLV engine, and rank by low-edge CLV.

Motivation (2026-07-23 profit review): HR live ROI -18%, model AUC 0.620 vs
MARKET AUC 0.683, brier_skill NEGATIVE (worse than base rate). Calibration is
already applied in production, so the gap is the MODEL, not the calibrator. This
asks the honest question: does ANY off-the-shelf model family beat the current
xgb_hr_v6 on the only metric that matters -- CLV on the low-edge, well-calibrated
bets (edge <= 6%), where the live book actually shows positive CLV?

Method (leakage-proof, mirrors mlb.analysis.walkforward):
  1. Load the HR feature table + dynamic feature set via walkforward._prepare.
  2. Split train (game_date < cutoff) / holdout (>= cutoff). No holdout outcome
     is ever seen in training.
  3. Train each candidate on the train slice; score the holdout.
  4. Feed each candidate's out-of-sample probabilities into
     backtest_market.backtest(preds=...) with the clean-market gates
     (--min-books / --max-spread), which joins real odds_history lines,
     line-shops, settles, and computes per-bet ROI + CLV.
  5. Print a scorecard: OOS AUC/Brier + overall & LOW-EDGE ROI/CLV per candidate.

CLV is the go/no-go (walkforward RULE). ROI on best-shopped lines flatters every
model; a candidate only "wins" if it clears the codified rubric
(backtest_market.verdict: significant low-edge CLV AND a monotonic edge ladder --
see docs/solutions/logic-errors/backtest-roi-vs-clv-soft-line-artifact.md).

--tune adds "xgb_optuna", a REAL per-system walk-forward-safe Optuna search
(mlb.analysis.bakeoff_tuning) -- unlike "xgb_tuned", a fixed hardcoded guess kept
around only as a cheap sanity check. --persist writes every run durably (candidates,
scorecard, tuned params, run metadata) via mlb.analysis.bakeoff_persist -- nothing
persists without it (otherwise print-only, same as before).

Run (Cloud Shell; same env as gen_preds/walkforward):
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  pip install optuna --break-system-packages   # once, only needed for --tune
  PYTHONPATH=. python3 -m mlb.analysis.hr_model_bakeoff --cutoff 2026-06-01 \
      --min-books 4 --max-spread 0.10
  # add --calibrate to fit a leakage-clean isotonic layer on each candidate
  # add --models xgb_prod,logistic,hist_gbm to run a subset
  # real tuning + gated + persisted:
  PYTHONPATH=. python3 -m mlb.analysis.hr_model_bakeoff --cutoff 2026-06-01 \
      --min-books 4 --max-spread 0.10 --tune --tune-trials 30 --persist
"""
from __future__ import annotations

import argparse
import logging
import warnings

import numpy as np
import pandas as pd

from mlb.analysis import walkforward as wf
from mlb.analysis import backtest_market as bt
from mlb.analysis import bakeoff_tuning
from mlb.analysis import bakeoff_persist

warnings.filterwarnings("ignore")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("hr_model_bakeoff")

# Market AUC on live HR bets (model-health, 2026-07-22) -- the bar to beat.
MARKET_AUC_REF = 0.683

# "xgb_tuned" is a fixed hardcoded preset (cheap sanity check, kept as-is). "xgb_optuna"
# -- the real per-system search (mlb.analysis.bakeoff_tuning) -- is deliberately NOT in
# this default list: it needs tuned params resolved first (see --tune in main()), so a
# plain run with no flags must behave exactly as before. --tune adds it dynamically.
ALL_MODELS = ["xgb_prod", "xhr_poisson", "xgb_reg", "xgb_tuned", "logistic", "hist_gbm", "random_forest"]


# ── model trainers: each returns holdout probabilities (np.ndarray) ────────────

def _means(tr: pd.DataFrame, feats: list) -> dict:
    return {f: float(pd.to_numeric(tr[f], errors="coerce").mean()) for f in feats}


def _xgb_predict(tr, ho, feats, c_variant):
    """Train an XGB variant via the production training path (val-tail early stop +
    scale_pos_weight for rare positives + full retrain), then score holdout."""
    import xgboost as xgb
    booster, best = wf._train_pre_cutoff(tr, feats, c_variant)
    means = _means(tr, feats)
    Xh = ho[feats].apply(pd.to_numeric, errors="coerce")
    for f in feats:
        Xh[f] = Xh[f].fillna(means[f])
    return booster.predict(xgb.DMatrix(Xh.astype(float), feature_names=feats),
                           iteration_range=(0, best))


def _sk_predict(tr, ho, feats, build_estimator):
    """Median-impute + (optional) scale, fit an sklearn estimator, return P(HR)."""
    from sklearn.impute import SimpleImputer
    Xtr = tr[feats].apply(pd.to_numeric, errors="coerce")
    Xho = ho[feats].apply(pd.to_numeric, errors="coerce")
    imp = SimpleImputer(strategy="median")
    Xtr_i = imp.fit_transform(Xtr)
    Xho_i = imp.transform(Xho)
    ytr = tr["hr"].astype(int).values
    est, needs_scale = build_estimator()
    if needs_scale:
        from sklearn.preprocessing import StandardScaler
        sc = StandardScaler()
        Xtr_i, Xho_i = sc.fit_transform(Xtr_i), sc.transform(Xho_i)
    est.fit(Xtr_i, ytr)
    return est.predict_proba(Xho_i)[:, 1]


def _xhr_poisson_predict(tr, ho, feats, c) -> np.ndarray:
    """Lever C: train count:poisson on the DE-NOISED xHR target (game_xhr), predict
    expected-HR lambda, return P(HR>=1) = 1 - exp(-lambda). Requires the game_xhr
    column (rebuild HR features first). Feeds the binary backtest path."""
    import xgboost as xgb
    if "game_xhr" not in tr.columns:
        raise RuntimeError("game_xhr column absent -- rebuild HR features (build_xhr_target)")
    X = tr[feats].apply(pd.to_numeric, errors="coerce")
    y = pd.to_numeric(tr["game_xhr"], errors="coerce").fillna(0.0).clip(lower=0.0)
    params = {k: v for k, v in c["params"].items() if k != "scale_pos_weight"}
    params.update(objective="count:poisson", eval_metric="rmse")
    nval = int(len(X) * 7 / 8)
    dtr = xgb.DMatrix(X.iloc[:nval], label=y.iloc[:nval], feature_names=feats)
    dval = xgb.DMatrix(X.iloc[nval:], label=y.iloc[nval:], feature_names=feats)
    b = xgb.train(params, dtr, num_boost_round=c["n_round"],
                  evals=[(dval, "v")], early_stopping_rounds=c["early"], verbose_eval=False)
    best = int(getattr(b, "best_iteration", c["n_round"] - 1)) + 1
    booster = xgb.train(params, xgb.DMatrix(X, label=y, feature_names=feats),
                        num_boost_round=best, verbose_eval=False)
    means = _means(tr, feats)
    Xh = ho[feats].apply(pd.to_numeric, errors="coerce")
    for f in feats:
        Xh[f] = Xh[f].fillna(means[f])
    lam = booster.predict(xgb.DMatrix(Xh.astype(float), feature_names=feats),
                          iteration_range=(0, best))
    return np.clip(1.0 - np.exp(-np.clip(lam, 0.0, None)), 0.001, 0.999)


def _predict(model: str, tr, ho, feats, c) -> np.ndarray:
    if model == "xgb_prod":
        return _xgb_predict(tr, ho, feats, c)
    if model == "xhr_poisson":
        return _xhr_poisson_predict(tr, ho, feats, c)
    if model == "xgb_reg":
        p = {**c["params"], "max_depth": 3, "min_child_weight": 20,
             "reg_lambda": 3.0, "subsample": 0.8, "colsample_bytree": 0.8}
        return _xgb_predict(tr, ho, feats, {**c, "params": p})
    if model == "xgb_tuned":
        p = {**c["params"], "max_depth": 5, "learning_rate": 0.05,
             "min_child_weight": 10, "reg_lambda": 2.0, "reg_alpha": 0.5,
             "subsample": 0.9, "colsample_bytree": 0.9}
        return _xgb_predict(tr, ho, feats, {**c, "params": p})
    if model == "xgb_optuna":
        # real per-system search (bakeoff_tuning.tune_system_walkforward) -- run()
        # resolves this ONCE before the model loop and stashes it on `c`.
        tuned = c.get("tuned_params")
        if not tuned:
            raise RuntimeError("xgb_optuna requested but no tuned params on c -- "
                               "run() should have resolved this before the model loop")
        return _xgb_predict(tr, ho, feats, {**c, "params": tuned})
    if model == "logistic":
        from sklearn.linear_model import LogisticRegression
        return _sk_predict(tr, ho, feats,
                           lambda: (LogisticRegression(max_iter=2000, C=0.5,
                                                       class_weight="balanced"), True))
    if model == "hist_gbm":
        from sklearn.ensemble import HistGradientBoostingClassifier

        def _build():
            try:
                return HistGradientBoostingClassifier(
                    max_depth=4, learning_rate=0.05, max_iter=400,
                    l2_regularization=1.0, class_weight="balanced"), False
            except TypeError:  # class_weight added in sklearn 1.3
                return HistGradientBoostingClassifier(
                    max_depth=4, learning_rate=0.05, max_iter=400,
                    l2_regularization=1.0), False
        # HGB handles NaN natively, but _sk_predict imputes -- harmless.
        return _sk_predict(tr, ho, feats, _build)
    if model == "random_forest":
        from sklearn.ensemble import RandomForestClassifier
        return _sk_predict(tr, ho, feats,
                           lambda: (RandomForestClassifier(
                               n_estimators=400, max_depth=None, min_samples_leaf=20,
                               class_weight="balanced_subsample", n_jobs=-1,
                               random_state=0), False))
    raise ValueError(f"unknown model {model!r}")


def _calibrate(model: str, tr, ho, feats, c):
    """Leakage-clean isotonic: fit on the last 1/8 of the train slice (model trained
    on the first 7/8), then apply to holdout. Returns holdout probabilities."""
    from sklearn.isotonic import IsotonicRegression
    tr = tr.sort_values("game_date")
    nfit = int(len(tr) * 7 / 8)
    tr_fit, tr_cal = tr.iloc[:nfit], tr.iloc[nfit:]
    if len(tr_cal) < 50:
        return _predict(model, tr, ho, feats, c)  # too thin to calibrate
    cal_prob = _predict(model, tr_fit, tr_cal, feats, c)
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(cal_prob, tr_cal["hr"].astype(int).values)
    raw_ho = _predict(model, tr_fit, ho, feats, c)  # same tr_fit model for consistency
    return iso.predict(raw_ho)


# ── preds frame in gen_preds schema (so backtest_market.backtest consumes it) ──

def _preds_frame(ho, spec, prob) -> pd.DataFrame:
    return pd.DataFrame({
        "system": "HR", "market": spec.market,
        "game_pk": pd.to_numeric(ho["game_pk"], errors="coerce").astype("Int64"),
        "game_date": pd.to_datetime(ho["game_date"], errors="coerce").dt.strftime("%Y-%m-%d").values,
        "player_id": pd.to_numeric(ho[spec.id_col], errors="coerce").astype("Int64"),
        "kind": "binary",
        "p_model": np.clip(prob, 0.001, 0.999),
        "mu": np.nan, "nb_alpha": np.nan,
        "realized": pd.to_numeric(ho[spec.label_col], errors="coerce").values,
    })


def _scorecard_row(model, prob, ho, res):
    from sklearn.metrics import roc_auc_score, brier_score_loss
    y = pd.to_numeric(ho["hr"], errors="coerce").values
    row = {"model": model, "auc": np.nan, "brier": np.nan, "brier_skill": np.nan,
           "n_bets": 0, "roi_best%": np.nan, "roi_cons%": np.nan, "clv%": np.nan,
           "clv_n": 0, "lo_n": 0, "lo_roi%": np.nan, "lo_clv%": np.nan,
           "verdict": "INSUFFICIENT_N", "verdict_reason": "no candidates",
           "clv_tstat": np.nan, "ladder_monotonic": None}
    try:
        row["auc"] = round(float(roc_auc_score(y, prob)), 4)
        base = float(np.mean(y))
        b = float(brier_score_loss(y, prob))
        b_base = base * (1 - base)
        row["brier"] = round(b, 4)
        row["brier_skill"] = round(1 - b / b_base, 4) if b_base else np.nan
    except Exception:  # noqa: BLE001
        pass
    cand = res.get("candidates") if isinstance(res, dict) else None
    if cand is not None and len(cand):
        row["n_bets"] = int(len(cand))
        row["roi_best%"] = round(cand["roi"].mean() * 100, 2)
        row["roi_cons%"] = round(cand["roi_cons"].mean() * 100, 2)
        clv = cand["clv_pct"].dropna()
        row["clv_n"] = int(len(clv))
        row["clv%"] = round(clv.mean(), 2) if len(clv) else np.nan
        lo = cand[cand["edge"] <= bt.LOW_EDGE_MAX]
        row["lo_n"] = int(len(lo))
        if len(lo):
            row["lo_roi%"] = round(lo["roi"].mean() * 100, 2)
            locl = lo["clv_pct"].dropna()
            row["lo_clv%"] = round(locl.mean(), 2) if len(locl) else np.nan
        # LEVER A: split YES (homered = OVER/YES) vs NO (UNDER/NO) -- is the NO
        # favorite side the whole loss? (83% of live HR bets are NO.)
        sel = cand["selection"].str.upper()
        for tag, mask in (("yes", sel.isin(["OVER", "YES"])), ("no", sel.isin(["UNDER", "NO"]))):
            g = cand[mask]
            row[f"{tag}_n"] = int(len(g))
            if len(g):
                row[f"{tag}_roi%"] = round(g["roi"].mean() * 100, 2)
                gc = g["clv_pct"].dropna()
                row[f"{tag}_clv%"] = round(gc.mean(), 2) if len(gc) else np.nan
        # codified go/no-go (docs/solutions/logic-errors/backtest-roi-vs-clv-soft-line-artifact.md)
        v = bt.verdict(cand)
        row["verdict"] = v["verdict"]
        row["verdict_reason"] = v["reason"]
        row["clv_tstat"] = v["clv_tstat"]
        row["ladder_monotonic"] = v["ladder_monotonic"]
    return row


def run(cutoff: str, until: str | None, models: list, min_books: int,
        max_spread: float, calibrate: bool, tune_trials: int = 30, tune_folds: int = 3,
        load_tuned_from: str | None = None, persist_prefix: str | None = None) -> pd.DataFrame:
    spec, c, df, feats = wf._prepare("HR", quiet=False)
    tr = df[df["game_date"] < cutoff]
    ho = df[df["game_date"] >= cutoff]
    if until:
        ho = ho[ho["game_date"] < until]
    if len(tr) < 200 or len(ho) < 10:
        raise RuntimeError(f"bad split: train={len(tr)} holdout={len(ho)} at {cutoff}")
    logger.info(f"HR bake-off | train {len(tr):,} (<{cutoff}) -> holdout {len(ho):,} "
               f"[{cutoff}, {until or 'end'})  | {len(feats)} features | "
               f"HR base rate {tr['hr'].mean():.3f} | calibrate={calibrate}")
    logger.info(f"gates: min_books={min_books} max_spread={max_spread}  "
               f"(market AUC bar = {MARKET_AUC_REF})")

    if "xgb_optuna" in models:
        loaded = bakeoff_persist.load_tuning(load_tuned_from, "HR") if load_tuned_from else None
        if loaded:
            tuned_params, tune_meta = loaded
            logger.info(f"[HR][tune] loaded prior tuned params from {load_tuned_from} "
                       f"(status={tune_meta.get('status')})")
        else:
            if load_tuned_from:
                logger.warning(f"[HR][tune] no tuned params at {load_tuned_from} "
                              f"-- searching fresh instead")
            tuned_params, tune_meta = bakeoff_tuning.tune_system_walkforward(
                "HR", cutoff, n_trials=tune_trials, n_folds=tune_folds, prep=(spec, c, df, feats))
        c = {**c, "tuned_params": tuned_params}
        if persist_prefix:
            bakeoff_persist.write_tuning(persist_prefix, "HR", tuned_params, tune_meta)

    rows, best_cand, best_key = [], None, (-1e9, None)
    for m in models:
        try:
            prob = _calibrate(m, tr, ho, feats, c) if calibrate else _predict(m, tr, ho, feats, c)
            preds = _preds_frame(ho, spec, prob)
            res = bt.backtest("HR", since=cutoff, until=until, preds=preds,
                              min_books=min_books, max_spread=max_spread, select="best")
            row = _scorecard_row(m, prob, ho, res)
            rows.append(row)
            # track the best candidate by low-edge CLV (fallback: overall CLV) for the drill-down
            score = row["lo_clv%"] if not pd.isna(row["lo_clv%"]) else (
                row["clv%"] if not pd.isna(row["clv%"]) else -1e9)
            if isinstance(res, dict) and res.get("candidates") is not None and score > best_key[0]:
                best_key = (score, m)
                best_cand = res
            logger.info(f"[HR][{m}] done AUC={row['auc']} bets={row['n_bets']} "
                       f"lo_clv={row['lo_clv%']}% (n={row['lo_n']}) verdict={row['verdict']}")
            if persist_prefix and isinstance(res, dict) and res.get("candidates") is not None:
                bakeoff_persist.write_candidates(persist_prefix, "HR", m, res["candidates"])
        except Exception as e:  # noqa: BLE001
            logger.error(f"[HR][{m}] FAILED: {type(e).__name__}: {e}")
            rows.append({"model": m, "auc": np.nan})

    board = pd.DataFrame(rows).set_index("model")
    print("\n=== HR MODEL SCORECARD (out-of-sample, gated) ===")
    print("  brier_skill>0 = beats base rate. lo_* = edge<=6% (the +CLV zone). "
          "verdict = codified go/no-go (backtest_market.verdict).")
    with pd.option_context("display.width", 220, "display.max_columns", 30):
        print(board.to_string(float_format=lambda x: f"{x:,.3f}"))

    if best_cand is not None:
        print(f"\n=== edge-bucket drill-down: {best_key[1]} "
              f"(best low-edge CLV) ===")
        bt._print_report(best_cand, f"OOS >= {cutoff}", best_cand["candidates"])
    n_promoted = int((board.get("verdict") == "PROMOTE_CANDIDATE").sum()) if "verdict" in board else 0
    print(f"\n  {n_promoted} of {len(board)} candidates cleared PROMOTE_CANDIDATE "
         f"(low-edge CLV >=+2% at t>2, monotonic edge ladder, n>={bt.BAKEOFF_MIN_N}).")
    if not n_promoted:
        print("  No candidate cleared the bar -- consistent with the 2026-06-30 all-system "
             "sweep finding no capturable model-vs-line edge; means the gap is "
             "market/calibration-side, not model fit. Best-line ROI alone flatters everyone.")
    return board


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="HR model bake-off: train families, gated OOS backtest")
    p.add_argument("--cutoff", default="2026-06-01", help="train<cutoff, score>=cutoff")
    p.add_argument("--until", default=None, help="optional holdout end (YYYY-MM-DD)")
    p.add_argument("--models", default=",".join(ALL_MODELS),
                   help=f"comma list from: {','.join(ALL_MODELS)}")
    p.add_argument("--min-books", type=int, default=4, help="clean-market gate")
    p.add_argument("--max-spread", type=float, default=0.10, help="clean-market gate")
    p.add_argument("--calibrate", action="store_true",
                   help="fit a leakage-clean isotonic layer per candidate")
    p.add_argument("--tune", action="store_true",
                   help="add xgb_optuna (real walk-forward-safe Optuna search)")
    p.add_argument("--tune-trials", type=int, default=30, help="Optuna trials")
    p.add_argument("--tune-folds", type=int, default=3, help="inner CV month-folds for tuning")
    p.add_argument("--load-tuned-from", default=None,
                   help="reuse a prior --persist run's tuned params instead of re-searching")
    p.add_argument("--persist", action="store_true", help="write results to GCS (see bakeoff_persist)")
    p.add_argument("--run-root", default=bakeoff_persist.DEFAULT_RUN_ROOT,
                   help=f"GCS prefix root for --persist (default {bakeoff_persist.DEFAULT_RUN_ROOT})")
    p.add_argument("--resume", default=None, metavar="RUN_ID",
                   help="resume a prior --persist run (implies --persist): if HR is already "
                        "in its systems_completed, no-op; otherwise restore its cutoff/until/"
                        "gates/tune settings and run -- you only need --resume RUN_ID, nothing "
                        "else. RUN_ID is the value printed after 'persisted -> "
                        "Analysis/bakeoff/runs/' by the original run.")
    args = p.parse_args(argv)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if "xgb_optuna" in models and not args.tune:
        p.error("--models includes xgb_optuna but --tune was not set (no params source)")

    persist_prefix, run_meta = None, None
    if args.resume:
        persist_prefix = bakeoff_persist.run_prefix(args.resume, args.run_root)
        try:
            run_meta = bakeoff_persist.read_run_meta(persist_prefix)
        except Exception as e:  # noqa: BLE001
            p.error(f"--resume {args.resume}: could not read run_meta.json at "
                   f"{persist_prefix} ({type(e).__name__}: {e})")
        if "HR" in (run_meta.get("systems_completed") or []):
            logger.info(f"[HR] already completed in {persist_prefix} -- nothing to do.")
            return 0
        # keep every gate/tuning param identical to the original run.
        args.cutoff = run_meta.get("cutoff", args.cutoff)
        args.until = run_meta.get("until", args.until)
        args.min_books = run_meta.get("min_books", args.min_books)
        args.max_spread = run_meta.get("max_spread", args.max_spread)
        args.calibrate = run_meta.get("calibrate", args.calibrate)
        args.tune = run_meta.get("tune", args.tune)
        args.tune_trials = run_meta.get("tune_trials", args.tune_trials)
        args.tune_folds = run_meta.get("tune_folds", args.tune_folds)
        args.load_tuned_from = run_meta.get("load_tuned_from", args.load_tuned_from)
        logger.info(f"[resume] {persist_prefix} -- HR not yet completed; running "
                   f"cutoff={args.cutoff} tune={args.tune}(trials={args.tune_trials}) "
                   f"min_books={args.min_books} max_spread={args.max_spread}")
    elif args.persist:
        run_id = bakeoff_persist.make_run_id(args.cutoff)
        persist_prefix = bakeoff_persist.run_prefix(run_id, args.run_root)
        run_meta = bakeoff_persist.new_run_meta(
            run_id, persist_prefix, args.cutoff, args.until, ["HR"],
            tune=args.tune, tune_trials=args.tune_trials, tune_folds=args.tune_folds,
            min_books=args.min_books, max_spread=args.max_spread, calibrate=args.calibrate,
            load_tuned_from=args.load_tuned_from)
        bakeoff_persist.write_run_meta(persist_prefix, run_meta)
    if persist_prefix:
        logger.info(f"persisting to {persist_prefix}")

    # append AFTER --resume may have flipped args.tune from run_meta -- a resumed
    # invocation that only passes --resume (no --tune of its own) must still get
    # xgb_optuna if the original run had --tune set.
    if args.tune and "xgb_optuna" not in models:
        models.append("xgb_optuna")

    board = run(args.cutoff, args.until, models, args.min_books, args.max_spread, args.calibrate,
               tune_trials=args.tune_trials, tune_folds=args.tune_folds,
               load_tuned_from=args.load_tuned_from, persist_prefix=persist_prefix)

    if persist_prefix:
        # normalize to model_bakeoff.py's unsuffixed column names (clv/lo_clv/roi_best, not
        # clv%/lo_clv%/roi_best%) so a persisted scorecard has ONE schema regardless of which
        # script produced it -- bakeoff_report.py (and anyone else) shouldn't have to special-case
        # HR's historical %-suffixed display convention, which stays unchanged on-screen above.
        rename = {"clv%": "clv", "lo_clv%": "lo_clv", "roi_best%": "roi_best",
                  "roi_cons%": "roi_cons", "lo_roi%": "lo_roi",
                  "yes_roi%": "yes_roi", "yes_clv%": "yes_clv",
                  "no_roi%": "no_roi", "no_clv%": "no_clv"}
        scorecard = board.reset_index().assign(system="HR").rename(columns=rename)
        bakeoff_persist.write_scorecard(persist_prefix, scorecard)
        run_meta = bakeoff_persist.mark_system_complete(persist_prefix, run_meta, "HR")
        bakeoff_persist.finish_run_meta(persist_prefix, run_meta, status="complete")
        print(f"\n  persisted -> {persist_prefix}  (scorecard.csv, candidates/, tuning/, run_meta.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
