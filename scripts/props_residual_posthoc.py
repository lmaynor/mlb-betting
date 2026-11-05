"""POST-HOC robustness tables for the props market-residual study (run AFTER the held-out season was scored, so nothing here selects
anything; it only describes the saved predictions).

    PYTHONPATH=. python -m scripts.props_residual_posthoc --table residual_table.parquet --preds final_preds.parquet --holdout 2025

Prints: paired log loss between every pair of models on each season (is the feature model better than the earlier c-only blend?),
the per-stat gain of every model, the gain by calendar month (is it one lucky stretch?), and the ROI at the MEDIAN book price for
looser edge cuts (0 / 1 / 2%).
"""
from __future__ import annotations

import argparse

import pandas as pd

from mlb_core.odds import props_residual as R

MODELS = ("p_ridge", "p_gbdt", "p_blend_c", "p_calib")


def pair_table(te: pd.DataFrame, preds: pd.DataFrame) -> pd.DataFrame:
    y, cl = te["over"].to_numpy(float), te["game_date"].to_numpy()
    rows = []
    for a in MODELS:
        for b in ("p_mkt",) + tuple(m for m in MODELS if m != a):
            if a == b:
                continue
            r = R.paired_logloss(y, preds.loc[te.index, a].to_numpy(float), preds.loc[te.index, b].to_numpy(float), cl)
            rows.append({"a": a[2:], "b": b[2:], "d": r["d"], "se": r["se"], "z": r["z"]})
    return pd.DataFrame(rows)


def stat_table(te: pd.DataFrame, preds: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for stat, sub in te.groupby("stat"):
        y, cl = sub["over"].to_numpy(float), sub["game_date"].to_numpy()
        pm = preds.loc[sub.index, "p_mkt"].to_numpy(float)
        row = {"stat": stat, "n": len(sub)}
        for m in MODELS:
            r = R.paired_logloss(y, preds.loc[sub.index, m].to_numpy(float), pm, cl)
            row[m[2:]] = f"{r['d']:+.5f} ({r['z']:+.1f})"
        rows.append(row)
    return pd.DataFrame(rows)


def month_table(te: pd.DataFrame, preds: pd.DataFrame, model: str) -> pd.DataFrame:
    rows = []
    for ym, sub in te.groupby(te["game_date"].str[:7]):
        if len(sub) < 1500:
            continue
        y, cl = sub["over"].to_numpy(float), sub["game_date"].to_numpy()
        r = R.paired_logloss(y, preds.loc[sub.index, model].to_numpy(float), preds.loc[sub.index, "p_mkt"].to_numpy(float), cl)
        rows.append({"month": ym, "n": len(sub), "d": r["d"], "z": r["z"]})
    return pd.DataFrame(rows)


def median_price_table(te: pd.DataFrame, preds: pd.DataFrame, thresholds=(0.0, 0.01, 0.02)) -> pd.DataFrame:
    """ROI at the MEDIAN book price (the price a bettor can actually take at close, no best-quote artifact) for looser edge cuts."""
    rows = []
    for m in MODELS + ("p_mkt",):
        for thr in thresholds:
            r = R.roi_threshold(te, preds.loc[te.index, m].to_numpy(float), "med", thr, n_boot=300)
            rows.append({"model": m[2:], "min_edge": thr, "n_bets": r["n"], "roi": r.get("roi"),
                         "ci": R._ci(r.get("roi_ci")) if r["n"] else "", "mkt_expected": r.get("mkt_ev")})
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--table", required=True)
    ap.add_argument("--preds", required=True)
    ap.add_argument("--holdout", type=int, required=True)
    a = ap.parse_args(argv)
    t = R.prepare(pd.read_parquet(a.table))
    preds = pd.read_parquet(a.preds)
    te = t.loc[preds.index]
    for label, sub in (("development folds", te[te["season"] < a.holdout]), (f"held-out {a.holdout}", te[te["season"] == a.holdout])):
        if not len(sub):
            continue
        print(f"\n## {label}: n={len(sub)}\n")
        print("paired log loss a - b (negative = a better), date-clustered z:")
        print(pair_table(sub, preds).round(5).to_string(index=False))
        print("\nper stat, d LL vs market (z):")
        print(stat_table(sub, preds).to_string(index=False))
        for m in ("p_ridge", "p_gbdt"):
            print(f"\nby month, {m}:")
            print(month_table(sub, preds, m).round(5).to_string(index=False))
        print("\nROI at the MEDIAN book price by minimum model edge (flat 1u, one bet per prop):")
        print(median_price_table(sub, preds).round(4).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
