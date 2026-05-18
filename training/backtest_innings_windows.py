"""
training/backtest_innings_windows.py

Derives empirical scaling factors for F3, First Half (1H), F7, and Full
Game ML markets by comparing actual home win rates at each innings window
against F5 model probabilities on the same historical games.

Run from Cloud Shell (once, before deploying the sub-market runners):

    gcloud run services proxy mlb-betting --region=us-central1 --port=8081 &
    sleep 4
    python3 training/backtest_innings_windows.py

Output: prints the empirical scalars to use in run_f5.py sub-market
scoring, plus calibration diagnostics per probability bucket.

Saves results to:
    F5_Pro_System/data/innings_window_backtest.csv  (full per-game results)
    F5_Pro_System/data/innings_window_scalars.json  (scalar summary)

These scalars replace the prior-based defaults in run_f5.py once you have
empirical confirmation they are stable.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s -- %(message)s")
logger = logging.getLogger("backtest_innings")

# ── Innings window definitions ────────────────────────────────────────────

WINDOWS = {
    "F3":   (1, 3),
    "F1H":  (1, 4),   # First Half = innings 1-4 on DK
    "F5":   (1, 5),   # Reference -- should match F5 model directly
    "F7":   (1, 7),
    "GAME": (1, 9),   # Full game (9+ innings, some go extra)
}

# ── Load data ─────────────────────────────────────────────────────────────

def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load scoring_master and F5 model_features from GCS."""
    from mlb_core.storage import read_csv, exists

    logger.info("Loading scoring_master.csv")
    if not exists("Scoring/scoring_master.csv"):
        logger.error("scoring_master.csv not found in GCS")
        sys.exit(1)
    scoring = read_csv("Scoring/scoring_master.csv", low_memory=False)
    scoring["game_pk"] = pd.to_numeric(scoring["game_pk"], errors="coerce")
    scoring["inning"]  = pd.to_numeric(scoring["inning"],  errors="coerce")
    scoring["runs"]    = pd.to_numeric(scoring["runs"],    errors="coerce").fillna(0)
    scoring = scoring.dropna(subset=["game_pk", "inning"])
    logger.info(f"  scoring_master: {len(scoring):,} rows | "
                f"{scoring['game_pk'].nunique():,} games")

    logger.info("Loading F5 model_features.csv")
    if not exists("F5_Pro_System/data/model_features.csv"):
        logger.error("F5 model_features.csv not found -- run build_f5_features first")
        sys.exit(1)
    f5 = read_csv("F5_Pro_System/data/model_features.csv", low_memory=False)
    f5["game_pk"]   = pd.to_numeric(f5["game_pk"], errors="coerce")
    f5["game_date"] = pd.to_datetime(f5["game_date"], errors="coerce")
    f5 = f5.dropna(subset=["game_pk"])

    # We need p_home from the F5 model -- it must have been scored and stored.
    # model_features.csv has home_wins_f5 (target) but not p_home predictions.
    # We need to re-score using the live model.
    logger.info(f"  F5 features: {len(f5):,} rows | "
                f"{f5['game_pk'].nunique():,} games | "
                f"cols: {list(f5.columns[:10])}")
    return scoring, f5


# ── Score F5 model on historical data ─────────────────────────────────────

def score_f5_historical(f5: pd.DataFrame) -> pd.DataFrame:
    """Run F5 model on historical features to get p_home per game.

    Returns f5 with a new `p_home` column.
    """
    import json
    import tempfile
    from pathlib import Path
    import xgboost as xgb
    from mlb_core.storage import download_model, read_bytes, exists
    from mlb_core.config import GCS_BUCKET

    logger.info("Scoring F5 model on historical features")

    if not exists("F5_Pro_System/models/xgb_f5_v5.json"):
        logger.error("F5 model artifact not found in GCS")
        sys.exit(1)

    booster = xgb.Booster()
    with tempfile.TemporaryDirectory() as tmpdir:
        local = download_model(
            "F5_Pro_System/models/xgb_f5_v5.json",
            Path(tmpdir) / "xgb_f5.json",
        )
        booster.load_model(str(local))
        meta_raw  = read_bytes("F5_Pro_System/models/model_meta_f5_v5.json")

    meta          = json.loads(meta_raw)
    features      = meta["features"]
    feature_means = meta.get("feature_means", {}) or {}
    booster.best_ntree_limit = meta.get("best_iteration", 0)
    logger.info(f"  F5 model: {len(features)} features | "
                f"AUC={meta.get('auc_oos', meta.get('wf_auc_mean', '?'))}")

    X = f5.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
    for col in features:
        m = feature_means.get(col)
        if m is not None:
            X[col] = X[col].fillna(float(m))
    X = X.astype(float)

    dm    = xgb.DMatrix(X, feature_names=features)
    ntree = getattr(booster, "best_ntree_limit", 0)
    preds = booster.predict(dm, iteration_range=(0, ntree) if ntree else None)

    f5 = f5.copy()
    f5["p_home"] = preds
    logger.info(f"  Scored {len(f5):,} historical games | "
                f"p_home mean={f5['p_home'].mean():.3f}")
    return f5


# ── Derive outcomes per innings window ────────────────────────────────────

def derive_window_outcomes(scoring: pd.DataFrame,
                            game_pks: set) -> pd.DataFrame:
    """For each game_pk, compute home_wins_{window} for all WINDOWS.

    Uses scoring_master.csv. Drops games where the window isn't complete
    (e.g. rain-shortened games that ended before inning 5 for F5 reference).

    Returns DataFrame keyed on game_pk with one column per window.
    """
    logger.info("Deriving outcomes for all innings windows")

    sc = scoring[scoring["game_pk"].isin(game_pks)].copy()

    rows: list[dict] = []
    for game_pk, g in sc.groupby("game_pk"):
        row: dict = {"game_pk": game_pk}
        for window_name, (inn_min, inn_max) in WINDOWS.items():
            w = g[g["inning"].between(inn_min, inn_max)]
            pivot = (w.pivot_table(index="game_pk", columns="half",
                                   values="runs", aggfunc="sum", fill_value=0)
                      .reset_index())
            pivot.columns.name = None

            # Need both top and bot present for a valid outcome
            if "top" not in pivot.columns or "bot" not in pivot.columns:
                row[f"home_wins_{window_name}"] = np.nan
                row[f"complete_{window_name}"]  = 0
                continue

            # Check inning coverage -- need min_periods on both halves
            top_innings = w[w["half"] == "top"]["inning"].nunique()
            bot_innings = w[w["half"] == "bot"]["inning"].nunique()
            required    = inn_max - inn_min + 1

            # Full game: accept if >= 9 innings top, >= 8.5 innings bot
            # (home doesn't bat in bot of 9 if leading)
            if window_name == "GAME":
                complete = (top_innings >= 9 and bot_innings >= 8)
            else:
                complete = (top_innings >= required and bot_innings >= required)

            if not complete:
                row[f"home_wins_{window_name}"] = np.nan
                row[f"complete_{window_name}"]  = 0
                continue

            away_runs = pivot.iloc[0].get("top", 0)   # away bats top
            home_runs = pivot.iloc[0].get("bot", 0)   # home bats bot

            if away_runs == home_runs:
                # Push -- exclude from calibration (no clear signal)
                row[f"home_wins_{window_name}"] = np.nan
                row[f"complete_{window_name}"]  = 1
            else:
                row[f"home_wins_{window_name}"] = int(home_runs > away_runs)
                row[f"complete_{window_name}"]  = 1

        rows.append(row)

    outcomes = pd.DataFrame(rows)
    for window_name in WINDOWS:
        col = f"home_wins_{window_name}"
        n_valid = outcomes[col].notna().sum()
        n_home  = outcomes[col].sum()
        logger.info(f"  {window_name}: {n_valid:,} complete games | "
                    f"home win rate {n_home/n_valid:.3f}")
    return outcomes


# ── Compute empirical scalars ─────────────────────────────────────────────

def compute_scalars(merged: pd.DataFrame) -> dict:
    """Derive empirical scalar for each window vs F5 reference.

    Scalar = slope of OLS regression: actual_outcome ~ p_home_scaled
    where p_home_scaled = 0.5 + (p_home_f5 - 0.5) * scalar

    We fit the scalar that minimises Brier score between p_home_scaled
    and actual_outcome.

    Also computes calibration diagnostics per 0.05-wide probability bucket.
    """
    from scipy.optimize import minimize_scalar

    scalars: dict = {}

    for window_name in WINDOWS:
        col = f"home_wins_{window_name}"
        if col not in merged.columns:
            continue

        valid = merged[["p_home", col]].dropna()
        if len(valid) < 50:
            logger.warning(f"  {window_name}: only {len(valid)} valid games -- "
                           f"scalar unreliable")
            scalars[window_name] = {
                "scalar": 1.0 if window_name == "F5" else None,
                "n":      len(valid),
                "note":   "insufficient data",
            }
            continue

        p_f5    = valid["p_home"].values
        actual  = valid[col].values

        if window_name == "F5":
            # Reference -- scalar = 1.0 by definition
            scaled  = p_f5
            brier   = float(np.mean((scaled - actual) ** 2))
            scalars[window_name] = {
                "scalar":       1.0,
                "n":            len(valid),
                "brier":        round(brier, 5),
                "actual_hw_rate": round(float(actual.mean()), 4),
                "mean_p_home":  round(float(p_f5.mean()), 4),
            }
            continue

        def brier_loss(s):
            scaled = 0.5 + (p_f5 - 0.5) * s
            scaled = np.clip(scaled, 0.02, 0.98)
            return float(np.mean((scaled - actual) ** 2))

        result  = minimize_scalar(brier_loss, bounds=(0.3, 1.5), method="bounded")
        scalar  = float(result.x)
        scaled  = np.clip(0.5 + (p_f5 - 0.5) * scalar, 0.02, 0.98)
        brier   = float(np.mean((scaled - actual) ** 2))

        # Calibration: actual hit rate per 0.05-wide bucket
        buckets: list[dict] = []
        edges   = np.arange(0.3, 0.75, 0.05)
        for lo in edges:
            hi   = lo + 0.05
            mask = (scaled >= lo) & (scaled < hi)
            n    = int(mask.sum())
            if n >= 10:
                buckets.append({
                    "bucket":      f"{lo:.2f}-{hi:.2f}",
                    "n":           n,
                    "mean_p":      round(float(scaled[mask].mean()), 4),
                    "actual_rate": round(float(actual[mask].mean()), 4),
                    "cal_error":   round(float(scaled[mask].mean() - actual[mask].mean()), 4),
                })

        scalars[window_name] = {
            "scalar":         round(scalar, 4),
            "n":              len(valid),
            "brier":          round(brier, 5),
            "actual_hw_rate": round(float(actual.mean()), 4),
            "mean_p_home":    round(float(p_f5.mean()), 4),
            "mean_p_scaled":  round(float(scaled.mean()), 4),
            "calibration":    buckets,
        }
        logger.info(
            f"  {window_name}: scalar={scalar:.4f} | "
            f"n={len(valid):,} | brier={brier:.5f} | "
            f"actual_hw={actual.mean():.3f} | "
            f"mean_p_scaled={scaled.mean():.3f}"
        )

    return scalars


# ── Main ──────────────────────────────────────────────────────────────────

def run() -> None:
    from mlb_core.storage import write_csv, write_bytes
    from mlb_core.config import GCS_BUCKET

    scoring, f5 = load_data()

    # Score F5 model on all historical feature rows
    f5 = score_f5_historical(f5)

    # Only keep games that appear in both scoring and f5 features
    game_pks = set(f5["game_pk"].dropna().astype(int)) & \
               set(scoring["game_pk"].dropna().astype(int))
    logger.info(f"Games in both datasets: {len(game_pks):,}")

    # Derive outcomes for all innings windows
    outcomes = derive_window_outcomes(scoring, game_pks)

    # Merge with F5 predictions
    f5_dedup  = f5[["game_pk", "game_date", "home_team", "away_team",
                    "p_home", "home_wins_f5"]].drop_duplicates("game_pk")
    merged    = f5_dedup.merge(outcomes, on="game_pk", how="inner")
    logger.info(f"Merged: {len(merged):,} games")

    # Compute scalars
    scalars = compute_scalars(merged)

    # Print summary
    print("\n" + "=" * 60)
    print("INNINGS WINDOW SCALAR SUMMARY")
    print("=" * 60)
    for window, s in scalars.items():
        if s.get("note"):
            print(f"  {window:6s}: INSUFFICIENT DATA ({s['n']} games)")
            continue
        print(f"  {window:6s}: scalar={s.get('scalar', 'N/A'):.4f} | "
              f"n={s['n']:,} | brier={s.get('brier', 0):.5f} | "
              f"actual_hw={s.get('actual_hw_rate', 0):.3f}")
        for b in s.get("calibration", []):
            err_str = f"  cal_err={b['cal_error']:+.4f}" if abs(b["cal_error"]) > 0.02 else ""
            print(f"    bucket {b['bucket']}: n={b['n']:3d} | "
                  f"p={b['mean_p']:.3f} | actual={b['actual_rate']:.3f}{err_str}")
    print("=" * 60)

    # Save outputs
    if GCS_BUCKET:
        write_csv(merged, "F5_Pro_System/data/innings_window_backtest.csv")
        scalars_json = json.dumps(scalars, indent=2)
        write_bytes(scalars_json.encode(), "F5_Pro_System/data/innings_window_scalars.json")
        logger.info("Saved backtest CSV and scalars JSON to GCS")
    else:
        out_dir = Path("F5_Pro_System/data")
        out_dir.mkdir(parents=True, exist_ok=True)
        merged.to_csv(out_dir / "innings_window_backtest.csv", index=False)
        with open(out_dir / "innings_window_scalars.json", "w") as f:
            json.dump(scalars, f, indent=2)
        logger.info(f"Saved backtest outputs to {out_dir}")


if __name__ == "__main__":
    run()
