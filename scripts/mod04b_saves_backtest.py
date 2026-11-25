"""Walk-forward backtest and kill-criterion verdict runner for MOD-04 B2 saves shadow engine.

Evaluates:
  1. Alpha(x) covariate-dependent dispersion vs fixed global dispersion on `SAVES_FEATURES_NOXG`.
  2. xG feature ablation (`SAVES_FEATURES_NOXG + XG_ABLATION_FEATURES` vs `SAVES_FEATURES_NOXG`).
  3. `DampedRegimeMonitor` walk-forward over strictly prior weekly settled batches.

SHADOW ONLY: never modifies serving weights or live state.
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

from nhl.props import saves_sota as SS
from nhl.props import xgfeat as X
from nhl.xg import data as D


def monitor_walk_forward(rows: pd.DataFrame) -> dict[str, Any]:
    """Evaluate `DampedRegimeMonitor(enabled=True)` vs `enabled=False` on strictly prior weekly batches.

    Scored rows are sorted chronologically by `date` and grouped into weekly batches (`to_period('W')`).
    Each batch is scored BEFORE updating the monitors, guaranteeing strictly prior settled rows only.
    """
    if rows is None or rows.empty:
        return {
            "n": 0,
            "n_batches": 0,
            "ll_monitor_on": None,
            "ll_monitor_off": None,
            "delta_ll_monitor": None,
            "final_offset": 0.0,
            "final_shadow_offset": 0.0,
        }

    df = rows.copy()
    if "variant" in df.columns and "fixed" in set(df["variant"].astype(str)):
        df = df[df["variant"] == "fixed"].copy()

    y_col = "y" if "y" in df.columns else ("saves" if "saves" in df.columns else None)
    alpha_col = "alpha_fixed" if "alpha_fixed" in df.columns else ("alpha" if "alpha" in df.columns else None)
    if y_col is None or "mu" not in df.columns or "date" not in df.columns:
        raise ValueError("rows must contain 'date', 'mu', and 'y' (or 'saves')")

    df["_y"] = pd.to_numeric(df[y_col], errors="coerce")
    df["_mu"] = pd.to_numeric(df["mu"], errors="coerce")
    if alpha_col is not None:
        df["_alpha"] = pd.to_numeric(df[alpha_col], errors="coerce").fillna(0.05)
    else:
        df["_alpha"] = 0.05
    df["_dt"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["_y"].notna() & df["_mu"].notna() & df["_dt"].notna()].copy()

    if df.empty:
        return {
            "n": 0,
            "n_batches": 0,
            "ll_monitor_on": None,
            "ll_monitor_off": None,
            "delta_ll_monitor": None,
            "final_offset": 0.0,
            "final_shadow_offset": 0.0,
        }

    sort_cols = ["_dt"] + [c for c in ("game_id", "player_id") if c in df.columns]
    df = df.sort_values(sort_cols).reset_index(drop=True)
    df["_batch"] = df["_dt"].dt.to_period("W")

    mon_on = SS.DampedRegimeMonitor(enabled=True)
    mon_off = SS.DampedRegimeMonitor(enabled=False)

    ll_on_parts: list[np.ndarray] = []
    ll_off_parts: list[np.ndarray] = []
    n_batches = 0

    for _, batch in df.groupby("_batch", sort=True):
        n_batches += 1
        y_arr = batch["_y"].to_numpy(dtype=float)
        mu_arr = batch["_mu"].to_numpy(dtype=float)
        alpha_arr = batch["_alpha"].to_numpy(dtype=float)

        mu_on = mon_on.apply(mu_arr)
        mu_off = mon_off.apply(mu_arr)

        ll_on_parts.append(SS._row_nb_loglik(y_arr, mu_on, alpha_arr))
        ll_off_parts.append(SS._row_nb_loglik(y_arr, mu_off, alpha_arr))

        settled = pd.DataFrame({"y": y_arr, "mu": mu_arr})
        mon_on.update(settled)
        mon_off.update(settled)

    ll_on_all = np.concatenate(ll_on_parts)
    ll_off_all = np.concatenate(ll_off_parts)
    mean_on = float(np.mean(ll_on_all))
    mean_off = float(np.mean(ll_off_all))

    return {
        "n": int(len(df)),
        "n_batches": int(n_batches),
        "ll_monitor_on": mean_on,
        "ll_monitor_off": mean_off,
        "delta_ll_monitor": float(mean_on - mean_off),
        "final_offset": float(mon_on.offset),
        "final_shadow_offset": float(mon_on.shadow_offset),
    }


def run_backtest(
    feat: pd.DataFrame,
    test_from: str,
    step_months: int = 3,
) -> dict[str, Any]:
    """Run walk-forward saves evaluation + monitor evaluation and emit kill-criterion verdicts."""
    features_fixed = list(X.SAVES_FEATURES_NOXG)
    features_xg = list(X.SAVES_FEATURES_NOXG) + list(SS.XG_ABLATION_FEATURES)

    scored_df, summary = SS.walk_forward_compare(
        feat,
        features_fixed=features_fixed,
        features_xg=features_xg,
        test_from=test_from,
        step_months=int(step_months),
    )

    fixed_stats = summary.get("fixed", {"n": 0})
    xg_stats = summary.get("xg", {"n": 0})
    n_fixed = int(fixed_stats.get("n", 0))

    fixed_rows = scored_df[scored_df["variant"] == "fixed"].copy() if not scored_df.empty else pd.DataFrame()
    mon_res = monitor_walk_forward(fixed_rows)

    if n_fixed > 0:
        mean_ll_fixed = float(fixed_stats["mean_ll_fixed"])
        mean_ll_x = float(fixed_stats["mean_ll_x"])
        mean_brier_tail_fixed = float(fixed_stats["mean_brier_tail_fixed"])
        mean_brier_tail_x = float(fixed_stats["mean_brier_tail_x"])
        alpha_x_verdict = (
            "KEEP_ALPHA_X"
            if (mean_ll_x > mean_ll_fixed and mean_brier_tail_x < mean_brier_tail_fixed)
            else "REVERT_TO_FIXED"
        )
    else:
        mean_ll_fixed = None
        mean_ll_x = None
        mean_brier_tail_fixed = None
        mean_brier_tail_x = None
        alpha_x_verdict = "REVERT_TO_FIXED"

    delta_ll_xg = summary.get("delta_ll_xg_vs_fixed")
    if delta_ll_xg is not None:
        delta_ll_xg = float(delta_ll_xg)
        xgfeat_verdict = "KEEP_NOXG" if delta_ll_xg <= 0.0 else "KEEP_XG"
    else:
        xgfeat_verdict = "KEEP_NOXG"

    delta_mon = mon_res.get("delta_ll_monitor")
    if delta_mon is not None and float(delta_mon) > 0.0:
        monitor_verdict = "ENABLE_MONITOR"
    else:
        monitor_verdict = "KEEP_DISABLED"

    return {
        "engine": SS.ENGINE_LABEL,
        "test_from": str(test_from),
        "step_months": int(step_months),
        "n": n_fixed,
        "alpha_x_verdict": alpha_x_verdict,
        "xgfeat_verdict": xgfeat_verdict,
        "monitor_verdict": monitor_verdict,
        "verdicts": {
            "alpha_x": alpha_x_verdict,
            "xgfeat": xgfeat_verdict,
            "monitor": monitor_verdict,
        },
        "mean_ll_fixed": mean_ll_fixed,
        "mean_ll_x": mean_ll_x,
        "mean_brier_tail_fixed": mean_brier_tail_fixed,
        "mean_brier_tail_x": mean_brier_tail_x,
        "delta_ll_xg_vs_fixed": delta_ll_xg,
        "delta_ll_alphax_vs_fixed_on_fixed": summary.get("delta_ll_alphax_vs_fixed_on_fixed"),
        "delta_ll_alphax_vs_fixed_on_xg": summary.get("delta_ll_alphax_vs_fixed_on_xg"),
        "ll_monitor_on": mon_res["ll_monitor_on"],
        "ll_monitor_off": mon_res["ll_monitor_off"],
        "delta_ll_monitor": mon_res["delta_ll_monitor"],
        "fixed": fixed_stats,
        "xg": xg_stats,
        "monitor": mon_res,
    }


def build_saves_archive_frame(data_dir: str, xg_dir: str) -> pd.DataFrame:
    """Build saves archive feature frame using the `eval_xg.build_frames` pattern."""
    m = D.load_masters(data_dir)
    xgl = pd.read_parquet(Path(xg_dir) / "xg_goalie_game.parquet")
    xt = pd.read_parquet(Path(xg_dir) / "xg_team_game.parquet")
    tt = X.team_table(m["games"], xt)
    gt = X.goalie_table(m["games"], m["goalies"], xgl)
    return X.saves_features(gt, tt)


def _validate_inputs(data_dir: str | None, xg_dir: str | None) -> tuple[str, str]:
    if not data_dir:
        raise FileNotFoundError("--data-dir is required and cannot be empty")
    if not xg_dir:
        raise FileNotFoundError("--xg-dir is required and cannot be empty")
    dpath = Path(data_dir)
    xpath = Path(xg_dir)
    if not dpath.is_dir():
        raise FileNotFoundError(f"data directory does not exist: {dpath}")
    if not xpath.is_dir():
        raise FileNotFoundError(f"xG directory does not exist: {xpath}")
    for fname in ("xg_goalie_game.parquet", "xg_team_game.parquet"):
        if not (xpath / fname).exists():
            raise FileNotFoundError(f"missing required xG parquet: {xpath / fname}")
    return str(dpath), str(xpath)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Walk-forward backtest for MOD-04 B2 saves shadow model.")
    ap.add_argument("--data-dir", default=None, help="Directory containing NHL/ master CSVs")
    ap.add_argument("--xg-dir", default=None, help="Directory containing xg_goalie_game.parquet and xg_team_game.parquet")
    ap.add_argument("--test-from", default="2022-10-01", help="Walk-forward evaluation start date (YYYY-MM-DD)")
    ap.add_argument("--step-months", type=int, default=3, help="Walk-forward refit cadence in months")
    ap.add_argument("--out", default=None, help="Optional JSON output file path")
    args = ap.parse_args(argv)

    try:
        data_dir, xg_dir = _validate_inputs(args.data_dir, args.xg_dir)
        feat = build_saves_archive_frame(data_dir, xg_dir)
    except Exception as exc:
        print(f"ERROR: cannot load saves archive inputs: {exc}", file=sys.stderr)
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
