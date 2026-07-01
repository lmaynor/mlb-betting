"""
mlb.analysis.walkforward -- leakage-proof out-of-sample backtest.

The plain backtest_market scores with the PRODUCTION model, which was trained on
data spanning the backtest window -> the "holdout" is really in-sample, so ROI is
optimistic (the K result: +14% ROI but ~0/negative CLV -- the classic tell).

This trains a FRESH model on data strictly BEFORE a cutoff date, then scores only
the games ON/AFTER the cutoff. The model has never seen the holdout outcomes, so
the resulting ROI/CLV is honest. It reuses each system's production training
contract (XGB_PARAMS, feature list, target, best-iteration early-stopping, NB
dispersion fit) verbatim -- the only change is the train/score date split -- so
the walk-forward model is methodologically identical to production, minus leakage.

Artifacts are NEVER written to the production GCS keys; everything stays in memory.
No calibrator is applied (a production calibrator is fit on all data = leaky; a
clean walk-forward calibrator would be refit pre-cutoff -- a follow-up). Raw model
lambda is used, which is the honest OOS discrimination test.

Run (Cloud Shell; same env as gen_preds):
    export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
    PYTHONPATH=. python3 -m mlb.analysis.walkforward --system K --cutoff 2026-06-01
"""

from __future__ import annotations

import argparse
import importlib

import numpy as np
import pandas as pd

from mlb.analysis import gen_preds as gp
from mlb.analysis import backtest_market as bt

# system -> (retrain module, kind). The retrain module must expose the production
# contract constants used below. Count systems only (binary walk-forward = follow-up).
WF_SYS = {
    "K":           ("mlb.training.retrain_k_v1",           "count"),
    "OUTS":        ("mlb.training.retrain_outs_v1",        "count"),
    "BATTER_HITS": ("mlb.training.retrain_batter_hits_v1", "count"),
    "BATTER_TB":   ("mlb.training.retrain_batter_tb_v1",   "count"),
}


def _get(mod, *names, default=None):
    for n in names:
        if hasattr(mod, n):
            return getattr(mod, n)
    return default


def _resolve_contract(system: str):
    """Pull the production training contract from the system's retrain module."""
    mod_name, kind = WF_SYS[system]
    mod = importlib.import_module(mod_name)
    params = _get(mod, "XGB_PARAMS")
    target = _get(mod, "TARGET")
    feats = _get(mod, "K_FEATURES", "OUTS_FEATURES", "BATTER_HITS_FEATURES",
                 "BATTER_TB_FEATURES", "FEATURES")
    n_round = int(_get(mod, "NUM_BOOST_ROUND", default=2000))
    early = int(_get(mod, "EARLY_STOPPING_ROUNDS", default=50))
    if not (params and target and feats):
        raise RuntimeError(f"{mod_name} missing XGB_PARAMS/TARGET/FEATURES")
    return dict(params=params, target=target, feats=list(feats),
                n_round=n_round, early=early, kind=kind)


def _train_pre_cutoff(tr: pd.DataFrame, feats: list, c: dict):
    """Train exactly like production: carve a validation tail for early-stopping to
    find best_iteration, then full-retrain on ALL pre-cutoff rows with that many
    rounds. Returns (booster, best_iter)."""
    import xgboost as xgb
    X = tr[feats].apply(pd.to_numeric, errors="coerce")
    y = tr[c["target"]].astype(float)
    nval = int(len(X) * (7 / 8))
    dtr = xgb.DMatrix(X.iloc[:nval], label=y.iloc[:nval], feature_names=feats)
    dval = xgb.DMatrix(X.iloc[nval:], label=y.iloc[nval:], feature_names=feats)
    b = xgb.train(c["params"], dtr, num_boost_round=c["n_round"],
                  evals=[(dval, "val")], early_stopping_rounds=c["early"],
                  verbose_eval=False)
    best = int(getattr(b, "best_iteration", c["n_round"] - 1)) + 1
    # full retrain on 100% of pre-cutoff data (production Section 7b)
    dall = xgb.DMatrix(X, label=y, feature_names=feats)
    booster = xgb.train(c["params"], dall, num_boost_round=best, verbose_eval=False)
    booster.best_ntree_limit = best
    return booster, best


def _fit_nb_alpha(booster, tr, feats, target) -> float:
    """var = mu + alpha*mu^2 -> alpha = (var-mu)/mu^2, clamped [0.01,0.50]. Same as
    retrain_k_v1's NB dispersion fit, on pre-cutoff residuals."""
    import xgboost as xgb
    X = tr[feats].apply(pd.to_numeric, errors="coerce")
    pred = booster.predict(xgb.DMatrix(X, feature_names=feats))
    y = tr[target].astype(float).values
    mu = float(np.mean(pred))
    var = float(np.var(y - pred))
    return float(np.clip((var - mu) / max(mu ** 2, 1e-6), 0.01, 0.50))


def walkforward_preds(system: str, cutoff: str) -> pd.DataFrame:
    """Train on game_date < cutoff, score game_date >= cutoff. Returns a preds
    frame in gen_preds schema (kind=count: mu + nb_alpha), OUT OF SAMPLE."""
    import xgboost as xgb
    if system not in WF_SYS:
        raise ValueError(f"walkforward supports {list(WF_SYS)}; got {system!r}")
    spec = gp.SPECS[system]
    c = _resolve_contract(system)

    df = gp._read_csv(spec.feature_csv, low_memory=False)
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    df = df.dropna(subset=[c["target"], "game_date"]).sort_values("game_date").reset_index(drop=True)
    feats = [f for f in c["feats"] if f in df.columns]
    if len(feats) < len(c["feats"]):
        print(f"  note: {len(c['feats'])-len(feats)} contract features absent from CSV, using {len(feats)}")

    tr = df[df["game_date"] < cutoff]
    ho = df[df["game_date"] >= cutoff]
    if len(tr) < 200 or len(ho) < 10:
        raise RuntimeError(f"bad split: train={len(tr)} holdout={len(ho)} at cutoff {cutoff}")
    print(f"  walk-forward {system}: train {len(tr)} rows (<{cutoff}) "
          f"[{tr['game_date'].min().date()}..{tr['game_date'].max().date()}] "
          f"-> holdout {len(ho)} rows (>= {cutoff})")

    booster, best = _train_pre_cutoff(tr, feats, c)
    nb_alpha = _fit_nb_alpha(booster, tr, feats, c["target"])
    means = {f: float(pd.to_numeric(tr[f], errors="coerce").mean()) for f in feats}
    print(f"  trained best_iter={best} nb_alpha={nb_alpha:.4f}")

    Xh = ho[feats].apply(pd.to_numeric, errors="coerce")
    for f in feats:
        Xh[f] = Xh[f].fillna(means[f])
    mu = booster.predict(xgb.DMatrix(Xh.astype(float), feature_names=feats),
                         iteration_range=(0, best))

    return pd.DataFrame({
        "system": system, "market": spec.market, "kind": "count",
        "game_pk": pd.to_numeric(ho["game_pk"], errors="coerce").astype("Int64"),
        "game_date": ho["game_date"].dt.strftime("%Y-%m-%d").values,
        "player_id": pd.to_numeric(ho[spec.id_col], errors="coerce").astype("Int64"),
        "realized": pd.to_numeric(ho[c["target"]], errors="coerce").values,
        "p_model": np.nan, "mu": mu, "nb_alpha": nb_alpha,
    })


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Leakage-proof walk-forward ROI/CLV backtest")
    p.add_argument("--system", required=True, help=", ".join(WF_SYS))
    p.add_argument("--cutoff", required=True, help="YYYY-MM-DD: train<cutoff, score>=cutoff")
    p.add_argument("--min-edge", type=float, default=0.0)
    p.add_argument("--select", choices=["best", "consensus"], default="best",
                   help="'consensus' removes soft-book selection bias (clean edge test)")
    args = p.parse_args(argv)

    preds = walkforward_preds(args.system, args.cutoff)
    res = bt.backtest(args.system, since=args.cutoff, min_edge=args.min_edge,
                      preds=preds, select=args.select)
    if "error" in res:
        print(f"walkforward[{args.system}] ERROR: {res['error']}")
        return 1
    print(f"\nWALK-FORWARD {res['system']} ({res['market']}) -- OUT OF SAMPLE, "
          f"holdout >= {args.cutoff}, {len(res['candidates'])} bets\n")
    bt._print_report(res, f"OOS >= {args.cutoff}", res["candidates"])
    print("\nHonest scoreboard: with a truly out-of-sample model, ROI% and CLV% should "
          "AGREE. If ROI stays high AND CLV turns positive -> real edge. If ROI collapses "
          "toward 0/negative (matching the in-sample CLV) -> the earlier ROI was leakage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
