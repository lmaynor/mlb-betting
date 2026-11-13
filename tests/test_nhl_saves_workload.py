"""Shift-split workload hierarchy for saves (nhl.props.saves_workload): team-game aggregation from the banked
stats-API backfill (PR #216 route), the hierarchy projection by hand, and the prior-only leakage contract."""
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from nhl.data import sog_backfill as SB
from nhl.props import saves_workload as W


def _skater_rows(team="TOR", opp="BOS", day="2026-01-05", gid=2026020501, n=5, ev=3000, pp=300, sh=200,
                 att=8, att_pp=2, shots=4):
    """n identical skater rows for one team-game. With the default 5 skaters: team TOI = the raw seconds."""
    return [{"season": "2025-26", "date": day, "game_id": gid, "player_id": 100 + i, "player_name": f"P{i}",
             "team": team, "opponent": opp, "position": "C", "shots": shots, "toi_ev_sec": ev,
             "toi_pp_sec": pp, "toi_sh_sec": sh, "attempts_total": att, "pp_attempts": att_pp}
            for i in range(n)]


def _team_games(days=6, team="BOS", opp="TOR"):
    """Constant BOS team games on consecutive days: EV 3000s / PP 300s / SH 200s team time,
    40 attempts (10 on the PP), 20 shots per game."""
    rows = []
    for k in range(days):
        rows += _skater_rows(team=team, opp=opp, day=f"2026-01-{5 + k:02d}", gid=2026020501 + k,
                             att=8, att_pp=2, shots=4)
    return W.team_game_table(rows)


# ------------------------------------------------------------------------------------------------- aggregation
def test_team_game_table_splits_toi_and_attempts_by_situation():
    tg = W.team_game_table(_skater_rows())
    assert len(tg) == 1
    r = tg.iloc[0]
    assert r["n_skaters"] == 5
    assert r["toi_ev_s"] == pytest.approx(3000) and r["toi_pp_s"] == pytest.approx(300)
    assert r["toi_sh_s"] == pytest.approx(200)
    assert r["att_total"] == 40 and r["att_pp"] == 10 and r["att_nonpp"] == 30 and r["shots"] == 20
    assert r["att_rate_pp_per60"] == pytest.approx(10 / (300 / 3600))          # 120 per 60 of PP time
    assert r["att_rate_nonpp_per60"] == pytest.approx(30 / (3200 / 3600))      # EV+SH time


def test_team_game_table_empty_and_missing_splits():
    assert list(W.team_game_table([]).columns) == W.TEAM_GAME_COLS
    rows = _skater_rows()
    for r in rows:
        r["toi_pp_sec"] = None
    tg = W.team_game_table(rows)
    assert np.isnan(tg.iloc[0]["att_rate_pp_per60"])                          # no PP time: NaN rate, row kept


# --------------------------------------------------------------------------------------------------- hierarchy
def test_project_saves_hierarchy_by_hand():
    got = W.project_saves_hierarchy(ev_min=50, pk_min=5, pp_min=5, att_rate_pp_per60=120,
                                    att_rate_nonpp_per60=33.75, sog_conv=0.5, sv_pct=0.90)
    assert got["ev"] == pytest.approx(50 / 60 * 33.75 * 0.5 * 0.90)
    assert got["pk"] == pytest.approx(5 / 60 * 120 * 0.5 * 0.90)
    assert got["pp"] == pytest.approx(5 / 60 * 33.75 * 0.5 * 0.90)
    assert got["total"] == pytest.approx(got["ev"] + got["pk"] + got["pp"])


def test_project_saves_hierarchy_nan_propagates():
    got = W.project_saves_hierarchy(ev_min=50, pk_min=5, pp_min=5, att_rate_pp_per60=float("nan"),
                                    att_rate_nonpp_per60=33.75, sog_conv=0.5, sv_pct=0.90)
    assert np.isnan(got["total"]) and np.isnan(got["pk"])


# ---------------------------------------------------------------------------------------------------- features
def _saves_frame(day="2026-01-11"):
    return pd.DataFrame([{"game_id": 2026020511, "player_id": 900, "date": day, "team": "TOR", "opp": "BOS",
                          "g_sv_l60": 0.905, "g_rest": 2.0, "g_starts_7d": 1.0}])


def test_workload_features_use_only_prior_team_games():
    tg = _team_games(days=6)                                                  # BOS games Jan 5..10
    f = W.workload_features(_saves_frame("2026-01-11"), tg)
    r = f.iloc[0]
    assert r["wl_opp_pp_toi_l10"] == pytest.approx(300)
    assert r["wl_opp_ev_toi_l10"] == pytest.approx(3000)
    assert r["wl_opp_att_pp_l10"] == pytest.approx(120)
    assert r["wl_opp_sog_conv_l10"] == pytest.approx((6 * 20 + 0.5 * 200) / (6 * 40 + 200), rel=1e-6)
    conv = (6 * 20 + 100) / (6 * 40 + 200)
    expect = (3000 / 3600 * (30 / (3200 / 3600)) + 300 / 3600 * 120 + 200 / 3600 * (30 / (3200 / 3600))) * conv * 0.905
    assert r["wl_proj_saves_hier"] == pytest.approx(expect, rel=1e-6)
    assert r["wl_rest_x_starts7d"] == pytest.approx(2.0)


def test_workload_features_leakage_contract():
    tg = _team_games(days=8)                                                  # Jan 5..12
    frame = pd.concat([_saves_frame("2026-01-10"), _saves_frame("2026-01-11")], ignore_index=True)
    base = W.workload_features(frame, tg)
    garbled = tg.copy()
    garbled.loc[garbled["date"] >= "2026-01-10", ["att_pp", "att_total", "shots", "toi_pp_s"]] = 99999.0
    garbled.loc[garbled["date"] >= "2026-01-10", "att_rate_pp_per60"] = 99999.0   # scramble the derived rate too
    after = W.workload_features(frame, garbled)
    early = base["date"] <= "2026-01-10"
    pd.testing.assert_frame_equal(after.loc[early, W.WORKLOAD_FEATURES].reset_index(drop=True),
                                  base.loc[early, W.WORKLOAD_FEATURES].reset_index(drop=True))


def test_workload_features_no_history_is_nan_not_zero():
    f = W.workload_features(_saves_frame("2026-01-05"), _team_games(days=2))
    assert np.isnan(f.iloc[0]["wl_proj_saves_hier"]) and np.isnan(f.iloc[0]["wl_opp_att_pp_l10"])
    empty = W.workload_features(_saves_frame(), W.team_game_table([]))
    assert empty[W.WORKLOAD_FEATURES].isna().all().all()


# ------------------------------------------------------------------------------------------------- banked loader
def test_banked_loader_reads_only_written_dates():
    rows = _skater_rows(day="2025-01-06", gid=2024020639)
    key = SB.gcs_key(SB.GCS_PREFIX, "2024-25", date(2025, 1, 6))
    store = {key: ("\n".join(json.dumps(r) for r in rows) + "\n").encode()}
    got = W.load_banked_rows(store.get, ["2024-25"])
    assert len(got) == 5 and got[0]["team"] == "TOR"
    keys = W.banked_keys(["2024-25"])
    assert key in keys and len(keys) == 273                                    # Oct 1 .. Jun 30
    assert W.parse_jsonl(store[key]) == rows
