"""Walk-forward backtest for MOD-04 B3 NHL skater SOG structural shadow model (`nhl_sog_trained_v1`).

SHADOW ONLY: evaluates `SogStructuralModel` via expanding walk-forward folds with strictly
prior training slices (`date < fold_start`), computing count log-likelihood, MAE, and line 2.5
calibration (Brier score and binary log-loss).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from nhl.props import mod04_features as F
from nhl.props import sog_trained as ST
from nhl.xg import data as D

MAIN_LINE = 2.5


def _expanding_fold_starts(dates: pd.Series, test_from: str, step_months: int) -> list[pd.Timestamp]:
    dt = pd.to_datetime(dates, errors="coerce").dropna()
    if dt.empty:
        return []
    cur = pd.Timestamp(test_from)
    end = dt.max()
    starts: list[pd.Timestamp] = []
    while cur <= end:
        starts.append(cur)
        cur = cur + pd.DateOffset(months=int(step_months))
    return starts


def run_backtest(
    feat: pd.DataFrame,
    test_from: str,
    step_months: int = 3,
    target: str = "sog",
) -> dict[str, Any]:
    """Run expanding walk-forward backtest for `SogStructuralModel` from `test_from`."""
    if feat is None or feat.empty:
        return {
            "engine": ST.ENGINE_LABEL,
            "test_from": str(test_from),
            "step_months": int(step_months),
            "n": 0,
            "folds": 0,
            "mean_ll": None,
            "mae": None,
            "brier_2p5": None,
            "ll_2p5": None,
        }

    if target not in feat.columns:
        raise ValueError(f"feat must contain target column {target!r} for backtest evaluation")
    if "date" not in feat.columns:
        raise ValueError("feat must contain 'date' column")

    df = feat.copy()
    df["date"] = df["date"].astype(str).str[:10]
    sort_cols = [c for c in ("date", "game_id", "player_id") if c in df.columns]
    df = df.sort_values(sort_cols).reset_index(drop=True)

    starts = _expanding_fold_starts(df["date"], test_from=test_from, step_months=step_months)

    ll_parts: list[np.ndarray] = []
    ae_parts: list[np.ndarray] = []
    brier_parts: list[np.ndarray] = []
    ll_2p5_parts: list[np.ndarray] = []
    p_over_parts: list[np.ndarray] = []
    y_over_parts: list[np.ndarray] = []
    folds = 0

    for i, s0 in enumerate(starts):
        s0_str = s0.strftime("%Y-%m-%d")
        s1_str = starts[i + 1].strftime("%Y-%m-%d") if i + 1 < len(starts) else "2100-01-01"

        tr = df[(df["date"] < s0_str) & df[target].notna()].copy()
        te = df[(df["date"] >= s0_str) & (df["date"] < s1_str) & df[target].notna()].copy()
        if tr.empty or te.empty:
            continue

        y_test = pd.to_numeric(te[target], errors="coerce").to_numpy(dtype=float)
        ok_y = np.isfinite(y_test) & (y_test >= 0.0)
        if not ok_y.any():
            continue
        te = te.loc[ok_y].copy()
        y_test = y_test[ok_y]

        kmax = max(15, int(np.ceil(float(y_test.max()))))
        try:
            model = ST.SogStructuralModel(kmax=kmax).fit(tr, target=target)
        except ValueError:
            continue

        folds += 1
        pmfs = model.predict_pmf(te, kmax=kmax)
        mu_pred = model.predict_mu(te)

        y_int = np.clip(np.rint(y_test).astype(int), 0, kmax)
        p_y = np.clip(pmfs[np.arange(len(y_int)), y_int], 1e-12, 1.0)
        ll_parts.append(np.log(p_y))
        ae_parts.append(np.abs(y_test - mu_pred))

        k_floor = int(np.floor(MAIN_LINE))  # 2 -> P(Y >= 3)
        p_over = np.clip(pmfs[:, k_floor + 1:].sum(axis=1), 1e-6, 1.0 - 1e-6)
        y_over = (y_test > MAIN_LINE).astype(float)
        brier_parts.append((p_over - y_over) ** 2)
        ll_2p5_parts.append(-(y_over * np.log(p_over) + (1.0 - y_over) * np.log(1.0 - p_over)))
        p_over_parts.append(p_over)
        y_over_parts.append(y_over)

    if folds == 0 or not ll_parts:
        return {
            "engine": ST.ENGINE_LABEL,
            "test_from": str(test_from),
            "step_months": int(step_months),
            "n": 0,
            "folds": 0,
            "mean_ll": None,
            "mae": None,
            "brier_2p5": None,
            "ll_2p5": None,
        }

    ll_all = np.concatenate(ll_parts)
    ae_all = np.concatenate(ae_parts)
    brier_all = np.concatenate(brier_parts)
    ll_2p5_all = np.concatenate(ll_2p5_parts)
    p_over_all = np.concatenate(p_over_parts)
    y_over_all = np.concatenate(y_over_parts)

    return {
        "engine": ST.ENGINE_LABEL,
        "test_from": str(test_from),
        "step_months": int(step_months),
        "n": int(len(ll_all)),
        "folds": int(folds),
        "mean_ll": float(np.mean(ll_all)),
        "mae": float(np.mean(ae_all)),
        "brier_2p5": float(np.mean(brier_all)),
        "ll_2p5": float(np.mean(ll_2p5_all)),
        "mean_p_over_2p5": float(np.mean(p_over_all)),
        "actual_over_rate_2p5": float(np.mean(y_over_all)),
    }


def build_sog_archive_frame(data_dir: str) -> pd.DataFrame:
    """Build SOG feature frame via `sog_trained.build_sog_features` and join realized SOG labels."""
    m = D.load_masters(data_dir)
    frames = {
        "games": m["games"],
        "skater_box": m["skaters"],
        "goalie_box": m["goalies"],
    }
    feat = ST.build_sog_features(frames)
    label_cols = ["game_id", "player_id", "sog"]
    if "attempts" in m["skaters"].columns:
        label_cols.append("attempts")
    labels = m["skaters"][label_cols].drop_duplicates(["game_id", "player_id"])
    return F.join_labels(feat, labels, how="inner")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Walk-forward backtest for MOD-04 B3 NHL SOG shadow model.")
    ap.add_argument("--data-dir", default=None, help="Directory containing NHL/ master CSVs")
    ap.add_argument("--test-from", default="2022-10-01", help="Walk-forward evaluation start date (YYYY-MM-DD)")
    ap.add_argument("--step-months", type=int, default=3, help="Walk-forward refit cadence in months")
    ap.add_argument("--out", default=None, help="Optional JSON output file path")
    args = ap.parse_args(argv)

    if not args.data_dir or not Path(args.data_dir).is_dir():
        print(f"ERROR: missing or invalid --data-dir: {args.data_dir!r}", file=sys.stderr)
        return 1

    try:
        feat = build_sog_archive_frame(args.data_dir)
    except Exception as exc:
        print(f"ERROR: cannot load NHL SOG archive data: {exc}", file=sys.stderr)
        return 1

    result = run_backtest(feat, test_from=args.test_from, step_months=args.step_months)
    if result["n"] <= 0:
        print(
            f"ERROR: walk-forward produced 0 scored rows for test_from={args.test_from} "
            "(insufficient archive history before test_from)",
            file=sys.stderr,
        )
        return 1

    payload = json.dumps(result, indent=2)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
