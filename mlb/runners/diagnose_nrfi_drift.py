"""
runners/diagnose_nrfi_drift.py -- NRFI v18 CONCEPT-drift attribution. (A2)

PSI (monitor_drift.py) catches COVARIATE shift -- did the feature distributions
move? NRFI's failure is CONCEPT drift: OOS AUC ~0.589 collapsed to live ~0.498
(handoff 2026-06-24) while the feature marginals may look stable. This diagnostic
measures the feature->target relationship decay DIRECTLY.

It re-scores the live v18 sub-models (pitcher / lineup / context) + the stacked
ensemble on recent NRFI_Pro_System/data/model_features.csv rows -- which carry the
realized `yrfi` target -- computes AUC per calendar month and over the recent
window, and compares each to the OOS sub-model AUCs stored in model_meta_v18.json.

Output answers: WHICH sub-model died, WHEN, and whether NRFI is salvageable by a
recency-weighted / lineup-only retrain (feeds Track B).

Reuses run_nrfi._load_v18_ensemble + _score_v18(return_components=True) so this
diagnostic can never diverge from the production scoring path.

Entrypoint: python -m mlb.runners.diagnose_nrfi_drift [--since YYYY-MM-DD]
            (run in Cloud Shell / Cloud Run -- needs xgboost + GCS)
"""
from __future__ import annotations

import json
import logging
import os

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TARGET = "yrfi"
GCS_FEATURES = "NRFI_Pro_System/data/model_features.csv"

# AUC verdict thresholds. A binary classifier at 0.50 has zero signal; we treat
# <= DEAD_FLOOR as dead, (DEAD_FLOOR, WEAK_FLOOR] as weak, > WEAK_FLOOR as ok.
DEAD_FLOOR   = float(os.getenv("DRIFT_DEAD_FLOOR", "0.52"))
WEAK_FLOOR   = float(os.getenv("DRIFT_WEAK_FLOOR", "0.55"))
# Minimum settled rows in a window before its AUC is trustworthy.
MIN_WINDOW_N = int(os.getenv("DRIFT_MIN_WINDOW_N", "200"))
# Default recent window if --since not given.
LOOKBACK_DAYS = int(os.getenv("DRIFT_LOOKBACK_DAYS", "150"))


# --- pure functions (no GCS / xgboost; unit-tested) --------------------------

def _safe_auc(y, p) -> float | None:
    """AUC with guards: drop NaN preds, require both classes and >= MIN_WINDOW_N."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    mask = ~np.isnan(p) & ~np.isnan(y)
    y, p = y[mask], p[mask]
    if len(y) < MIN_WINDOW_N:
        return None
    if len(np.unique(y)) < 2:
        return None
    from sklearn.metrics import roc_auc_score
    return round(float(roc_auc_score(y, p)), 4)


def auc_over_windows(df: pd.DataFrame, score_cols: list[str],
                     target_col: str = TARGET,
                     date_col: str = "game_date") -> dict:
    """Per-calendar-month AUC for each score column. {period: {col: auc|None, _n}}."""
    g = df.copy()
    g[date_col] = pd.to_datetime(g[date_col])
    g["_period"] = g[date_col].dt.to_period("M").astype(str)
    out: dict[str, dict] = {}
    for period, chunk in g.groupby("_period"):
        y = chunk[target_col].astype(float).values
        row: dict = {"_n": int(len(chunk))}
        for col in score_cols:
            row[col] = _safe_auc(y, chunk[col].values)
        out[period] = row
    return dict(sorted(out.items()))


def classify_submodels(live: dict, oos: dict) -> dict:
    """Per-sub-model verdict from live vs OOS AUC.

    live/oos: {name: auc|None}. Returns {name: {live, oos, gap, status}} where
    status in {ok, weak, dead, insufficient_data}.
    """
    verdict: dict[str, dict] = {}
    for name, live_auc in live.items():
        oos_auc = oos.get(name)
        if live_auc is None:
            verdict[name] = {"live": None, "oos": oos_auc, "gap": None,
                             "status": "insufficient_data"}
            continue
        if live_auc > WEAK_FLOOR:
            status = "ok"
        elif live_auc > DEAD_FLOOR:
            status = "weak"
        else:
            status = "dead"
        verdict[name] = {
            "live":   live_auc,
            "oos":    oos_auc,
            "gap":    round(oos_auc - live_auc, 4) if oos_auc is not None else None,
            "status": status,
        }
    return verdict


def recommend(verdict: dict, stacked_live: float | None) -> str:
    """Plain-English next step from the per-sub-model verdict."""
    ok   = [n for n, v in verdict.items() if v["status"] == "ok"]
    dead = [n for n, v in verdict.items() if v["status"] == "dead"]
    if stacked_live is not None and stacked_live <= DEAD_FLOOR and not ok:
        return ("All sub-models at/below noise live -- NRFI is not salvageable by "
                "retrain alone; needs new features or a different target (Track B).")
    if ok and len(ok) < len(verdict):
        return (f"Only {ok} retains live signal; {dead or 'others'} decayed. Try a "
                f"recency-weighted retrain or a {'/'.join(ok)}-only model (Track B).")
    if not ok:
        return "No sub-model clears the weak floor -- recency-weighted retrain + feature review."
    return "All sub-models retain live signal -- drift is mild; refresh via standard retrain."


# --- orchestration (Cloud) ---------------------------------------------------

def run(since: str | None = None) -> dict:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_csv, exists
    from mlb.runners.run_nrfi import _load_v18_ensemble, _score_v18

    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set -- requires GCS mode"}

    sub_boosters, meta = _load_v18_ensemble()
    if meta is None or not sub_boosters:
        return {"status": "error", "error": "v18 ensemble not loadable from GCS"}

    if not exists(GCS_FEATURES):
        return {"status": "error", "error": f"{GCS_FEATURES} not found"}
    df = read_csv(GCS_FEATURES, low_memory=False)
    if df.empty or TARGET not in df.columns:
        return {"status": "error", "error": f"features empty or '{TARGET}' missing"}

    df["game_date"] = pd.to_datetime(df["game_date"])
    cutoff = (pd.Timestamp(since) if since
              else df["game_date"].max() - pd.Timedelta(days=LOOKBACK_DAYS))
    recent = df[df["game_date"] >= cutoff].copy()
    if recent.empty:
        return {"status": "error", "error": f"no rows since {cutoff.date()}"}
    logger.info(f"concept-drift: {len(recent):,} rows since {cutoff.date()} "
                f"(through {recent['game_date'].max().date()})")

    stacked, sub_probs = _score_v18(sub_boosters, meta, recent, return_components=True)
    sub_names = list(sub_probs.keys())
    scored = recent[["game_date", TARGET]].copy()
    scored["_stacked"] = stacked
    for name in sub_names:
        scored[f"_sub_{name}"] = sub_probs[name]
    score_cols = [f"_sub_{n}" for n in sub_names] + ["_stacked"]

    monthly = auc_over_windows(scored, score_cols)

    y_all = scored[TARGET].astype(float).values
    overall = {"_n": int(len(scored))}
    for col in score_cols:
        overall[col] = _safe_auc(y_all, scored[col].values)

    oos_subs = meta.get("sub_model_aucs", {})
    live_subs = {n: overall[f"_sub_{n}"] for n in sub_names}
    verdict = classify_submodels(live_subs, oos_subs)
    stacked_live = overall["_stacked"]
    rec = recommend(verdict, stacked_live)

    result = {
        "status":        "ok",
        "window_from":   str(cutoff.date()),
        "window_to":     str(recent["game_date"].max().date()),
        "n_rows":        int(len(scored)),
        "stacked_live_auc": stacked_live,
        "stacked_oos_auc":  meta.get("auc_oos"),
        "submodels":     verdict,
        "monthly_auc":   monthly,
        "recommendation": rec,
    }
    logger.info("concept-drift verdict: %s", json.dumps(
        {"stacked_live": stacked_live, "stacked_oos": meta.get("auc_oos"),
         "submodels": {k: v["status"] for k, v in verdict.items()}}))
    logger.info("recommendation: %s", rec)
    return result


def main():
    import argparse
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s -- %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="YYYY-MM-DD lower bound (default: last 150d)")
    args = ap.parse_args()
    result = run(since=args.since)
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
