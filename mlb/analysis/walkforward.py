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
# contract constants used below (XGB_PARAMS, TARGET, a *_FEATURES list OR a
# _NON_FEATURE_COLS exclusion set for dynamic feature selection).
WF_SYS = {
    "K":           ("mlb.training.retrain_k_v1",           "count"),
    "OUTS":        ("mlb.training.retrain_outs_v1",        "count"),
    "BATTER_HITS": ("mlb.training.retrain_batter_hits_v1", "count"),
    "BATTER_TB":   ("mlb.training.retrain_batter_tb_v1",   "count"),
    "HR":          ("mlb.training.retrain_hr_v6",          "binary"),
    "GAME":        ("mlb.training.retrain_game_v1",        "binary"),
}


def _get(mod, *names, default=None):
    for n in names:
        if hasattr(mod, n):
            return getattr(mod, n)
    return default


def _resolve_contract(system: str):
    """Pull the production training contract from the system's retrain module.
    feats=None means DYNAMIC selection (numeric, not in `exclude`, >=10% coverage) --
    matches retrain_hr_v6, which builds its feature list at runtime."""
    mod_name, kind = WF_SYS[system]
    mod = importlib.import_module(mod_name)
    params = _get(mod, "XGB_PARAMS")
    target = _get(mod, "TARGET")
    feats = _get(mod, "K_FEATURES", "OUTS_FEATURES", "BATTER_HITS_FEATURES",
                 "BATTER_TB_FEATURES", "GAME_FEATURES", "FEATURES")
    exclude = set(_get(mod, "_NON_FEATURE_COLS", "NON_FEATURE_COLS", default=set()) or set())
    n_round = int(_get(mod, "NUM_BOOST_ROUND", default=2000))
    early = int(_get(mod, "EARLY_STOPPING_ROUNDS", "EARLY_STOPPING", default=50))
    if not (params and target):
        raise RuntimeError(f"{mod_name} missing XGB_PARAMS/TARGET")
    if not feats and not exclude:
        raise RuntimeError(f"{mod_name}: no *_FEATURES list and no exclusion set")
    return dict(params=params, target=target, feats=list(feats) if feats else None,
                exclude=exclude, n_round=n_round, early=early, kind=kind)


def _train_pre_cutoff(tr: pd.DataFrame, feats: list, c: dict):
    """Train exactly like production: carve a validation tail for early-stopping to
    find best_iteration, then full-retrain on ALL pre-cutoff rows with that many
    rounds. For rare-event binary (e.g. HR ~7%) add scale_pos_weight = n_neg/n_pos,
    matching retrain_hr_v6. Returns (booster, best_iter)."""
    import xgboost as xgb
    X = tr[feats].apply(pd.to_numeric, errors="coerce")
    y = tr[c["target"]].astype(float)
    params = dict(c["params"])
    if c["kind"] == "binary" and "scale_pos_weight" not in params:
        pos = float(y.mean())
        if 0 < pos < 0.20:   # rare positive (HR); GAME ~0.54 -> untouched
            params["scale_pos_weight"] = round((1 - pos) / pos, 2)
    nval = int(len(X) * (7 / 8))
    dtr = xgb.DMatrix(X.iloc[:nval], label=y.iloc[:nval], feature_names=feats)
    dval = xgb.DMatrix(X.iloc[nval:], label=y.iloc[nval:], feature_names=feats)
    b = xgb.train(params, dtr, num_boost_round=c["n_round"],
                  evals=[(dval, "val")], early_stopping_rounds=c["early"],
                  verbose_eval=False)
    best = int(getattr(b, "best_iteration", c["n_round"] - 1)) + 1
    # full retrain on 100% of pre-cutoff data (production Section 7b)
    dall = xgb.DMatrix(X, label=y, feature_names=feats)
    booster = xgb.train(params, dall, num_boost_round=best, verbose_eval=False)
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


def _prepare(system: str, quiet: bool = False):
    """Load + clean a system's feature table ONCE (spec, contract, df, feats).
    Reused across all rolling cutoffs so the 268k-row CSV isn't re-read per window."""
    if system not in WF_SYS:
        raise ValueError(f"walkforward supports {list(WF_SYS)}; got {system!r}")
    spec = gp.SPECS[system]
    c = _resolve_contract(system)
    df = gp._read_csv(spec.feature_csv, low_memory=False)
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    df = df.dropna(subset=[c["target"], "game_date"]).sort_values("game_date").reset_index(drop=True)
    if c["feats"]:
        feats = [f for f in c["feats"] if f in df.columns]
        if len(feats) < len(c["feats"]) and not quiet:
            print(f"  note: {len(c['feats'])-len(feats)} contract features absent, using {len(feats)}")
    else:
        # dynamic (HR): numeric, not excluded, >=10% coverage -- mirrors retrain_hr_v6
        feats = [col for col in df.columns
                 if col not in c["exclude"] and col != c["target"]
                 and pd.api.types.is_numeric_dtype(df[col])
                 and df[col].notna().mean() >= 0.10]
        if not quiet:
            print(f"  dynamic feature selection: {len(feats)} features")
    return spec, c, df, feats


def walkforward_preds(system: str, cutoff: str, until: str | None = None,
                      quiet: bool = False, prep: tuple | None = None) -> pd.DataFrame:
    """Train on game_date < cutoff, score game_date in [cutoff, until). Returns a
    preds frame in gen_preds schema (kind=count: mu + nb_alpha), OUT OF SAMPLE.
    Pass `prep` (from _prepare) to avoid re-reading the feature CSV each window."""
    import xgboost as xgb
    spec, c, df, feats = prep or _prepare(system, quiet=quiet)

    tr = df[df["game_date"] < cutoff]
    ho = df[df["game_date"] >= cutoff]
    if until:
        ho = ho[ho["game_date"] < until]
    if len(tr) < 200 or len(ho) < 10:
        raise RuntimeError(f"bad split: train={len(tr)} holdout={len(ho)} at cutoff {cutoff}")
    if not quiet:
        print(f"  walk-forward {system}: train {len(tr)} rows (<{cutoff}) "
              f"[{tr['game_date'].min().date()}..{tr['game_date'].max().date()}] "
              f"-> holdout {len(ho)} rows ([{cutoff}, {until or 'end'}))")

    booster, best = _train_pre_cutoff(tr, feats, c)
    means = {f: float(pd.to_numeric(tr[f], errors="coerce").mean()) for f in feats}

    Xh = ho[feats].apply(pd.to_numeric, errors="coerce")
    for f in feats:
        Xh[f] = Xh[f].fillna(means[f])
    pred = booster.predict(xgb.DMatrix(Xh.astype(float), feature_names=feats),
                           iteration_range=(0, best))

    # game-level systems (GAME) have no player id -> NA player_id, join on game_pk only
    pid = (pd.to_numeric(ho[spec.id_col], errors="coerce").astype("Int64")
           if spec.id_col else pd.Series([pd.NA] * len(ho), dtype="Int64"))
    base = {
        "system": system, "market": spec.market,
        "game_pk": pd.to_numeric(ho["game_pk"], errors="coerce").astype("Int64"),
        "game_date": ho["game_date"].dt.strftime("%Y-%m-%d").values,
        "player_id": pid,
        "realized": pd.to_numeric(ho[c["target"]], errors="coerce").values,
    }
    if c["kind"] == "count":
        nb_alpha = _fit_nb_alpha(booster, tr, feats, c["target"])
        if not quiet:
            print(f"  trained best_iter={best} nb_alpha={nb_alpha:.4f}")
        return pd.DataFrame({**base, "kind": "count",
                             "p_model": np.nan, "mu": pred, "nb_alpha": nb_alpha})
    # binary (HR, GAME): booster.predict IS the probability of the positive class
    if not quiet:
        print(f"  trained best_iter={best} (binary; pos-rate hold {float(pred.mean()):.3f})")
    return pd.DataFrame({**base, "kind": "binary",
                         "p_model": pred, "mu": np.nan, "nb_alpha": np.nan})


def rolling(system: str, start: str, end: str, step_months: int = 1,
            edge: float = 0.10, select: str = "consensus", out_prefix: str | None = None,
            min_books: int = 1, max_spread: float = 1.0) -> dict:
    """Retrain at each monthly cutoff in [start, end); score the NEXT window cold;
    pool the >= `edge` bucket across all windows. Each window is an independent
    out-of-sample test -- a stable edge here (not one lucky month) is the real proof."""
    cutoffs = [d.strftime("%Y-%m-%d") for d in
               pd.date_range(start=start, end=end, freq=f"{step_months}MS")]
    print(f"\nROLLING walk-forward {system}: {len(cutoffs)} windows "
          f"[{start}..{end}] step={step_months}mo  edge>={edge:.0%}  select={select}\n")
    prep = _prepare(system)   # load the feature CSV ONCE, reuse every window
    print(f"  {'window':>12}  {'bets':>5}  {'hit%':>6}  {'roi_cons%':>9}  {'units':>7}  {'over%':>5}")
    pooled = []
    for i, cut in enumerate(cutoffs):
        nxt = cutoffs[i + 1] if i + 1 < len(cutoffs) else end
        try:
            preds = walkforward_preds(system, cut, until=nxt, quiet=True, prep=prep)
            res = bt.backtest(system, since=cut, until=nxt, preds=preds, select=select,
                              min_books=min_books, max_spread=max_spread)
            if "error" in res:
                print(f"  {cut:>12}  (no bets)"); continue
            c = res["candidates"]
            b = c[c["edge"] >= edge]
            if not len(b):
                print(f"  {cut:>12}  (0 bets >= edge)"); continue
            over = (b["selection"].isin(["OVER", "YES", "HOME"])).mean() * 100
            print(f"  {cut:>12}  {len(b):>5}  {(b['won']==1).mean()*100:>5.1f}  "
                  f"{b['roi_cons'].mean()*100:>+8.2f}  {b['roi_cons'].sum():>+6.1f}  {over:>4.0f}")
            pooled.append(b)
        except Exception as e:  # noqa: BLE001
            print(f"  {cut:>12}  ERROR {e}")
    if not pooled:
        return {"error": "no pooled bets"}
    allb = pd.concat(pooled, ignore_index=True)
    roi = allb["roi_cons"].mean()
    sd = allb["roi_cons"].std()
    se = sd / (len(allb) ** 0.5)
    # roi_best = the SOFT-LINE strategy (bet the softest available price); roi_cons =
    # the no-soft-line baseline (bet at consensus). best>>cons => real soft-line value.
    roi_best = allb["roi"].mean()
    se_best = allb["roi"].std() / (len(allb) ** 0.5)
    print(f"\n  POOLED {len(allb)} bets across {len(pooled)} windows:")
    print(f"    ROI(best/soft-line) {roi_best*100:+.2f}%  (z={roi_best/se_best:.1f})  "
          f"units {allb['roi'].sum():+.1f}   <- YOUR soft-line strategy")
    print(f"    ROI(consensus)      {roi*100:+.2f}%  (z={roi/se:.1f})  "
          f"units {allb['roi_cons'].sum():+.1f}   <- no-soft-line baseline")
    print(f"    win-rate {(allb['won']==1).mean()*100:.1f}%  avg n_books "
          f"{allb['n_books'].mean():.1f}  CLV {allb['clv_pct'].mean():+.2f}% (adverse-selection check)")

    def _seg(name, g):
        return (f"    {name:>14}  n={len(g):>5}  hit={ (g['won']==1).mean()*100:>4.1f}%  "
                f"roi_cons={g['roi_cons'].mean()*100:>+7.2f}%  "
                f"nbk={g['n_books'].mean():>3.1f}  clv={g['clv_pct'].mean():>+6.2f}%")
    print("\n  -- by line value --")
    for lv, g in allb.groupby("line"):
        if len(g) >= 20:
            print(_seg(f"line {lv}", g))
    print("  -- by book depth --")
    print(_seg("n_books<=2", allb[allb["n_books"] <= 2]))
    print(_seg("n_books>=4", allb[allb["n_books"] >= 4]))
    print("  -- by entry odds --")
    print(_seg("dec<1.5 (juiced)", allb[allb["decimal"] < 1.5]))
    print(_seg("1.5<=dec<=3.5", allb[(allb["decimal"] >= 1.5) & (allb["decimal"] <= 3.5)]))
    print(_seg("dec>3.5 (longshot)", allb[allb["decimal"] > 3.5]))
    print("\n  -- sample bets (10%+ edge) --")
    cols = ["game_date", "player_id", "selection", "line", "book", "decimal",
            "consensus_dec", "model_prob", "fair_prob", "realized", "won"]
    smp = allb[cols].head(15)
    with pd.option_context("display.width", 220, "display.max_columns", 20):
        print(smp.to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    summary = {"system": system, "start": start, "end": end, "select": select,
               "edge": edge, "n_windows": len(pooled), "n_bets": int(len(allb)),
               "roi_best": round(roi_best, 4), "z_best": round(roi_best / se_best, 2),
               "roi_cons": round(roi, 4), "z": round(roi / se, 2),
               "win_rate": round((allb["won"] == 1).mean(), 4),
               "clv": round(allb["clv_pct"].mean(), 3),
               "avg_n_books": round(allb["n_books"].mean(), 2),
               "line_counts": {str(k): int(v) for k, v in allb["line"].value_counts().head(8).items()}}
    if out_prefix:
        import json as _json
        from mlb_core import storage
        storage.write_csv(allb, f"{out_prefix}/{system}_pooled_bets.csv")
        storage.write_bytes(_json.dumps(summary, indent=2).encode(),
                            f"{out_prefix}/{system}_summary.json")
        print(f"\n  wrote -> {out_prefix}/{system}_{{summary.json,pooled_bets.csv}}")
    return {"pooled": allb, "summary": summary, "roi": roi, "z": roi / se, "n": len(allb)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Leakage-proof walk-forward ROI/CLV backtest")
    p.add_argument("--system", default=None, help=", ".join(WF_SYS))
    p.add_argument("--cutoff", default=None, help="YYYY-MM-DD: train<cutoff, score>=cutoff")
    p.add_argument("--min-edge", type=float, default=0.0)
    p.add_argument("--select", choices=["best", "consensus"], default="best",
                   help="'consensus' removes soft-book selection bias (clean edge test)")
    # rolling mode
    p.add_argument("--rolling", action="store_true",
                   help="retrain at monthly cutoffs across [--start,--end); pool the edge bucket")
    p.add_argument("--all", action="store_true", help="run rolling for ALL WF_SYS systems")
    p.add_argument("--start", default="2024-05-01")
    p.add_argument("--end", default="2026-06-01")
    p.add_argument("--step-months", type=int, default=1)
    p.add_argument("--edge", type=float, default=0.10, help="edge-bucket threshold to pool (rolling)")
    p.add_argument("--min-books", type=int, default=1, help="require >= N books quoting the side")
    p.add_argument("--max-spread", type=float, default=1.0,
                   help="drop markets where cross-book implied range exceeds this (e.g. 0.10)")
    p.add_argument("--out-prefix", default=None,
                   help="GCS/local prefix to persist per-system summary.json + pooled_bets.csv")
    args = p.parse_args(argv)

    if (args.rolling and not args.system) and not args.all:
        p.error("--rolling needs --system (or use --all)")
    if args.rolling or args.all:
        systems = list(WF_SYS) if args.all else [args.system]
        results = {}
        for s in systems:
            try:
                r = rolling(s, args.start, args.end, step_months=args.step_months,
                            edge=args.edge, select=args.select, out_prefix=args.out_prefix,
                            min_books=args.min_books, max_spread=args.max_spread)
                results[s] = r.get("summary", {"error": r.get("error")})
            except Exception as e:  # noqa: BLE001
                print(f"\n{s}: ERROR {e}")
                results[s] = {"error": str(e)}
        if args.all:
            print("\n===== BATCH SUMMARY =====")
            print(f"  {'system':>12}  {'bets':>6}  {'roi_cons%':>9}  {'z':>6}  {'CLV%':>6}  {'nbk':>4}")
            for s, v in results.items():
                if "error" in v:
                    print(f"  {s:>12}  ERROR {v['error']}"); continue
                print(f"  {s:>12}  {v['n_bets']:>6}  {v['roi_cons']*100:>+8.2f}  "
                      f"{v['z']:>6.1f}  {v['clv']:>+6.2f}  {v['avg_n_books']:>4.1f}")
        return 0

    if not args.cutoff:
        p.error("--cutoff required unless --rolling")
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
