"""nba.minutes_restriction.grade -- Arm S against the pre-registered bars (spike section 4).

Bars (evaluated on the POOLED test seasons, primary population = non-game-2 cases; game-2 is a
supplementary cohort, reported but never part of a bar):

  1. missed >= 4 injury cohort: Arm S MAE must cut naive MAE by >= 15% (relative).
  2. full population:           Arm S MAE must cut naive MAE by >= 10% (relative).
  3. calibration:               within each missed band (1, 2-3, 4-9, 10+; band 0 reported too),
                                |mean predicted ratio - mean realized ratio| <= 0.05.

A bar with no cases (or a zero naive MAE) FAILS closed with observed=None: missing evidence is
not a pass. Boundary values pass: exactly 15.0% clears bar 1 (>=, with a 1e-9 float guard).
The report is a JSON-able dict; render_markdown turns it into the human-readable grading sheet.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BAR_MISSED_GE4_CUT = 0.15
BAR_FULL_CUT = 0.10
CALIBRATION_TOL = 0.05
_EPS = 1e-9
BAND_ORDER = ("0", "1", "2-3", "4-9", "10+")


def _mae(realized: np.ndarray, predicted: np.ndarray) -> float | None:
    if len(realized) == 0:
        return None
    return float(np.mean(np.abs(realized - predicted)))


def cohort_stats(df: pd.DataFrame) -> dict:
    """Naive vs Arm S MAE (minutes) and the relative improvement for one case cohort."""
    if df is None or not len(df):
        return {"n": 0, "naive_mae": None, "arm_s_mae": None, "rel_improvement": None}
    realized = pd.to_numeric(df["realized_minutes"], errors="coerce").to_numpy(dtype=float)
    naive = pd.to_numeric(df["naive_minutes"], errors="coerce").to_numpy(dtype=float)
    pred = pd.to_numeric(df["pred_minutes"], errors="coerce").to_numpy(dtype=float)
    ok = np.isfinite(realized) & np.isfinite(naive) & np.isfinite(pred)
    naive_mae = _mae(realized[ok], naive[ok])
    arm_mae = _mae(realized[ok], pred[ok])
    rel = None
    if naive_mae is not None and naive_mae > 0 and arm_mae is not None:
        rel = float((naive_mae - arm_mae) / naive_mae)
    return {"n": int(ok.sum()), "naive_mae": naive_mae, "arm_s_mae": arm_mae,
            "rel_improvement": rel}


def calibration(df: pd.DataFrame) -> dict:
    """Per missed band: mean predicted vs realized ratio, the gap, and the > tol flag."""
    out: dict = {}
    if df is None or not len(df):
        return out
    for band in BAND_ORDER:
        sub = df[df["missed_band"] == band]
        if not len(sub):
            continue
        pred = pd.to_numeric(sub["pred_ratio"], errors="coerce")
        real = pd.to_numeric(sub["realized_ratio"], errors="coerce")
        ok = pred.notna() & real.notna()
        if not ok.any():
            continue
        mp, mr = float(pred[ok].mean()), float(real[ok].mean())
        out[band] = {"n": int(ok.sum()), "mean_pred_ratio": mp, "mean_realized_ratio": mr,
                     "diff": mp - mr, "flagged": bool(abs(mp - mr) > CALIBRATION_TOL + _EPS)}
    return out


def _missed_ge4_injury(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["games_missed"] >= 4) & (df["is_injury_return"].astype(bool))]


def _season_block(df: pd.DataFrame) -> dict:
    primary = df[~df["is_game_two"].astype(bool)]
    return {
        "full": cohort_stats(primary),
        "missed_ge4_injury": cohort_stats(_missed_ge4_injury(primary)),
        "game_two_supplementary": cohort_stats(df[df["is_game_two"].astype(bool)]),
        "calibration": calibration(primary),
    }


def grade(predictions: pd.DataFrame) -> dict:
    """Predictions (arm_s.walk_forward output) -> the grading report dict with per-bar verdicts."""
    if predictions is None:
        predictions = pd.DataFrame(columns=["season_start", "season_label", "is_game_two",
                                            "games_missed", "is_injury_return", "missed_band",
                                            "realized_minutes", "naive_minutes", "pred_minutes",
                                            "pred_ratio", "realized_ratio"])
    seasons: dict = {}
    if len(predictions):
        for (start, label), sub in predictions.groupby(["season_start", "season_label"], sort=True):
            seasons[str(label)] = _season_block(sub)
    pooled = _season_block(predictions) if len(predictions) else {
        "full": cohort_stats(predictions), "missed_ge4_injury": cohort_stats(predictions),
        "game_two_supplementary": cohort_stats(predictions), "calibration": {}}

    rel_ge4 = pooled["missed_ge4_injury"]["rel_improvement"]
    rel_full = pooled["full"]["rel_improvement"]
    calib = pooled["calibration"]
    calib_pass = bool(calib) and all(not b["flagged"] for b in calib.values())
    bars = {
        "missed_ge4_injury_mae_cut": {
            "required": BAR_MISSED_GE4_CUT, "observed": rel_ge4,
            "pass": bool(rel_ge4 is not None and rel_ge4 >= BAR_MISSED_GE4_CUT - _EPS)},
        "full_population_mae_cut": {
            "required": BAR_FULL_CUT, "observed": rel_full,
            "pass": bool(rel_full is not None and rel_full >= BAR_FULL_CUT - _EPS)},
        "calibration": {
            "tolerance": CALIBRATION_TOL,
            "max_abs_diff": max((abs(b["diff"]) for b in calib.values()), default=None),
            "pass": calib_pass},
    }
    return {
        "seasons": seasons,
        "pooled": pooled,
        "bars": bars,
        "verdict": "PASS" if all(b["pass"] for b in bars.values()) else "FAIL",
        "fit_seasons": predictions.attrs.get("fit_seasons"),
        "n_train": predictions.attrs.get("n_train"),
        "used_fallback": predictions.attrs.get("used_fallback"),
    }


# -----------------------------------------------------------------------------------------------
# Markdown rendering

def _fmt(value, digits=3):
    return "n/a" if value is None or (isinstance(value, float) and not np.isfinite(value)) \
        else f"{value:.{digits}f}"


def _pct(value):
    return "n/a" if value is None else f"{100.0 * value:.1f}%"


def _cohort_lines(title: str, stats: dict) -> list:
    return [f"| {title} | {stats['n']} | {_fmt(stats['naive_mae'], 2)} | "
            f"{_fmt(stats['arm_s_mae'], 2)} | {_pct(stats['rel_improvement'])} |"]


def render_markdown(report: dict) -> str:
    """Grading report dict -> markdown sheet (verdict first, Minto order)."""
    bars = report["bars"]
    lines = [
        "# Arm S minutes-restriction backtest -- grading",
        "",
        f"**Verdict: {report['verdict']}**",
        "",
        f"Fit seasons (season_start): {report.get('fit_seasons')} "
        f"(n_train={report.get('n_train')}, mean-ratio fallback={report.get('used_fallback')})",
        "",
        "## Bars (pooled test seasons)",
        "",
        "| Bar | Required | Observed | Result |",
        "|---|---|---|---|",
        f"| Missed >= 4 injury MAE cut | >= {_pct(bars['missed_ge4_injury_mae_cut']['required'])} "
        f"| {_pct(bars['missed_ge4_injury_mae_cut']['observed'])} "
        f"| {'PASS' if bars['missed_ge4_injury_mae_cut']['pass'] else 'FAIL'} |",
        f"| Full population MAE cut | >= {_pct(bars['full_population_mae_cut']['required'])} "
        f"| {_pct(bars['full_population_mae_cut']['observed'])} "
        f"| {'PASS' if bars['full_population_mae_cut']['pass'] else 'FAIL'} |",
        f"| Calibration (max band |diff| <= {bars['calibration']['tolerance']}) | "
        f"<= {bars['calibration']['tolerance']} | {_fmt(bars['calibration']['max_abs_diff'])} "
        f"| {'PASS' if bars['calibration']['pass'] else 'FAIL'} |",
        "",
    ]
    blocks = [(f"Test season {label}", block) for label, block in sorted(report["seasons"].items())]
    blocks.append(("Pooled test seasons", report["pooled"]))
    for title, block in blocks:
        lines += [f"## {title}", "",
                  "| Cohort | n | Naive MAE (min) | Arm S MAE (min) | Improvement |",
                  "|---|---|---|---|---|"]
        lines += _cohort_lines("Full population (primary)", block["full"])
        lines += _cohort_lines("Missed >= 4 injury", block["missed_ge4_injury"])
        lines += _cohort_lines("Game-2 (supplementary, not barred)", block["game_two_supplementary"])
        lines += ["", "| Missed band | n | Mean predicted ratio | Mean realized ratio | Diff | Flag |",
                  "|---|---|---|---|---|---|"]
        for band in BAND_ORDER:
            if band in block["calibration"]:
                c = block["calibration"][band]
                lines.append(f"| {band} | {c['n']} | {_fmt(c['mean_pred_ratio'])} | "
                             f"{_fmt(c['mean_realized_ratio'])} | {_fmt(c['diff'])} | "
                             f"{'OFF' if c['flagged'] else 'ok'} |")
        lines.append("")
    return "\n".join(lines) + "\n"
