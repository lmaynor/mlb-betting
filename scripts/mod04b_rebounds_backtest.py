"""Walk-forward backtest for MOD-04 B4 NBA rebounds decomposition shadow model (`nba_rebounds_decomp_shadow_v1`).

SHADOW ONLY: evaluates `ReboundsDecompModel` via expanding walk-forward folds with strictly
prior training slices (`date < fold_start`), computing NegBin count log-likelihood, MAE, and
line 5.5 calibration (Brier score and binary log-loss).
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

from nba.props import config as C
from nba.props import data as ND
from nba.props import rebounds_decomp as RD
from nhl.props import mod04_features as F

MAIN_LINE = 5.5


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


def derive_team_box_from_player_box(
    player_box: pd.DataFrame,
    games: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Derive a per-(game_id, team) `team_box` table by summing player counting stats.

    `nba.props.data.load_masters` returns `(player_box, games)` and notes that historical
    SportsBlaze `team_boxscores_master.csv` rows have NaN rebounds/turnovers for 2019-2024,
    while player box totals sum cleanly to team totals. When `team_box` is unavailable, this
    helper aggregates `player_box` per `(game_id, team)` summing `rebounds`, `rebounds_offensive`,
    `rebounds_defensive`, field goals (2PT/3PT/total), free throws, turnovers, and parsed minutes,
    and attaches `opp` / `is_home` from `games` (or paired team rows).
    """
    if player_box is None or player_box.empty:
        return pd.DataFrame()

    pb = player_box.copy()
    if "team" in pb.columns:
        pb["_team"] = pb["team"].map(lambda x: ND.sb_team(x) or str(x).strip().upper())
    elif "team_abbr" in pb.columns:
        pb["_team"] = pb["team_abbr"].map(lambda x: ND.sb_team(x) or str(x).strip().upper())
    else:
        raise KeyError("player_box must contain 'team' or 'team_abbr'")

    if "minutes" in pb.columns:
        if pb["minutes"].dtype == object or pd.api.types.is_string_dtype(pb["minutes"]):
            pb["_min_num"] = ND.parse_minutes(pb["minutes"]).fillna(0.0)
        else:
            pb["_min_num"] = pd.to_numeric(pb["minutes"], errors="coerce").fillna(0.0)
    else:
        pb["_min_num"] = 0.0

    sum_cols = [
        "rebounds",
        "rebounds_offensive",
        "rebounds_defensive",
        "field_goals_attempted",
        "field_goals_made",
        "two_points_attempted",
        "two_points_made",
        "three_points_attempted",
        "three_points_made",
        "free_throws_attempted",
        "free_throws_made",
        "turnovers",
    ]
    agg_spec: dict[str, tuple[str, str]] = {"minutes": ("_min_num", "sum")}
    for col in sum_cols:
        if col in pb.columns:
            pb[col] = pd.to_numeric(pb[col], errors="coerce").fillna(0.0)
            agg_spec[col] = (col, "sum")
    for meta_col in ("date", "season_year", "season_type", "is_home", "team_id"):
        if meta_col in pb.columns:
            agg_spec[meta_col] = (meta_col, "first")

    tb = pb.groupby(["game_id", "_team"], as_index=False).agg(**agg_spec)
    tb = tb.rename(columns={"_team": "team"})

    if "rebounds_defensive" not in tb.columns and "rebounds" in tb.columns and "rebounds_offensive" in tb.columns:
        tb["rebounds_defensive"] = (tb["rebounds"] - tb["rebounds_offensive"]).clip(lower=0.0)

    if games is not None and not games.empty and "game_id" in games.columns:
        g = games.copy()
        if "home_abbr" in g.columns and "away_abbr" in g.columns:
            g["_home"] = g["home_abbr"].map(lambda x: ND.sb_team(x) or str(x).strip().upper())
            g["_away"] = g["away_abbr"].map(lambda x: ND.sb_team(x) or str(x).strip().upper())
            home_rows = pd.DataFrame({
                "game_id": g["game_id"],
                "team": g["_home"],
                "opp_from_g": g["_away"],
                "is_home_from_g": True,
                "date_from_g": g["date"] if "date" in g.columns else np.nan,
            })
            away_rows = pd.DataFrame({
                "game_id": g["game_id"],
                "team": g["_away"],
                "opp_from_g": g["_home"],
                "is_home_from_g": False,
                "date_from_g": g["date"] if "date" in g.columns else np.nan,
            })
            g_map = pd.concat([home_rows, away_rows], ignore_index=True).drop_duplicates(["game_id", "team"])
            tb = tb.merge(g_map, on=["game_id", "team"], how="left")
            tb["opp"] = tb["opp_from_g"]
            if "is_home" not in tb.columns:
                tb["is_home"] = tb["is_home_from_g"].fillna(False)
            else:
                tb["is_home"] = tb["is_home"].fillna(tb["is_home_from_g"]).fillna(False)
            if "date" not in tb.columns:
                tb["date"] = tb["date_from_g"]
            else:
                tb["date"] = tb["date"].fillna(tb["date_from_g"])
            tb = tb.drop(columns=["opp_from_g", "is_home_from_g", "date_from_g"])

    if "opp" not in tb.columns or tb["opp"].isna().all():
        pairs = tb[["game_id", "team"]].drop_duplicates()
        opp_map = pairs.merge(pairs, on="game_id", suffixes=("", "_opp"))
        opp_map = opp_map[opp_map["team"] != opp_map["team_opp"]].drop_duplicates(["game_id", "team"])
        tb = tb.merge(opp_map.rename(columns={"team_opp": "opp"}), on=["game_id", "team"], how="left")

    if "is_home" not in tb.columns:
        tb["is_home"] = False
    return tb


def run_backtest(
    feat: pd.DataFrame,
    test_from: str,
    step_months: int = 3,
    target: str = "rebounds",
) -> dict[str, Any]:
    """Run expanding walk-forward backtest for `ReboundsDecompModel` from `test_from`."""
    if feat is None or feat.empty:
        return {
            "engine": RD.ENGINE_LABEL,
            "test_from": str(test_from),
            "step_months": int(step_months),
            "n": 0,
            "folds": 0,
            "mean_ll": None,
            "mae": None,
            "brier_5p5": None,
            "ll_5p5": None,
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
    ll_5p5_parts: list[np.ndarray] = []
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

        kmax = max(25, int(np.ceil(float(y_test.max()))))
        try:
            model = RD.ReboundsDecompModel(kmax=kmax).fit(tr, target=target)
        except ValueError:
            continue

        folds += 1
        pmfs = model.predict_pmf(te, kmax=kmax)
        mu_pred = model.predict_mu(te)

        y_int = np.clip(np.rint(y_test).astype(int), 0, kmax)
        p_y = np.clip(pmfs[np.arange(len(y_int)), y_int], 1e-12, 1.0)
        ll_parts.append(np.log(p_y))
        ae_parts.append(np.abs(y_test - mu_pred))

        k_floor = int(np.floor(MAIN_LINE))  # 5 -> P(Y >= 6)
        p_over = np.clip(pmfs[:, k_floor + 1:].sum(axis=1), 1e-6, 1.0 - 1e-6)
        y_over = (y_test > MAIN_LINE).astype(float)
        brier_parts.append((p_over - y_over) ** 2)
        ll_5p5_parts.append(-(y_over * np.log(p_over) + (1.0 - y_over) * np.log(1.0 - p_over)))
        p_over_parts.append(p_over)
        y_over_parts.append(y_over)

    if folds == 0 or not ll_parts:
        return {
            "engine": RD.ENGINE_LABEL,
            "test_from": str(test_from),
            "step_months": int(step_months),
            "n": 0,
            "folds": 0,
            "mean_ll": None,
            "mae": None,
            "brier_5p5": None,
            "ll_5p5": None,
        }

    ll_all = np.concatenate(ll_parts)
    ae_all = np.concatenate(ae_parts)
    brier_all = np.concatenate(brier_parts)
    ll_5p5_all = np.concatenate(ll_5p5_parts)
    p_over_all = np.concatenate(p_over_parts)
    y_over_all = np.concatenate(y_over_parts)

    return {
        "engine": RD.ENGINE_LABEL,
        "test_from": str(test_from),
        "step_months": int(step_months),
        "n": int(len(ll_all)),
        "folds": int(folds),
        "mean_ll": float(np.mean(ll_all)),
        "mae": float(np.mean(ae_all)),
        "brier_5p5": float(np.mean(brier_all)),
        "ll_5p5": float(np.mean(ll_5p5_all)),
        "mean_p_over_5p5": float(np.mean(p_over_all)),
        "actual_over_rate_5p5": float(np.mean(y_over_all)),
    }


def build_rebounds_archive_frame(data_dir: str) -> pd.DataFrame:
    """Build NBA rebounds decomposition features from local masters and join realized rebound labels."""
    player_box, games = ND.load_masters(data_dir)
    if "season_type" in player_box.columns:
        player_box = player_box[~player_box["season_type"].isin(C.EXCLUDE_SEASON_TYPES)].copy()
    if "season_type" in games.columns:
        games = games[~games["season_type"].isin(C.EXCLUDE_SEASON_TYPES)].copy()

    tbox_path = Path(data_dir) / "team_boxscores_master.csv"
    if tbox_path.exists():
        tb_raw = pd.read_csv(tbox_path, low_memory=False)
        has_stats = (
            "rebounds_offensive" in tb_raw.columns
            and pd.to_numeric(tb_raw["rebounds_offensive"], errors="coerce").notna().all()
            and "field_goals_attempted" in tb_raw.columns
        )
        team_box = tb_raw if has_stats else derive_team_box_from_player_box(player_box, games)
    else:
        team_box = derive_team_box_from_player_box(player_box, games)

    feat = RD.build_rebound_features({"player_box": player_box, "team_box": team_box})
    label_cols = ["game_id", "player_id", "rebounds"]
    for extra in ("rebounds_offensive", "rebounds_defensive"):
        if extra in player_box.columns:
            label_cols.append(extra)
    labels = player_box[label_cols].drop_duplicates(["game_id", "player_id"])
    return F.join_labels(feat, labels, how="inner")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Walk-forward backtest for MOD-04 B4 NBA rebounds shadow model.")
    ap.add_argument("--data-dir", default=None, help="Directory containing NBA player_boxscores_master.csv and games_master.csv")
    ap.add_argument("--test-from", default="2022-10-01", help="Walk-forward evaluation start date (YYYY-MM-DD)")
    ap.add_argument("--step-months", type=int, default=3, help="Walk-forward refit cadence in months")
    ap.add_argument("--out", default=None, help="Optional JSON output file path")
    args = ap.parse_args(argv)

    if not args.data_dir or not Path(args.data_dir).is_dir():
        print(f"ERROR: missing or invalid --data-dir: {args.data_dir!r}", file=sys.stderr)
        return 1

    dpath = Path(args.data_dir)
    for fname in ("player_boxscores_master.csv", "games_master.csv"):
        if not (dpath / fname).exists():
            print(f"ERROR: missing required NBA master file: {dpath / fname}", file=sys.stderr)
            return 1

    try:
        feat = build_rebounds_archive_frame(args.data_dir)
    except Exception as exc:
        print(f"ERROR: cannot load NBA rebounds archive data: {exc}", file=sys.stderr)
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
