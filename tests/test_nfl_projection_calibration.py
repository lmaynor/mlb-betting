"""The NFL calibration of the serving projection engine (ev.daily_projections + nfl.props.env_adjust)."""
import json
import math
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from ev import daily_projections as dp
from mlb_core.data import cockpit_form as cf
from nfl.props import engine_eval as EE
from nfl.props import env_adjust as EA

START = datetime(2026, 10, 18, 17, 0, tzinfo=timezone.utc)          # 1:00 PM ET Sunday, ET date 2026-10-18


def _rows(n=12, base_rec=5, base_yds=60):
    return [{"season": 2026, "_sort": 202600 + i, "_opp": "DEN", "team": "KC", "targets": float(base_rec + (i % 3)),
             "receptions": float(base_rec - 1 + (i % 3)), "receiving_yards": float(base_yds + 10 * (i % 3)),
             "rushing_yards": 0.0, "carries": 0.0, "attempts": 0.0, "passing_yards": 0.0, "passing_tds": 0.0,
             "rushing_tds": 0.0, "receiving_tds": float(i % 2), "player_display_name": "Wide Rec"} for i in range(n)]


def _logs():
    idx = {f"p{i}": _rows(base_yds=50 + 3 * i) for i in range(30)}
    return dp.GameLogs("nfl", {"all": idx})


def _row(stat="rec_yds", line=64.5, logs=None, rows=None):
    logs = logs or _logs()
    return dp.make_row("nfl", stat, "Wide Rec", "wide rec", rows or _rows(), cf.NFL_STATS[stat], logs, line, None, [],
                       "KC", "DEN", "nfl:2026-10-18:DEN@KC", START, 300.0)


@pytest.fixture
def calib(monkeypatch):
    def set_(d):
        monkeypatch.setattr(dp, "_CALIB", d)
    return set_


def test_uncalibrated_row_is_unchanged(calib):
    calib({})
    r = _row()
    assert r["calib_mult"] == 1.0 and r["proj_raw"] == r["proj_mean"] and r["engine"] == "empirical_ewm_v2"


def test_scale_mode_scales_the_mean_only(calib):
    calib({"rec_yds": {"k": 0.9, "n": 1}})
    r = _row()
    assert r["calib_mult"] == pytest.approx(0.9) and r["proj_mean"] == pytest.approx(r["proj_raw"] * 0.9, rel=1e-3)


def test_normal_family_spread_stays_tied_to_the_uncalibrated_mean(calib):
    calib({"rec_yds": {"k": 0.9, "n": 1}})
    logs = _logs()
    r = _row(logs=logs)
    ctx = logs.context("rec_yds")
    mu_raw, mu = r["proj_raw"], r["proj_mean"]
    expect = dp.p_over("normal", mu, 64.5, ctx, sd_mu=mu_raw)
    assert r["p_over_model"] == pytest.approx(expect, abs=1e-4)
    assert abs(dp.p_over("normal", mu, 64.5, ctx) - expect) > 1e-4              # following the mean would differ
    lo, hi = r["proj_p10"], r["proj_p90"]
    sd = max(ctx.cv * mu_raw, dp.MIN_SD_FRAC * mu_raw)
    assert (lo, hi) == (pytest.approx(max(mu - 1.2816 * sd, 0), abs=0.06), pytest.approx(mu + 1.2816 * sd, abs=0.06))


def test_env_mode_uses_the_games_lines_and_falls_back_to_exp_b0(calib):
    coef = {"b0": -0.05, "b1": 0.25, "b2": -0.016, "n": 1}
    calib({"rec_yds": coef})
    logs = _logs()
    logs._env = {("KC", "2026-10-18"): (28.0, 3.0)}                              # ET date of START
    with_lines = _row(logs=logs)
    expect = math.exp(-0.05 + 0.25 * math.log(28.0 / 22.5) - 0.016 * 3.0 / 7.0)
    assert with_lines["calib_mult"] == pytest.approx(expect, rel=1e-3)
    logs2 = _logs()
    logs2._env = {}                                                              # lines unavailable
    assert _row(logs=logs2)["calib_mult"] == pytest.approx(math.exp(-0.05), rel=1e-3)
    logs3 = _logs()
    logs3._env = {("KC", "2026-10-19"): (28.0, 3.0)}                             # a different date must not match
    assert _row(logs=logs3)["calib_mult"] == pytest.approx(math.exp(-0.05), rel=1e-3)


def test_other_sports_and_uncalibrated_stats_are_never_touched(calib):
    calib({"rec_yds": {"k": 0.9, "n": 1}})
    assert dp.calibration("nba", "points") is None and dp.calibration("nhl", "sog") is None
    assert dp.calibration("nfl", "pass_yds") is None
    assert _row(stat="receptions", line=4.5)["calib_mult"] == 1.0


def test_env_lines_are_nfl_only_and_failure_safe(monkeypatch):
    assert dp.GameLogs("nba", {"all": {}}).env_lines() == {}
    monkeypatch.setattr("nfl.data.read.load", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no mirror")))
    assert dp.GameLogs("nfl", {"all": {}}).env_lines() == {}


def test_env_lines_built_from_the_mirrored_schedules(monkeypatch):
    sched = pd.DataFrame([{"game_id": "g", "season": 2026, "week": 7, "gameday": "2026-10-18", "home_team": "KC",
                           "away_team": "DEN", "spread_line": 3.0, "total_line": 50.0},
                          {"game_id": "h", "season": 2026, "week": 7, "gameday": "2026-10-18", "home_team": "JAX",
                           "away_team": "LA", "spread_line": np.nan, "total_line": 44.0}])
    monkeypatch.setattr("nfl.data.read.load", lambda *a, **k: sched)
    env = dp.GameLogs("nfl", {"all": {}}).env_lines()
    assert env[("KC", "2026-10-18")] == pytest.approx((26.5, 3.0)) and env[("DEN", "2026-10-18")] == pytest.approx((23.5, -3.0))
    assert ("JAC", "2026-10-18") not in env                                      # a missing spread is dropped, never guessed


# ---------------------------------------------------------------------------------------- the shipped file
def test_shipped_calibration_file_is_well_formed_and_matches_its_own_evidence():
    with open(EA.CALIB_PATH) as fh:
        payload = json.load(fh)
    assert payload["coefs"].keys() == payload["evidence"].keys() == set(EE.STATS)
    for stat, coef in payload["coefs"].items():
        mode = payload["evidence"][stat]["mode"]
        if coef is None:
            assert mode == "none"
            continue
        assert mode in ("scale", "env") and (("k" in coef) == (mode == "scale"))
        if mode == "scale":
            assert 0.85 < coef["k"] < 1.05                                       # a few percent, not a rewrite
        else:
            assert abs(coef["b0"]) < 0.15 and abs(coef["b1"]) < 1.0 and abs(coef["b2"]) < 0.3
        assert coef["n"] >= EA.MIN_FIT_ROWS
        assert payload["evidence"][stat]["scale_verdict"] == "ADOPT"             # nothing ships without its evidence
    assert EA.load_calibration() == {k: v for k, v in payload["coefs"].items() if v}


# ---------------------------------------------------------------------------------------- harness logic
def _cal_frame(n=800, seed=5, season_gain=True):
    """Synthetic replay rows where the engine over-projects by 6% (a pure scale bias) in every season."""
    rng = np.random.default_rng(seed)
    parts = []
    for s in (2019, 2020, 2021, 2022, 2023):
        mu = rng.uniform(20, 90, n)
        y = np.clip(rng.normal(mu * 0.94, 0.45 * mu * 0.94), 0, None)
        parts.append(pd.DataFrame({"season": s, "stat": "rec_yds", "y": y, "alpha": 0.2, "cv": 0.45, "mu_l5": mu,
                                   "mu_szn": mu, "mu__default": mu, "game_id": [f"{s}_{i}" for i in range(n)],
                                   "team": "KC"}))
    return pd.concat(parts, ignore_index=True)


def test_calibration_walkforward_finds_a_pure_scale_bias_and_not_a_game_term():
    r = _cal_frame()
    env_tbl = pd.DataFrame({"game_id": r["game_id"], "team": "KC", "team_spread": 0.0, "implied_total": 22.5})
    cal = EE.calibration_walkforward(r, env_tbl, [2021, 2022, 2023])
    e = cal["rec_yds"]
    assert EE.cal_verdict("rec_yds", e["scale"]) == "ADOPT"
    assert e["scale"]["pooled"]["d_mae"] < 0
    assert EE.cal_verdict("rec_yds", e["env_vs_scale"]) != "ADOPT"               # no game effect was planted
    assert e["params"]["scale"][2023]["k"] == pytest.approx(0.94, abs=0.02)      # fitted on seasons < 2023 only


def test_cal_verdict_requires_season_agreement_and_non_inferior_log_score():
    base = {"n": 1000, "d_logscore": 0.0, "d_logscore_se": 0.01, "d_mae": -1.0, "d_mae_se": 0.1,
            "d_brier": 0.0, "d_brier_se": 0.001}
    seasons = {s: {**base} for s in range(2021, 2026)}
    assert EE.cal_verdict("rec_yds", {"pooled": base, "per_season": seasons}) == "ADOPT"
    two_bad = {s: ({**base, "d_mae": 0.5} if s in (2021, 2022) else {**base}) for s in range(2021, 2026)}
    assert EE.cal_verdict("rec_yds", {"pooled": base, "per_season": two_bad}) != "ADOPT"          # only 3/5 seasons
    worse_ls = {**base, "d_logscore": -0.05}
    assert EE.cal_verdict("rec_yds", {"pooled": worse_ls, "per_season": seasons}) != "ADOPT"     # z = -5 < -1
    weak = {**base, "d_mae": -0.1}
    assert EE.cal_verdict("rec_yds", {"pooled": weak, "per_season": seasons}) != "ADOPT"          # z = 1 < 2.64
    brier = {**base, "d_mae": 0.1, "d_brier": -0.01}                                              # anytime_td uses Brier
    assert EE.cal_verdict("anytime_td", {"pooled": brier, "per_season": {s: {**brier} for s in seasons}}) == "ADOPT"
