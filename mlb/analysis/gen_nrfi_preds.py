"""Generate historical NRFI v18 predictions for the odds backtest. [CLOUD SHELL]

Scores EVERY historical game in NRFI_Pro_System/data/model_features.csv with the
live v18 ensemble (reusing run_nrfi's own _load_v18_ensemble / _score_v18 so the
preds match production exactly), combines the two starters' half-inning probs into
a game-level P(YRFI), applies the isotonic calibrator, and emits a preds CSV
(game_key, p_yrfi, game_date, model_yrfi_raw) for mlb/analysis/nrfi_market.py.

Run in Cloud Shell (needs xgboost + GCS access + the v18 artifacts):

    cd ~/mlb-betting
    PYTHONPATH=. python3 -m mlb.analysis.gen_nrfi_preds --out nrfi_preds.csv

LEAKAGE NOTE: v18 was trained on 2024-2025 data, so those games are IN-SAMPLE and
will look optimistic. The genuinely out-of-sample / live read is the post-training
period (2026 rows). The script prints the meta's train range if present; filter the
backtest to the OOS window (nrfi_market.py groups by year) for the honest number.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

# Reuse production scoring verbatim -- do NOT reimplement the ensemble here.
from mlb.runners.run_nrfi import (
    _V18_CALIBRATOR_KEY,
    _load_calibrator_by_key,
    _load_v18_ensemble,
    _score_v18,
)
from mlb.analysis.nrfi_market import norm_team

_FEATURES_KEY = "NRFI_Pro_System/data/model_features.csv"


def generate(features_key: str = _FEATURES_KEY, calibrate: bool = True) -> pd.DataFrame:
    from mlb_core.storage import read_csv

    boosters, meta = _load_v18_ensemble()
    if boosters is None:
        raise RuntimeError("v18 ensemble not found in GCS -- cannot generate preds.")
    print(f"v18 loaded | AUC_OOS={meta.get('auc_oos')} | "
          f"sub_aucs={meta.get('sub_model_aucs')} | "
          f"train_range={meta.get('train_range') or meta.get('train_dates') or 'unknown'}")

    feat = read_csv(features_key, low_memory=False)
    feat["game_date"] = pd.to_datetime(feat["game_date"], errors="coerce")
    print(f"model_features: {len(feat):,} rows, "
          f"{feat['game_date'].min().date()} -> {feat['game_date'].max().date()}")

    # Score every half-inning starter row exactly as the runner does.
    feat = feat.copy()
    feat["p_half_yrfi"] = _score_v18(boosters, meta, feat)

    # Combine the two starters per game: P(YRFI) = 1 - (1-p_home)(1-p_away).
    # pitcher_is_home distinguishes the two halves (matches run_nrfi pivot).
    feat["_side"] = np.where(feat["pitcher_is_home"] == 1, "home", "away")
    pivot = feat.pivot_table(
        index=["game_pk", "away_team", "home_team", "game_date"],
        columns="_side", values="p_half_yrfi", aggfunc="first",
    ).reset_index().dropna(subset=["home", "away"])
    pivot["model_yrfi_raw"] = 1.0 - (1.0 - pivot["home"]) * (1.0 - pivot["away"])
    pivot["p_yrfi"] = pivot["model_yrfi_raw"]

    # Realized game-level YRFI from the target already in model_features.
    # The per-row `yrfi` target is half-inning; the GAME is YRFI if EITHER
    # half scored, so aggregate by max (safe even if the label is game-level).
    if "yrfi" in feat.columns:
        realized = feat.groupby("game_pk")["yrfi"].max().rename("yrfi")
        pivot = pivot.merge(realized, on="game_pk", how="left")
        print(f"realized yrfi attached for {pivot['yrfi'].notna().sum()}/{len(pivot)} games "
              f"(base rate {pivot['yrfi'].mean():.3f})")
    else:
        pivot["yrfi"] = np.nan
        print("WARNING: model_features has no 'yrfi' column -- outcomes unavailable")

    if calibrate:
        cal = _load_calibrator_by_key(_V18_CALIBRATOR_KEY)
        if cal is not None:
            raw = pivot["model_yrfi_raw"].values.copy()
            in_range = (raw >= cal.X_min_) & (raw <= cal.X_max_)
            out = raw.copy()
            if in_range.any():
                out[in_range] = cal.predict(raw[in_range])
            pivot["p_yrfi"] = out
            print(f"isotonic calibrator applied to {int(in_range.sum())}/{len(raw)} games")
        else:
            print("no v18 calibrator found -- emitting RAW probs (set --no-calibrate to keep)")

    pivot["game_key"] = (
        pivot["game_date"].dt.strftime("%Y-%m-%d")
        + "_" + pivot["away_team"].map(norm_team)
        + "@" + pivot["home_team"].map(norm_team)
    )
    return pivot[["game_key", "p_yrfi", "yrfi", "game_date", "model_yrfi_raw"]].sort_values("game_date")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate historical NRFI v18 preds")
    ap.add_argument("--out", default="nrfi_preds.csv")
    ap.add_argument("--features-key", default=_FEATURES_KEY)
    ap.add_argument("--no-calibrate", action="store_true")
    args = ap.parse_args()

    preds = generate(args.features_key, calibrate=not args.no_calibrate)
    preds.to_csv(args.out, index=False)
    print(f"\nwrote {len(preds):,} game preds -> {args.out}")
    print(preds.groupby(preds["game_date"].dt.year)["p_yrfi"].agg(["count", "mean"]))


if __name__ == "__main__":
    main()
