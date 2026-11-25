"""Hermetic offline unit tests for MOD-04 Cluster (c) Playmaking."""
from __future__ import annotations

import json
import numpy as np
import pandas as pd
import pytest

from mod04c.dist_negbin import (
    blend_p,
    convolve_pmf,
    fit_alpha_grid,
    nb_pmf,
    pmf_p_over,
    shift_mu_to_p_over,
)
from mod04c.feature_store import (
    LabelStore,
    assert_clean,
    build_pregame_features,
    filter_history,
    filter_snapshots,
    join_labels,
    validate_feature_frame,
)
from mod04c.nba_assists import (
    GAME_ENV_FEATURES,
    build_nba_assist_features,
    eb_shrink,
    walk_forward as nba_walk_forward,
)
from mod04c.nhl_assist_split import split_from_pbp, split_from_shots, verify_split
from mod04c.nhl_assists import walk_forward_compare as nhl_walk_forward_compare
from mod04c.passing import evaluate_skip_condition
from mod04c.prd04_guard import apply_guard, evaluate_guard
from mod04c.shadow import make_shadow_rows, write_predictions_json


# ---------------------------------------------------------------------------
# 1. U1 feature_store: schema gate + time travel + LabelStore isolation
# ---------------------------------------------------------------------------
def test_schema_gate_blocks_forbidden_columns():
    df_actual = pd.DataFrame({"player_id": [1], "actual_assists": [5]})
    with pytest.raises(ValueError, match="actual_assists"):
        validate_feature_frame(df_actual)

    df_post = pd.DataFrame({"player_id": [1], "assists_post": [4]})
    with pytest.raises(ValueError, match="assists_post"):
        assert_clean(df_post)

    df_outcome = pd.DataFrame({"player_id": [1], "OUTCOME_val": [3]})
    with pytest.raises(ValueError, match="OUTCOME_val"):
        validate_feature_frame(df_outcome)


def test_build_pregame_features_no_time_travel():
    slate = pd.DataFrame({"player_id": [101, 102], "team": ["BOS", "NYK"]})
    past_history = pd.DataFrame({
        "player_id": [101, 101, 102],
        "date": ["2025-11-01", "2025-11-05", "2025-11-03"],
        "assists": [6, 8, 4],
        "minutes": [32.0, 34.0, 28.0],
    })
    future_history = pd.concat([
        past_history,
        pd.DataFrame({
            "player_id": [101, 102],
            "date": ["2025-11-10", "2025-11-12"],  # target date and after
            "assists": [99, 99],
            "minutes": [48.0, 48.0],
        }),
    ], ignore_index=True)

    past_snaps = pd.DataFrame({
        "player_id": [101],
        "snapshot_ts_utc": ["2025-11-10T22:30:00Z"],
        "proj_flag": [1],
    })
    future_snaps = pd.concat([
        past_snaps,
        pd.DataFrame({
            "player_id": [101],
            "snapshot_ts_utc": ["2025-11-10T23:30:00Z"],  # after cutoff
            "proj_flag": [999],
        }),
    ], ignore_index=True)

    f_clean = build_pregame_features(
        "2025-11-10",
        "2025-11-10T23:00:00Z",
        slate=slate,
        history=past_history,
        snapshots=past_snaps,
    )
    f_polluted = build_pregame_features(
        "2025-11-10",
        "2025-11-10T23:00:00Z",
        slate=slate,
        history=future_history,
        snapshots=future_snaps,
    )
    pd.testing.assert_frame_equal(f_clean, f_polluted)
    assert f_clean.loc[f_clean["player_id"] == 101, "assists_l5"].iloc[0] == pytest.approx(7.0)
    assert f_clean.loc[f_clean["player_id"] == 101, "games_played"].iloc[0] == 2


def test_label_store_only_join_path():
    slate = pd.DataFrame({"player_id": [101], "team": ["BOS"], "date": ["2025-11-10"]})
    hist = pd.DataFrame({"player_id": [101], "date": ["2025-11-05"], "assists": [7]})
    feats = build_pregame_features("2025-11-10", "2025-11-10T23:00:00Z", slate=slate, history=hist)
    assert "actual_assists" not in feats.columns

    store = LabelStore(pd.DataFrame({"player_id": [101], "date": ["2025-11-10"], "actual_assists": [9]}))
    joined = join_labels(feats, store)
    assert joined["actual_assists"].iloc[0] == 9


# ---------------------------------------------------------------------------
# 2. U2/U4 dist_negbin
# ---------------------------------------------------------------------------
def test_nb_pmf_and_convolve():
    pmf = nb_pmf(np.array([3.2, 6.5]), np.array([0.15, 0.0]), kmax=40)
    assert pmf.shape == (2, 41)
    assert np.allclose(pmf.sum(axis=1), 1.0)

    pa = nb_pmf(2.0, 0.1, kmax=30)
    pb = nb_pmf(1.5, 0.0, kmax=30)
    conv = convolve_pmf(pa, pb)
    assert conv.sum() == pytest.approx(1.0, abs=1e-9)
    ks = np.arange(len(conv), dtype=float)
    mean_conv = float(np.sum(ks * conv))
    mean_a = float(np.sum(np.arange(len(pa)) * pa))
    mean_b = float(np.sum(np.arange(len(pb)) * pb))
    assert mean_conv == pytest.approx(mean_a + mean_b, abs=1e-6)


def test_blend_p_endpoints():
    assert blend_p(0.72, 0.55, c=0.0) == pytest.approx(0.55, abs=1e-6)
    assert blend_p(0.72, 0.55, c=1.0) == pytest.approx(0.72, abs=1e-6)


def test_shift_mu_to_p_over_hits_target():
    alpha = 0.12
    line = 5.5
    p_target = 0.64
    mu_shifted = shift_mu_to_p_over(4.0, alpha, line, p_target)
    pmf = nb_pmf(mu_shifted, alpha, kmax=60)
    p_got = pmf_p_over(pmf, line)
    assert abs(p_got - p_target) < 1e-3


# ---------------------------------------------------------------------------
# 3. PRD-04 guard
# ---------------------------------------------------------------------------
def test_prd04_guard_eligibility_rules():
    lock = "2025-11-10T23:00:00Z"

    # 30 min pre-lock + no unresolved -> eligible
    r_ok = evaluate_guard("2025-11-10T22:30:00Z", lock, [])
    assert r_ok.eligible is True

    # 90 min pre-lock -> excluded
    r_stale = evaluate_guard("2025-11-10T21:30:00Z", lock, [])
    assert r_stale.eligible is False
    assert "stale" in r_stale.reason

    # Fresh snapshot + one Questionable teammate -> excluded
    r_q = evaluate_guard("2025-11-10T22:45:00Z", lock, ["Questionable"])
    assert r_q.eligible is False
    assert "unresolved" in r_q.reason

    # Missing snapshot -> excluded
    r_miss = evaluate_guard(None, lock, [])
    assert r_miss.eligible is False
    assert "missing_snapshot" in r_miss.reason


def test_apply_guard_scrubs_excluded_probs_and_keeps_audit_rows():
    rows = [
        {"player_id": 1, "p_model": 0.61, "snapshot_ts_utc": "2025-11-10T22:40:00Z", "lock_utc": "2025-11-10T23:00:00Z", "unresolved_statuses": []},
        {"player_id": 2, "p_model": 0.58, "snapshot_ts_utc": "2025-11-10T20:00:00Z", "lock_utc": "2025-11-10T23:00:00Z", "unresolved_statuses": []},
    ]
    stamped = apply_guard(rows)
    assert stamped[0]["guard_status"] == "scored"
    assert stamped[0]["p_model"] == pytest.approx(0.61)
    assert stamped[1]["guard_status"] == "excluded"
    assert stamped[1]["p_model"] is None

    sh = make_shadow_rows("nba_assists", "mod04_nba_assists_shadow_v1", stamped, [r["p_model"] for r in stamped])
    assert sh[0]["p_model"] == pytest.approx(0.61)
    assert sh[1]["p_model"] is None
    assert sh[1]["p_served"] is None
    assert sh[1]["guard_status"] == "excluded"


# ---------------------------------------------------------------------------
# 4. C1 NHL assist split
# ---------------------------------------------------------------------------
def test_split_from_shots_and_verify():
    shots = pd.DataFrame({
        "game_id": [1001, 1001, 1001, 1001, 1002],
        "event": ["goal", "goal", "sog", "goal", "goal"],
        "period_type": ["REG", "REG", "REG", "SO", "OT"],
        "assist1_id": [10, 11, np.nan, 10, 10],
        "assist2_id": [11, np.nan, np.nan, 12, 12],
    })
    sp = split_from_shots(shots)
    # Game 1001 (SO excluded): player 10 has 1 prim; player 11 has 1 prim + 1 sec = 2
    # Game 1002: player 10 has 1 prim; player 12 has 1 sec
    row_1001_11 = sp[(sp["game_id"] == 1001) & (sp["player_id"] == 11)].iloc[0]
    assert row_1001_11["primary_assists"] == 1
    assert row_1001_11["secondary_assists"] == 1
    assert row_1001_11["assists_split_total"] == 2
    assert not ((sp["game_id"] == 1001) & (sp["player_id"] == 12)).any()

    master = pd.DataFrame({
        "game_id": [1001, 1001, 1001, 1002, 1002],
        "player_id": [10, 11, 12, 10, 12],
        "assists": [1, 2, 0, 1, 1],
    })
    ver = verify_split(sp, master)
    assert ver["exact_match"] is True
    assert ver["mismatches"] == 0
    assert ver["split_total"] == 5
    assert ver["master_total"] == 5

    # PBP equivalence on game 1001
    pbp = {
        "id": 1001,
        "plays": [
            {"typeDescKey": "goal", "periodDescriptor": {"periodType": "REG"}, "details": {"assist1PlayerId": 10, "assist2PlayerId": 11}},
            {"typeDescKey": "goal", "periodDescriptor": {"periodType": "REG"}, "details": {"assist1PlayerId": 11}},
            {"typeDescKey": "goal", "periodDescriptor": {"periodType": "SO"}, "details": {"assist1PlayerId": 10, "assist2PlayerId": 12}},
        ],
    }
    sp_pbp = split_from_pbp(pbp)
    assert int(sp_pbp["assists_split_total"].sum()) == 3


# ---------------------------------------------------------------------------
# 5. C2 NBA assists: eb_shrink, vacated usage separation, game_env ablation
# ---------------------------------------------------------------------------
def test_eb_shrink_pulls_small_sample_toward_prior():
    shrunk_small = eb_shrink(0.50, 10.0, 0.10, k=200.0)
    shrunk_large = eb_shrink(0.50, 2000.0, 0.10, k=200.0)
    assert 0.10 < shrunk_small < 0.15
    assert shrunk_large > 0.45


def test_nba_assist_features_and_walk_forward():
    rng = np.random.default_rng(42)
    dates = [f"2025-11-{d:02d}" for d in range(1, 21)]
    rows = []
    for gi, dt in enumerate(dates):
        for pid, tm, pos, rate in [(1, "BOS", "PG", 0.16), (2, "BOS", "C", 0.05), (3, "NYK", "PG", 0.14), (4, "NYK", "PF", 0.06)]:
            mins = 30.0
            fga = 14.0
            fgm = 7.0
            rows.append({
                "game_id": 500 + gi,
                "date": dt,
                "player_id": pid,
                "team_abbr": tm,
                "position": pos,
                "minutes": mins,
                "assists": int(rng.poisson(rate * 60.0)),
                "turnovers": int(rng.poisson(2.0)),
                "field_goals_attempted": fga,
                "field_goals_made": fgm,
                "three_points_made": 2.0,
                "free_throws_attempted": 4.0,
            })
    hist = pd.DataFrame(rows)
    inj = pd.DataFrame({"team_abbr": ["BOS"], "player_id": [1], "status": ["Out"]})
    slate = pd.DataFrame({"player_id": [2], "team": ["BOS"], "position": ["C"], "spread": [-3.5], "total": [224.5]})

    feat = build_nba_assist_features("2025-11-21", "2025-11-21T23:00:00Z", slate=slate, history=hist, injuries=inj)
    assert "vacated_handler_minutes" in feat.columns
    assert "finisher_fga_share_available" in feat.columns
    assert feat["vacated_handler_minutes"].iloc[0] > 0
    assert 0.0 < feat["finisher_fga_share_available"].iloc[0] < 1.0
    assert all(c in feat.columns for c in GAME_ENV_FEATURES)

    wf = nba_walk_forward(hist, fold_dates=["2025-11-16", "2025-11-18", "2025-11-20"])
    assert wf["n_rows"] > 0
    assert np.isfinite(wf["model_nb_ll"])
    assert np.isfinite(wf["eb_nb_ll"])
    assert np.isfinite(wf["unified_nb_ll"])


# ---------------------------------------------------------------------------
# 6. C3 NHL walk_forward_compare
# ---------------------------------------------------------------------------
def test_nhl_walk_forward_compare():
    rng = np.random.default_rng(7)
    dates = [f"2025-10-{d:02d}" for d in range(1, 21)]
    rows = []
    for gi, dt in enumerate(dates):
        for pid, tm, opp, p_rate, s_rate in [
            (10, "TOR", "MTL", 0.55, 0.35),
            (11, "TOR", "MTL", 0.25, 0.20),
            (12, "MTL", "TOR", 0.45, 0.30),
            (13, "MTL", "TOR", 0.20, 0.15),
        ]:
            pa = int(rng.poisson(p_rate))
            sa = int(rng.poisson(s_rate))
            rows.append({
                "game_id": 2000 + gi,
                "date": dt,
                "player_id": pid,
                "team_abbr": tm,
                "opp_abbr": opp,
                "toi_sec": 1100,
                "sog": int(rng.poisson(2.5)),
                "primary_assists": pa,
                "secondary_assists": sa,
                "assists": pa + sa,
            })
    hist = pd.DataFrame(rows)
    res = nhl_walk_forward_compare(hist, fold_dates=["2025-10-16", "2025-10-18", "2025-10-20"])
    assert res["n_rows"] > 0
    assert np.isfinite(res["split_ll"])
    assert np.isfinite(res["unified_ll"])
    assert res["recommendation"] in ("KEEP UNIFIED", "ADOPT SPLIT")


# ---------------------------------------------------------------------------
# 7. C4 Passing skip condition + shadow JSON writer
# ---------------------------------------------------------------------------
def test_evaluate_skip_condition_truth_table():
    assert evaluate_skip_condition(1.00, -0.0050) is True
    assert evaluate_skip_condition(0.85, -0.0030) is True
    assert evaluate_skip_condition(1.15, -0.0030) is True
    assert evaluate_skip_condition(0.84, -0.0050) is False
    assert evaluate_skip_condition(1.16, -0.0050) is False
    assert evaluate_skip_condition(1.00, -0.0029) is False


def test_write_predictions_json_local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    rows = make_shadow_rows(
        "nhl_assists",
        "mod04_nhl_assists_split_shadow_v1",
        [{"date": "2025-11-10", "player_id": 10, "team": "TOR", "opponent": "MTL", "game_id": 1}],
        [0.42],
        market_p=[0.40],
        lines=[0.5],
        c_serving=0.25,
    )
    assert rows[0]["p_model"] == pytest.approx(0.42)
    assert rows[0]["blend_status"] == "blended"

    key, n = write_predictions_json(rows, "nhl_assists", "v1", "EVSweep/mod04c/shadow/nhl_assists/2025-11-10.json")
    assert n == 1
    out_file = tmp_path / key
    assert out_file.exists()
    payload = json.loads(out_file.read_text())
    assert payload["family"] == "nhl_assists"
    assert payload["version"] == "v1"
    assert len(payload["rows"]) == 1
