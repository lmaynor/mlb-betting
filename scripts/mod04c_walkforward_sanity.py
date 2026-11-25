"""Offline walk-forward sanity check for MOD-04c NBA & NHL assist heads (NOT the gate)."""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd
from scipy.special import logit
from sklearn.linear_model import LogisticRegression

from mlb_core import storage
from mod04c import nba_assists, nhl_assists
from mod04c.dist_negbin import PROB_CLIP, nb_pmf, pmf_p_over
from nba.config import PLAYER_BOX_MASTER as NBA_PLAYER_BOX_MASTER
from nhl.config import SKATER_BOX_MASTER as NHL_SKATER_MASTER


def _select_fold_dates(df: pd.DataFrame, n_folds: int) -> list[str]:
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    counts = d.groupby("date").size()
    eligible = counts[counts >= 40].index.sort_values().tolist()
    if len(eligible) <= n_folds:
        eligible = counts.index.sort_values().tolist()
    # Leave at least the first half of dates for prior history
    tail = eligible[max(1, len(eligible) // 2):]
    if not tail:
        return []
    step = max(1, len(tail) // max(n_folds, 1))
    return tail[::step][:n_folds]


def _nba_implied_slope(rows: pd.DataFrame, history: pd.DataFrame) -> float | None:
    if rows.empty:
        return None
    h = history.copy()
    h["date"] = pd.to_datetime(h["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    h = h.sort_values(["player_id", "date"], kind="mergesort")
    lines = []
    for r in rows.itertuples(index=False):
        prior_p = h[(h["player_id"] == r.player_id) & (h["date"] < r.date)].tail(15)
        med = float(prior_p["assists"].median()) if not prior_p.empty else 0.0
        lines.append(max(0.5, np.floor(med) + 0.5))
    lines_arr = np.asarray(lines, dtype=float)
    pmf = nb_pmf(rows["mu"].to_numpy(float), rows["alpha"].to_numpy(float), kmax=40)
    p = np.clip(pmf_p_over(pmf, lines_arr), PROB_CLIP, 1.0 - PROB_CLIP)
    y_bin = (rows["y"].to_numpy(float) > lines_arr).astype(int)
    if len(np.unique(y_bin)) < 2:
        return None
    lr = LogisticRegression(C=1e6, max_iter=1000)
    lr.fit(logit(p).reshape(-1, 1), y_bin)
    return float(lr.coef_[0][0])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Walk-forward sanity check for MOD-04c playmaking heads.")
    ap.add_argument("--sport", required=True, choices=("nba", "nhl"))
    ap.add_argument("--folds", type=int, default=6)
    args = ap.parse_args(argv)

    if args.sport == "nba":
        box = storage.read_csv(NBA_PLAYER_BOX_MASTER)
        if "season_type" in box.columns:
            box = box[box["season_type"] != "Preseason"].copy()
        folds = _select_fold_dates(box, args.folds)
        res = nba_assists.walk_forward(box, folds)
        slope = _nba_implied_slope(res["rows"], box)
        summary = {
            "check_type": "SANITY — not the gate",
            "sport": "nba",
            "folds": res["folds"],
            "n_rows": res["n_rows"],
            "model_nb_ll": res["model_nb_ll"],
            "eb_nb_ll": res["eb_nb_ll"],
            "unified_nb_ll": res["unified_nb_ll"],
            "implied_calibration_slope_vs_trailing_median": slope,
        }
    else:
        sk = storage.read_csv(NHL_SKATER_MASTER)
        sp = storage.read_csv("NHL/assist_split/assist_split.csv")
        merged = sk.merge(
            sp[["game_id", "player_id", "primary_assists", "secondary_assists"]],
            on=["game_id", "player_id"],
            how="left",
        )
        merged["primary_assists"] = merged["primary_assists"].fillna(0)
        merged["secondary_assists"] = merged["secondary_assists"].fillna(0)
        folds = _select_fold_dates(merged, args.folds)
        res = nhl_assists.walk_forward_compare(merged, folds)
        summary = {
            "check_type": "SANITY — not the gate",
            "sport": "nhl",
            "folds": res["folds"],
            "n_rows": res["n_rows"],
            "split_ll": res["split_ll"],
            "unified_ll": res["unified_ll"],
            "primary_ll": res["primary_ll"],
            "secondary_ll": res["secondary_ll"],
            "recommendation": res["recommendation"],
        }

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
