"""Tests for mod04 NHL blocked-shots stack — U1 time-travel, schema gate,
Label Store isolation, and TOI backbone sanity.

Written by the Cluster (a) finisher after the agy lane hit its quota
(RESOURCE_EXHAUSTED) before producing this file; mirrors the standard of
tests/test_mod04_feature_store.py for the NBA half.
"""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mod04.backbone_nhl import TOI_BACKBONE_COLS, compute_toi_backbone
from mod04.feature_store_nhl import (
    FEATURE_COLUMNS,
    assert_nhl_feature_schema,
    build_pregame_features_nhl,
    load_labels_nhl,
)

TARGET = "2026-11-20"
CUTOFF = "2026-11-20T23:00:00Z"
PRIOR_DATES = ["2026-11-10", "2026-11-12", "2026-11-15", "2026-11-18"]
ALL_DATES = PRIOR_DATES + [TARGET, "2026-11-22"]

# (player_id, name, position, team, toi_sec, blocked_shots)
PLAYERS = [
    (8470001, "Adam Defense", "D", "BOS", 1500, 3),
    (8470002, "Brian Defense", "D", "BOS", 1380, 2),
    (8470003, "Chris Center", "C", "BOS", 1200, 1),
    (8470004, "David Wing", "L", "BOS", 1080, 0),
    (8470005, "Erik Defense", "D", "NYR", 1560, 4),
    (8470006, "Frank Wing", "R", "NYR", 1140, 1),
]
TEAM_IDS = {"BOS": 6, "NYR": 3}


def _write_fixture_dataset(root: Path, include_future_box: bool = True,
                           include_future_games: bool = True) -> None:
    nhl = root / "NHL"
    (nhl / "toi_splits").mkdir(parents=True, exist_ok=True)

    box_dates = ALL_DATES if include_future_box else PRIOR_DATES
    game_dates = ALL_DATES if include_future_games else PRIOR_DATES

    game_rows, box_rows, split_rows = [], [], []
    for i, d in enumerate(game_dates):
        gid = 2026020001 + i
        game_rows.append({
            "game_id": gid, "season_year": 2026, "game_type": 2, "date": d,
            "away_abbr": "NYR", "home_abbr": "BOS", "state": "OFF",
            "home_id": TEAM_IDS["BOS"], "away_id": TEAM_IDS["NYR"],
            "home_score": 3, "away_score": 2,
            "home_goals_ex_so": 3, "away_goals_ex_so": 2,
        })
    for i, d in enumerate(box_dates):
        gid = 2026020001 + ALL_DATES.index(d)
        mult = 99 if d >= TARGET else 1  # poison: leakage is immediately visible
        for pid, name, pos, team, toi, blk in PLAYERS:
            box_rows.append({
                "game_id": gid, "season_year": 2026, "game_type": 2, "date": d,
                "team_id": TEAM_IDS[team], "team_abbr": team,
                "is_home": team == "BOS", "player_id": pid, "name": name,
                "position": pos, "toi_sec": toi, "played": True,
                "blocked_shots": blk * mult, "hits": 2, "sog": 1,
            })
            ev = int(toi * 0.85)
            pp = int(toi * 0.08)
            pk = toi - ev - pp
            split_rows.append({
                "game_id": gid, "player_id": pid, "toi_total": toi,
                "toi_ev": ev, "toi_pp": pp, "toi_pk": pk,
            })

    pd.DataFrame(game_rows).to_csv(nhl / "games_master.csv", index=False)
    pd.DataFrame(box_rows).to_csv(nhl / "skater_boxscores_master.csv", index=False)
    pd.DataFrame(split_rows).to_csv(nhl / "toi_splits" / "toi_splits.csv", index=False)


def test_ci_time_travel_byte_identical(tmp_path: Path):
    """Deleting ALL skater/split rows on/after target_date must produce
    byte-identical features (the U1 contract), with and without an explicit slate."""
    d_full = tmp_path / "full"
    d_trunc = tmp_path / "trunc"
    _write_fixture_dataset(d_full, include_future_box=True, include_future_games=True)
    _write_fixture_dataset(d_trunc, include_future_box=False, include_future_games=True)

    f_full = build_pregame_features_nhl(TARGET, CUTOFF, d_full)
    f_trunc = build_pregame_features_nhl(TARGET, CUTOFF, d_trunc)

    assert len(f_full) == len(PLAYERS)
    for col in FEATURE_COLUMNS:
        assert col in f_full.columns
    pd.testing.assert_frame_equal(f_full, f_trunc, check_exact=True)
    assert f_full.to_json(orient="records") == f_trunc.to_json(orient="records")

    # Explicit-slate variant with ALL future games + boxscores deleted
    d_trunc_all = tmp_path / "trunc_all"
    _write_fixture_dataset(d_trunc_all, include_future_box=False, include_future_games=False)
    slate = pd.DataFrame([
        {"game_id": 2026020005, "player_id": 8470001, "name": "Adam Defense",
         "team": "BOS", "opp": "NYR", "is_home": 1},
        {"game_id": 2026020005, "player_id": 8470005, "name": "Erik Defense",
         "team": "NYR", "opp": "BOS", "is_home": 0},
    ])
    s_full = build_pregame_features_nhl(TARGET, CUTOFF, d_full, slate=slate)
    s_trunc = build_pregame_features_nhl(TARGET, CUTOFF, d_trunc_all, slate=slate)
    assert len(s_full) == 2
    pd.testing.assert_frame_equal(s_full, s_trunc, check_exact=True)


def test_schema_gate_raises_on_leaky_columns(tmp_path: Path):
    _write_fixture_dataset(tmp_path, include_future_box=True)
    feat = build_pregame_features_nhl(TARGET, CUTOFF, tmp_path)
    assert_nhl_feature_schema(feat)

    for bad_col in ("actual_blocked_shots", "blocked_post", "outcome_toi"):
        poisoned = feat.assign(**{bad_col: 1})
        with pytest.raises(ValueError, match="Leakage schema gate"):
            assert_nhl_feature_schema(poisoned)


def test_label_store_isolated_from_feature_paths(tmp_path: Path):
    _write_fixture_dataset(tmp_path, include_future_box=True)
    labels = load_labels_nhl(tmp_path)
    assert {"game_id", "date", "player_id", "blocked_shots"} <= set(labels.columns)

    for mod_rel in ("mod04/backbone_nhl.py", "mod04/score_clustera_nhl.py"):
        src = Path(mod_rel).read_text()
        assert "load_labels_nhl" not in src

    tree = ast.parse(Path("mod04/feature_store_nhl.py").read_text())
    fn_node = next(n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "build_pregame_features_nhl")
    called = {node.func.id for node in ast.walk(fn_node)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "load_labels_nhl" not in called


def test_toi_backbone_sanity():
    """Stage 1 exposure: finite, non-negative mu/sigma on the EV+PK basis."""
    prior = []
    for i, d in enumerate(PRIOR_DATES):
        for pid, name, pos, team, toi, blk in PLAYERS:
            prior.append({
                "player_id": pid, "game_id": 2026020001 + i, "date": d,
                "team": team, "position": pos, "toi_sec": float(toi),
            })
    splits = [{
        "game_id": r["game_id"], "player_id": r["player_id"],
        "toi_ev": r["toi_sec"] * 0.85, "toi_pk": r["toi_sec"] * 0.07,
    } for r in prior]
    slate = pd.DataFrame([{
        "player_id": pid, "team": team, "position": pos,
        "player_norm": name.lower(),
    } for pid, name, pos, team, toi, blk in PLAYERS])

    out = compute_toi_backbone(pd.DataFrame(prior), pd.DataFrame(splits), slate,
                               target_date=TARGET)
    assert len(out) == len(PLAYERS)
    for col in TOI_BACKBONE_COLS:
        assert col in out.columns
    numeric = out.select_dtypes(include=[np.number])
    assert np.isfinite(numeric.to_numpy()).all()
    assert (numeric.to_numpy() >= 0).all()
