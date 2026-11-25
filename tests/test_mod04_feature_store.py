"""Tests for mod04.feature_store — U1 time-travel, cutoff, schema gate, and Label Store isolation."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mod04.feature_store import (
    FEATURE_COLUMNS,
    assert_feature_schema,
    build_pregame_features,
    load_labels,
)


def _write_fixture_dataset(
    root: Path,
    include_future_box: bool = True,
    include_future_games: bool = True,
) -> None:
    nba = root / "NBA"
    nba.mkdir(parents=True, exist_ok=True)
    off = nba / "injuries" / "official" / "2026-11-20"
    off.mkdir(parents=True, exist_ok=True)
    arm_dir = nba / "minutes_restriction" / "arm_s_index"
    arm_dir.mkdir(parents=True, exist_ok=True)
    shots_dir = nba / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    # Prior games on Nov 10..Nov 18, target on Nov 20, future on Nov 22
    prior_dates = ["2026-11-10", "2026-11-12", "2026-11-15", "2026-11-18"]
    all_dates = prior_dates + ["2026-11-20", "2026-11-22"]
    box_dates = all_dates if include_future_box else prior_dates
    game_dates = all_dates if include_future_games else prior_dates

    players = [
        ("p1", "Alice Guard", "PG", "BOS", True, "32:00", 2, 0, 2),
        ("p2", "Bob Wing", "SF", "BOS", True, "28:00", 1, 1, 2),
        ("p3", "Carl Big", "C", "BOS", True, "30:00", 1, 2, 3),
        ("p4", "Dan Backup", "PG", "BOS", False, "18:00", 1, 0, 1),
        ("p5", "Evan Guard", "SG", "NY", True, "31:00", 2, 0, 2),
        ("p6", "Frank Big", "C", "NY", True, "29:00", 0, 2, 3),
    ]
    games_rows = []
    for i, d in enumerate(game_dates):
        gid = f"g_{i}"
        games_rows.append({
            "game_id": gid,
            "season_year": 2026,
            "season_type": "Regular Season",
            "date": d,
            "away_abbr": "NY",
            "home_abbr": "BOS",
            "away_points": (999 if d >= "2026-11-20" else 105 + i),
            "home_points": (999 if d >= "2026-11-20" else 110 + i),
        })

    rows = []
    for i, d in enumerate(box_dates):
        gid = f"g_{i}"
        # On future dates, inject extreme outlier steals/blocks to detect any leakage immediately
        mult = 99 if d >= "2026-11-20" else 1
        for pid, name, pos, team, st, mins, stl, blk, pf in players:
            rows.append({
                "game_id": gid,
                "season_year": 2026,
                "season_type": "Regular Season",
                "date": d,
                "team_abbr": team,
                "is_home": team == "BOS",
                "player_id": pid,
                "name": name,
                "position": pos,
                "starter": st,
                "played": True,
                "minutes": mins,
                "points": 15,
                "field_goals_attempted": 12,
                "free_throws_attempted": 3,
                "rebounds_offensive": 1,
                "rebounds": 5,
                "assists": 4,
                "steals": stl * mult,
                "blocks": blk * mult,
                "turnovers": 2 * mult,
                "fouls": pf,
            })

    pd.DataFrame(rows).to_csv(nba / "player_boxscores_master.csv", index=False)
    pd.DataFrame(games_rows).to_csv(nba / "games_master.csv", index=False)

    # Shots parquet (prior + future poisoned)
    shot_rows = []
    for d in box_dates:
        zone = 13 if d >= "2026-11-20" else 0
        for t, o in [("NY", "BOS"), ("BOS", "NY")]:
            for _ in range(10):
                shot_rows.append({
                    "date": d,
                    "team": t,
                    "opp": o,
                    "zone": zone,
                    "x": 0.5,
                    "y": 6.0 if zone == 0 else 28.0,
                })
    pd.DataFrame(shot_rows).to_parquet(shots_dir / "shots_2026.parquet", index=False)

    # Pre-cutoff official report (18:00 UTC <= 23:00 UTC cutoff) and post-cutoff report (23:30 UTC)
    pd.DataFrame([{
        "report_ts_et": "2026-11-20T13:00:00-05:00",
        "game_date": "11/20/2026",
        "team_code": "BOS",
        "player": "Alice Guard",
        "status": "Out",
    }]).to_csv(off / "report_1300.csv", index=False)

    pd.DataFrame([{
        "report_ts_et": "2026-11-20T18:30:00-05:00",
        "game_date": "11/20/2026",
        "team_code": "BOS",
        "player": "Bob Wing",
        "status": "Out",
    }]).to_csv(off / "report_1830.csv", index=False)

    # arm_s_index pre-cutoff cap on Carl Big
    pd.DataFrame([{
        "player": "Carl Big",
        "team": "BOS",
        "game_date": "2026-11-20",
        "pred_ratio": 0.70,
        "pred_minutes": 21.0,
        "is_injury_return": True,
        "decision_report_ts": "2026-11-20T21:00:00+00:00",
    }]).to_csv(arm_dir / "predictions_export.csv", index=False)


def test_ci_time_travel_byte_identical(tmp_path: Path):
    """Deleting ALL box-score rows on/after target_date must produce byte-identical features."""
    d_full = tmp_path / "full"
    d_trunc = tmp_path / "trunc"
    _write_fixture_dataset(d_full, include_future_box=True, include_future_games=True)
    _write_fixture_dataset(d_trunc, include_future_box=False, include_future_games=True)

    cutoff = "2026-11-20T23:00:00Z"
    f_full = build_pregame_features("2026-11-20", cutoff, d_full)
    f_trunc = build_pregame_features("2026-11-20", cutoff, d_trunc)

    assert len(f_full) == 6
    assert list(f_full.columns) == list(f_trunc.columns)
    for col in FEATURE_COLUMNS:
        assert col in f_full.columns
    pd.testing.assert_frame_equal(f_full, f_trunc, check_exact=True)
    assert f_full.to_json(orient="records") == f_trunc.to_json(orient="records")

    # Also verify when an explicit slate is supplied and ALL future games + boxscores are deleted
    d_trunc_all = tmp_path / "trunc_all"
    _write_fixture_dataset(d_trunc_all, include_future_box=False, include_future_games=False)
    slate = pd.DataFrame([
        {"game_id": "g_4", "player": "Alice Guard", "team": "BOS", "opp": "NY", "is_home": 1},
        {"game_id": "g_4", "player": "Evan Guard", "team": "NY", "opp": "BOS", "is_home": 0},
    ])
    s_full = build_pregame_features("2026-11-20", cutoff, d_full, slate=slate)
    s_trunc = build_pregame_features("2026-11-20", cutoff, d_trunc_all, slate=slate)
    pd.testing.assert_frame_equal(s_full, s_trunc, check_exact=True)


def test_injury_cutoff_and_arm_s_flag(tmp_path: Path):
    """Pre-cutoff Out is applied; post-cutoff Out is ignored; arm_s_index cap flags Carl Big."""
    _write_fixture_dataset(tmp_path, include_future_box=True)
    feat = build_pregame_features("2026-11-20", "2026-11-20T23:00:00Z", tmp_path)

    alice = feat[feat["player_id"] == "p1"].iloc[0]
    bob = feat[feat["player_id"] == "p2"].iloc[0]
    carl = feat[feat["player_id"] == "p3"].iloc[0]
    dan = feat[feat["player_id"] == "p4"].iloc[0]

    assert alice["is_player_out"] == 1
    assert alice["mu_min"] == 0.0
    assert bob["is_player_out"] == 0  # 18:30 ET == 23:30 UTC > 23:00 UTC cutoff!
    assert bob["mu_min"] > 0.0
    assert dan["absorbed_min_delta"] > 0.0
    assert carl["min_cap_flag"] == 1
    assert carl["mu_min"] <= 21.0 + 1e-6


def test_schema_gate_raises_on_leaky_columns(tmp_path: Path):
    """assert_feature_schema must raise on *_post, actual_*, outcome_*, and denylist."""
    _write_fixture_dataset(tmp_path, include_future_box=True)
    feat = build_pregame_features("2026-11-20", "2026-11-20T23:00:00Z", tmp_path)
    assert_feature_schema(feat)

    for bad_col in ("actual_steals", "stl_post", "outcome_blocks", "steals"):
        poisoned = feat.assign(**{bad_col: 1})
        with pytest.raises(ValueError, match="Leakage schema gate"):
            assert_feature_schema(poisoned)

    with pytest.raises(ValueError, match="Leakage schema gate"):
        assert_feature_schema(feat.assign(custom_leak=0), denylist=["custom_leak"])


def test_label_store_isolated_from_feature_paths(tmp_path: Path):
    """load_labels returns targets; feature/scoring modules never call or import it."""
    _write_fixture_dataset(tmp_path, include_future_box=True)
    labels = load_labels(tmp_path)
    assert {"game_id", "date", "player_id", "steals", "blocks", "minutes", "played"} <= set(labels.columns)

    for mod_rel in ("mod04/backbone_nba.py", "mod04/score_clustera.py"):
        src = Path(mod_rel).read_text()
        assert "load_labels" not in src

    tree = ast.parse(Path("mod04/feature_store.py").read_text())
    fn_node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_pregame_features")
    called = {node.func.id for node in ast.walk(fn_node) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "load_labels" not in called
