"""
mlb.analysis.model_bakeoff -- all-system "optimization gap" scorecard.

Generalizes mlb.analysis.hr_model_bakeoff to every system with a trainable
single-booster contract (walkforward.WF_SYS): K, OUTS, BATTER_HITS, BATTER_TB
(count) and HR, GAME (binary). For each system it trains several model families
out-of-sample and pushes each through the SAME gated edge-bucket/CLV engine
(backtest_market.backtest(preds=...)), so you can see -- per system -- whether the
production model is actually optimized or just a notebook-era default.

Answers "are we optimized?" with numbers:
  * rank  = OOS AUC (binary) or Spearman rho (count) -- ranking quality.
  * lo_clv% = CLV on edge<=6% bets (the +CLV zone from live /edge-analysis);
              CLV is the go/no-go. Best-line ROI flatters everyone -- ignore it.
A system is UNDER-optimized only if a fresh family beats xgb_prod on BOTH rank
AND low-edge CLV with real n. If the winner already beats the market's rank and
still has no CLV, the fix is calibration/closing-line capture, not a new model.

NOT covered (no single-booster contract): NRFI (v18 ensemble), 1I/1IOU/F5/F1H
(proxy/derived), PITCHER_ER (Gamma proxy). Those need a model BUILT, not tuned.

Method: leakage-proof -- train on game_date < cutoff, score >= cutoff. Reuses
walkforward._prepare / _train_pre_cutoff verbatim so xgb_prod == production.

Run (Cloud Shell; same env as walkforward). Start UNGATED so no system is
starved (the HR lesson: tight gates left 7 bets); re-gate count systems after:
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  PYTHONPATH=. python3 -m mlb.analysis.model_bakeoff --cutoff 2026-06-01
  # subset / gated / calibrated:
  PYTHONPATH=. python3 -m mlb.analysis.model_bakeoff --systems K,BATTER_TB \
      --cutoff 2026-06-01 --min-books 4 --max-spread 0.10 --calibrate
"""
from __future__ import annotations

import argparse
import warnings

import numpy as np
import pandas as pd

from mlb.analysis import walkforward as wf
from mlb.analysis import backtest_market as bt

warnings.filterwarnings("ignore")

LOW_EDGE_MAX = 0.06
DEFAULT_SYSTEMS = list(wf.WF_SYS)  # K, OUTS, BATTER_HITS, BATTER_TB, HR, GAME
BINARY_MODELS = ["xgb_prod", "xgb_reg", "xgb_tuned", "logistic", "hist_gbm", "random_forest"]
COUNT_MODELS = ["xgb_prod", "xgb_reg", "xgb_tuned", "poisson_glm", "hist_gbm", "random_forest"]

# Live model-health AUC bars (2026-07-22) for context in the printout.
MARKET_AUC = {"K": 0.520, "OUTS": 0.546, "BATTER_HITS": 0.643, "BATTER_TB": 0.665,
              "HR": 0.683, "GAME": None}


# ── training / prediction (kind-aware) ─────────────────────────────────────────

def _means(tr, feats):
    return {f: float(pd.to_numeric(tr[f], errors="coerce").mean()) for f in feats}


def _xgb(tr, ho, feats, c_variant):
    """Train an XGB variant via the production path; predict tr AND ho.
    Works for count:poisson and binary:logistic alike (objective from c.params)."""
    import xgboost as xgb
    booster, best = wf._train_pre_cutoff(tr, feats, c_variant)
    means = _means(tr, feats)

    def _pred(frame):
        X = frame[feats].apply(pd.to_numeric, errors="coerce")
        for f in feats:
            X[f] = X[f].fillna(means[f])
        return booster.predict(xgb.DMatrix(X.astype(float), feature_names=feats),
                               iteration_range=(0, best))
    return _pred(ho), _pred(tr)


def _sk(tr, ho, feats, build, scale):
    """Median-impute (+ optional scale), fit an sklearn estimator, predict tr & ho.
    Returns proba[:,1] for classifiers, value for regressors."""
    from sklearn.impute import SimpleImputer
    Xtr = tr[feats].apply(pd.to_numeric, errors="coerce")
    Xho = ho[feats].apply(pd.to_numeric, errors="coerce")
    imp = SimpleImputer(strategy="median")
    Xtr_i, Xho_i = imp.fit_transform(Xtr), imp.transform(Xho)
    if scale:
        from sklearn.preprocessing import StandardScaler
        sc = StandardScaler()
        Xtr_i, Xho_i = sc.fit_transform(Xtr_i), sc.transform(Xho_i)
    est, is_clf = build()
    est.fit(Xtr_i, tr["__y__"].values)
    if is_clf:
        return est.predict_proba(Xho_i)[:, 1], est.predict_proba(Xtr_i)[:, 1]
    return np.clip(est.predict(Xho_i), 0.0, None), np.clip(est.predict(Xtr_i), 0.0, None)


def _predict(model, tr, ho, feats, c):
    """Return (pred_ho, pred_tr). Probability for binary, mean for count."""
    kind = c["kind"]
    if model == "xgb_prod":
        return _xgb(tr, ho, feats, c)
    if model == "xgb_reg":
        p = {**c["params"], "max_depth": 3, "min_child_weight": 20, "reg_lambda": 3.0,
             "subsample": 0.8, "colsample_bytree": 0.8}
        return _xgb(tr, ho, feats, {**c, "params": p})
    if model == "xgb_tuned":
        p = {**c["params"], "max_depth": 5, "learning_rate": 0.05, "min_child_weight": 10,
             "reg_lambda": 2.0, "reg_alpha": 0.5, "subsample": 0.9, "colsample_bytree": 0.9}
        return _xgb(tr, ho, feats, {**c, "params": p})
    if model == "logistic":
        from sklearn.linear_model import LogisticRegression
        return _sk(tr, ho, feats,
                   lambda: (LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced"), True), True)
    if model == "poisson_glm":
        from sklearn.linear_model import PoissonRegressor
        return _sk(tr, ho, feats,
                   lambda: (PoissonRegressor(alpha=1e-3, max_iter=500), False), True)
    if model == "hist_gbm":
        if kind == "binary":
            from sklearn.ensemble import HistGradientBoostingClassifier as H

            def _b():
                try:
                    return H(max_depth=4, learning_rate=0.05, max_iter=400,
                             l2_regularization=1.0, class_weight="balanced"), True
                except TypeError:
                    return H(max_depth=4, learning_rate=0.05, max_iter=400,
                             l2_regularization=1.0), True
            return _sk(tr, ho, feats, _b, False)
        from sklearn.ensemble import HistGradientBoostingRegressor as H
        return _sk(tr, ho, feats,
                   lambda: (H(loss="poisson", max_depth=4, learning_rate=0.05,
                              max_iter=400, l2_regularization=1.0), False), False)
    if model == "random_forest":
        if kind == "binary":
            from sklearn.ensemble import RandomForestClassifier as R
            return _sk(tr, ho, feats,
                       lambda: (R(n_estimators=200, min_samples_leaf=50,
                                  class_weight="balanced_subsample", n_jobs=-1,
                                  random_state=0), True), False)
        from sklearn.ensemble import RandomForestRegressor as R
        return _sk(tr, ho, feats,
                   lambda: (R(n_estimators=200, min_samples_leaf=50, n_jobs=-1,
                              random_state=0), False), False)
    raise ValueError(f"unknown model {model!r}")


def _calibrate_binary(model, tr, ho, feats, c):
    """Leakage-clean isotonic for binary: fit on last 1/8 of train (model trained on
    first 7/8), apply to holdout."""
    from sklearn.isotonic import IsotonicRegression
    tr = tr.sort_values("game_date")
    nfit = int(len(tr) * 7 / 8)
    tr_fit, tr_cal = tr.iloc[:nfit], tr.iloc[nfit:]
    if len(tr_cal) < 50:
        return _predict(model, tr, ho, feats, c)[0]
    cal_prob, _ = _predict(model, tr_fit, tr_cal, feats, c)
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(cal_prob, tr_cal["__y__"].values)
    raw_ho, _ = _predict(model, tr_fit, ho, feats, c)
    return iso.predict(raw_ho)


def _nb_alpha(mu_tr, y_tr):
    """var = mu + alpha*mu^2 -> alpha, clamped [0.01,0.50]. Matches walkforward."""
    mu = float(np.mean(mu_tr))
    var = float(np.var(y_tr - mu_tr))
    return float(np.clip((var - mu) / max(mu ** 2, 1e-6), 0.01, 0.50))


# ── preds frame + scoring ──────────────────────────────────────────────────────

def _preds_frame(system, spec, ho, target, kind, pred_ho, nb_alpha=np.nan):
    base = {
        "system": system, "market": spec.market,
        "game_pk": pd.to_numeric(ho["game_pk"], errors="coerce").astype("Int64"),
        "game_date": pd.to_datetime(ho["game_date"], errors="coerce").dt.strftime("%Y-%m-%d").values,
        "player_id": (pd.to_numeric(ho[spec.id_col], errors="coerce").astype("Int64")
                      if spec.id_col else pd.Series([pd.NA] * len(ho), dtype="Int64")),
        "realized": pd.to_numeric(ho[target], errors="coerce").values,
    }
    if kind == "binary":
        base.update(kind="binary", p_model=np.clip(pred_ho, 0.001, 0.999),
                    mu=np.nan, nb_alpha=np.nan)
    else:
        base.update(kind="count", p_model=np.nan,
                    mu=np.clip(pred_ho, 0.01, None), nb_alpha=nb_alpha)
    return pd.DataFrame(base)


def _rank_metric(kind, pred_ho, y):
    """AUC for binary, Spearman rho for count. Plus a secondary fit stat."""
    try:
        if kind == "binary":
            from sklearn.metrics import roc_auc_score, brier_score_loss
            auc = float(roc_auc_score(y, pred_ho))
            base = float(np.mean(y))
            b = float(brier_score_loss(y, pred_ho))
            bb = base * (1 - base)
            return round(auc, 4), (round(1 - b / bb, 3) if bb else np.nan)
        from scipy.stats import spearmanr
        rho = float(spearmanr(pred_ho, y).correlation)
        mae = float(np.mean(np.abs(pred_ho - y)))
        return round(rho, 4), round(mae, 3)
    except Exception:  # noqa: BLE001
        return np.nan, np.nan


def _bet_stats(res):
    row = dict(n_bets=0, roi_best=np.nan, clv=np.nan, clv_n=0, lo_n=0, lo_clv=np.nan)
    cand = res.get("candidates") if isinstance(res, dict) else None
    if cand is None or not len(cand):
        return row
    row["n_bets"] = int(len(cand))
    row["roi_best"] = round(cand["roi"].mean() * 100, 2)
    clv = cand["clv_pct"].dropna()
    row["clv_n"] = int(len(clv))
    row["clv"] = round(clv.mean(), 2) if len(clv) else np.nan
    lo = cand[cand["edge"] <= LOW_EDGE_MAX]
    row["lo_n"] = int(len(lo))
    locl = lo["clv_pct"].dropna()
    row["lo_clv"] = round(locl.mean(), 2) if len(locl) else np.nan
    return row


# ── driver ─────────────────────────────────────────────────────────────────────

def run_system(system, cutoff, until, models, min_books, max_spread, calibrate):
    spec, c, df, feats = wf._prepare(system, quiet=True)
    target, kind = c["target"], c["kind"]
    df = df.copy()
    df["__y__"] = pd.to_numeric(df[target], errors="coerce")  # sklearn label handle
    tr = df[df["game_date"] < cutoff].reset_index(drop=True)
    ho = df[df["game_date"] >= cutoff].reset_index(drop=True)
    if until:
        ho = ho[ho["game_date"] < until].reset_index(drop=True)
    if len(tr) < 200 or len(ho) < 10:
        print(f"  [{system}] SKIP -- train={len(tr)} holdout={len(ho)}")
        return []
    print(f"\n[{system}] kind={kind} train {len(tr):,} -> holdout {len(ho):,} "
          f"| {len(feats)} feats | target={target}"
          + (f" | base={tr['__y__'].mean():.3f}" if kind == "binary" else ""))
    y_ho = ho[target].astype(float).values
    rows = []
    for m in models:
        try:
            if calibrate and kind == "binary":
                pred_ho = _calibrate_binary(m, tr, ho, feats, c)
                mu_tr = None
            else:
                pred_ho, pred_tr = _predict(m, tr, ho, feats, c)
                mu_tr = pred_tr
            alpha = _nb_alpha(mu_tr, tr["__y__"].values) if (kind == "count" and mu_tr is not None) else np.nan
            preds = _preds_frame(system, spec, ho, target, kind, pred_ho, alpha)
            res = bt.backtest(system, since=cutoff, until=until, preds=preds,
                              min_books=min_books, max_spread=max_spread, select="best")
            rank, fit2 = _rank_metric(kind, pred_ho, y_ho)
            bs = _bet_stats(res)
            rows.append({"system": system, "model": m, "kind": kind,
                         "rank": rank, "fit2": fit2, **bs})
            print(f"    {m:<14} rank={rank}  bets={bs['n_bets']}  clv_n={bs['clv_n']}  "
                  f"lo_clv={bs['lo_clv']}% (n={bs['lo_n']})")
        except Exception as e:  # noqa: BLE001
            print(f"    {m:<14} FAILED: {type(e).__name__}: {e}")
            rows.append({"system": system, "model": m, "kind": kind, "rank": np.nan})
    return rows


def _verdict(board):
    """Per system: prod vs best alternative on rank and low-edge CLV."""
    print("\n=== OPTIMIZATION-GAP VERDICT (per system) ===")
    for sysname, g in board.groupby("system", sort=False):
        g = g.set_index("model")
        if "xgb_prod" not in g.index:
            continue
        prod = g.loc["xgb_prod"]
        alts = g.drop(index="xgb_prod")
        # best alt by low-edge CLV, else overall CLV, else rank
        key = "lo_clv" if alts["lo_clv"].notna().any() else ("clv" if alts["clv"].notna().any() else "rank")
        best = alts.sort_values(key, ascending=False).head(1)
        if not len(best):
            continue
        bm, br = best.index[0], best.iloc[0]
        mkt = MARKET_AUC.get(sysname)
        d_rank = (br["rank"] - prod["rank"]) if pd.notna(br["rank"]) and pd.notna(prod["rank"]) else np.nan
        rank_lbl = "AUC" if prod["kind"] == "binary" else "rho"
        # market bar is AUC -> only comparable to binary rank; count rank is Spearman.
        mkt_tag = ""
        if prod["kind"] == "binary" and mkt is not None and pd.notna(br["rank"]):
            mkt_tag = f"  [{'>' if br['rank'] > mkt else '<'}market {mkt}]"
        print(f"  {sysname:<12} prod {rank_lbl}={prod['rank']} lo_clv={prod['lo_clv']}%"
              f"  |  best alt={bm} {rank_lbl}={br['rank']} (drank {d_rank:+.3f}) "
              f"lo_clv={br['lo_clv']}%" + mkt_tag)
    print("\n  Under-optimized => an alt beats xgb_prod on rank AND low-edge CLV (real n).")
    print("  Beats market rank but no CLV => fix calibration/closing-lines, not the model.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="All-system optimization-gap scorecard")
    p.add_argument("--systems", default=",".join(DEFAULT_SYSTEMS),
                   help=f"comma list from: {','.join(DEFAULT_SYSTEMS)}")
    p.add_argument("--cutoff", default="2026-06-01")
    p.add_argument("--until", default=None)
    p.add_argument("--min-books", type=int, default=1, help="1 = ungated (avoid HR-style starvation)")
    p.add_argument("--max-spread", type=float, default=1.0, help="1.0 = ungated")
    p.add_argument("--calibrate", action="store_true", help="leakage-clean isotonic (binary only)")
    p.add_argument("--models", default=None, help="override model list (comma); default = kind-appropriate")
    args = p.parse_args(argv)

    systems = [s.strip().upper() for s in args.systems.split(",") if s.strip()]
    override = [m.strip() for m in args.models.split(",")] if args.models else None
    all_rows = []
    for s in systems:
        if s not in wf.WF_SYS:
            print(f"[{s}] SKIP -- no trainable contract (not in {list(wf.WF_SYS)})")
            continue
        kind = wf.WF_SYS[s][1]
        models = override or (BINARY_MODELS if kind == "binary" else COUNT_MODELS)
        all_rows += run_system(s, args.cutoff, args.until, models,
                               args.min_books, args.max_spread, args.calibrate)

    if not all_rows:
        print("no results")
        return 1
    board = pd.DataFrame(all_rows)
    print("\n=== FULL SCORECARD (OOS, gates: "
          f"min_books={args.min_books} max_spread={args.max_spread}, calibrate={args.calibrate}) ===")
    print("  rank = AUC(binary)/Spearman(count). lo_clv = CLV on edge<=6% bets = go/no-go.")
    show = board[["system", "model", "rank", "fit2", "n_bets", "clv_n", "clv", "lo_n", "lo_clv", "roi_best"]]
    with pd.option_context("display.width", 240, "display.max_columns", 30, "display.max_rows", 100):
        print(show.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    _verdict(board)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
