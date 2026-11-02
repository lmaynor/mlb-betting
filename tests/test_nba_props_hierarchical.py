"""nba.props.hierarchical + its wiring in ev.daily_projections -- all offline.

Pins: combo additivity / covariance coherence, role-specific teammate redistribution, attempt-first 3P% shrinkage, the
minutes distribution, and the zero-leakage property (mutating rows on / after the cutoff moves nothing).
"""
import copy
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from scipy.stats import norm

from ev import daily_projections as dp
from mlb_core.data import cockpit_form as cf
from nba.props import hierarchical as H

START = datetime(2026, 2, 20, 1, 0, tzinfo=timezone.utc)        # tonight's tip: UTC day 2026-02-20
NOW = START - timedelta(hours=5)
ALL_STATS = list(dp.PROJECT_STATS["nba"])


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(dp._rosters, "load", lambda sport, now=None, **k: None)
    monkeypatch.setattr(dp, "write_archive", lambda payload, now: [])


def mk_rows(name, team, n=30, pts=15.0, reb=5.0, ast=3.0, mins=30.0, fg3a=4.0, fg3m=1.4, ftm=2.0, stl=1.0, blk=0.5,
            seed=0, noise=True, opp="BOS", start_day=1):
    """n chronological box-score rows dated 2026-01-start_day .. ; output scales with a shared random minutes draw."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        m = float(np.clip(mins + (rng.normal(0, 4.0) if noise else 0.0), 8, 44))
        k = m / mins
        out.append({"name": name, "team_abbr": team, "_date": (datetime(2026, 1, start_day) + timedelta(days=i)).strftime("%Y-%m-%d"),
                    "_sort": (datetime(2026, 1, start_day) + timedelta(days=i)).strftime("%Y-%m-%d"), "_opp": opp,
                    "season_year": 2025, "minutes": m,
                    "points": float(max(round(pts * k + (rng.normal(0, 3.0) if noise else 0.0)), 0)),
                    "rebounds": float(max(round(reb * k + (rng.normal(0, 1.5) if noise else 0.0)), 0)),
                    "assists": float(max(round(ast * k + (rng.normal(0, 1.2) if noise else 0.0)), 0)),
                    "three_points_attempted": float(max(round(fg3a * k + (rng.normal(0, 1.2) if noise else 0.0)), 0)),
                    "three_points_made": float(max(round(fg3m * k + (rng.normal(0, 0.9) if noise else 0.0)), 0)),
                    "free_throws_made": float(max(round(ftm * k + (rng.normal(0, 1.0) if noise else 0.0)), 0)),
                    "steals": float(max(round(stl * k + (rng.normal(0, 0.8) if noise else 0.0)), 0)),
                    "blocks": float(max(round(blk * k + (rng.normal(0, 0.6) if noise else 0.0)), 0)),
                    "fouls": 2.5})
    return out


def team_logs():
    idx = {}
    for nm, kw in {"Pat Guard": dict(pts=20, reb=3, ast=10, mins=34, seed=1),            # the starting point guard
                   "Bo Backup": dict(pts=12, reb=2.5, ast=6, mins=26, seed=2),
                   "Cy Center": dict(pts=14, reb=11, ast=1.2, mins=30, blk=2.0, seed=3),
                   "Pf Forward": dict(pts=13, reb=8, ast=2.0, mins=28, blk=1.0, seed=4),
                   "Wes Wing": dict(pts=15, reb=4, ast=3.0, mins=29, seed=5)}.items():
        idx[cf.norm_name(nm)] = mk_rows(nm, "LAL", **kw)
    idx[cf.norm_name("Opp Man")] = mk_rows("Opp Man", "BOS", pts=18, opp="LAL", seed=9)
    return dp.GameLogs("nba", {"all": idx})


def row(logs, stat, player, line=None, start=START):
    pn = cf.norm_name(player)
    rows, sdef = logs.rows_for(stat, pn)
    return dp.make_row("nba", stat, player, pn, rows, sdef, logs, line, None, [], "LAL", "BOS",
                       "nba:2026-02-19:BOS@LAL", start, 300.0, has_line=line is not None)


# ------------------------------------------------------------------------------------ 1. additivity and coherence
@pytest.mark.parametrize("scenario", ["plain", "absence", "spread_and_replay"])
def test_combo_means_are_the_exact_sum_of_the_component_means(scenario):
    logs = team_logs()
    if scenario == "absence":
        logs._nba_abs = {"LAL": {cf.norm_name("Pat Guard"): 1.0, cf.norm_name("Wes Wing"): 0.5}}
    if scenario == "spread_and_replay":
        logs.set_nba_spreads({"nba:2026-02-19:BOS@LAL": 17.5})
        logs.nba_replay = True
    for player in ("Bo Backup", "Cy Center", "Pf Forward"):
        m = {s: row(logs, s, player)["proj_mean"] for s in ("points", "rebounds", "assists", "pr", "pa", "ra", "pra")}
        assert m["pr"] == pytest.approx(m["points"] + m["rebounds"], abs=1e-3)
        assert m["pa"] == pytest.approx(m["points"] + m["assists"], abs=1e-3)
        assert m["ra"] == pytest.approx(m["rebounds"] + m["assists"], abs=1e-3)
        assert m["pra"] == pytest.approx(m["points"] + m["rebounds"] + m["assists"], abs=1e-3)
        assert round(m["points"] + m["rebounds"], 2) == round(m["pr"], 2)


def test_combo_variance_includes_positive_covariance():
    logs = team_logs()
    pn = cf.norm_name("Bo Backup")
    rows, _ = logs.rows_for("pra", pn)
    corr = H.within_player_corr(rows)
    assert corr[0, 1] > 0 and corr[0, 2] > 0 and corr[1, 2] > 0                 # shared minutes -> positive co-movement
    parts = {k: dp._nba_component(k, pn, rows, logs, "LAL", "BOS", "nba:2026-02-19:BOS@LAL", START)
             for k in ("points", "rebounds", "assists")}
    combo = dp._nba_projection("pra", pn, rows, logs, "LAL", "BOS", "nba:2026-02-19:BOS@LAL", START)
    indep = math.sqrt(sum(c["sd"] ** 2 for c in parts.values()))
    assert combo["sd"] > indep
    mu, sd = H.combo_moments({k: (c["mu"], c["sd"]) for k, c in parts.items()}, corr, ("points", "rebounds", "assists"))
    assert combo["sd"] == pytest.approx(sd) and combo["family"] == "normal"
    # a partial combo only carries its own pairs
    mu_pr, sd_pr = H.combo_moments({k: (c["mu"], c["sd"]) for k, c in parts.items()}, corr, ("points", "rebounds"))
    assert sd_pr == pytest.approx(math.sqrt(parts["points"]["sd"] ** 2 + parts["rebounds"]["sd"] ** 2
                                            + 2 * corr[0, 1] * parts["points"]["sd"] * parts["rebounds"]["sd"]))


def test_within_player_corr_is_shrunk_psd_and_falls_back_to_prior():
    rows = mk_rows("A", "LAL", n=40, seed=11)
    r = H.within_player_corr(rows)
    assert np.allclose(np.diag(r), 1.0) and np.allclose(r, r.T) and np.linalg.eigvalsh(r).min() > 0
    sample = np.corrcoef(np.array([[x["points"], x["rebounds"], x["assists"]] for x in rows[-30:]]), rowvar=False)
    assert r[0, 1] == pytest.approx(0.5 * sample[0, 1] + 0.5 * 0.20)
    assert np.array_equal(H.within_player_corr(rows[:5]), H.CORR_PRIOR)
    flat = mk_rows("B", "LAL", n=30, noise=False)                               # constant columns -> prior
    assert np.allclose(H.within_player_corr(flat), H.CORR_PRIOR)


# ------------------------------------------------------------------------------------- 2. role-specific redistribution
def _team(*specs):
    return {nm: mk_rows(nm, "LAL", noise=False, **kw) for nm, kw in specs}


def test_absent_point_guard_lifts_backup_guard_assists_more_than_the_centers():
    team = _team(("pg", dict(pts=20, reb=3, ast=10, mins=34)), ("bg", dict(pts=12, reb=2.5, ast=6, mins=26)),
                 ("c", dict(pts=14, reb=11, ast=1.2, mins=30, blk=2.0)))
    res = H.compute_vacated_shares(team, {"pg": 1.0})
    g, c = res["boost"]["bg"], res["boost"]["c"]
    assert g["assists"] - 1.0 >= 0.25 > 0.10 > c["assists"] - 1.0 >= 0.0
    assert g["assists"] - 1.0 > 5 * (c["assists"] - 1.0)
    assert g["assists"] != g["rebounds"]                                         # not one uniform multiplier
    assert res["boost"]["pg"] == {}                                              # the absent player absorbs nothing
    mean_ast = {k: np.mean([r["assists"] for r in v[-10:]]) for k, v in team.items()}
    assert res["vacated"]["assists"] == pytest.approx(mean_ast["pg"] / sum(mean_ast.values()), rel=1e-9)


def test_absent_center_lifts_the_remaining_big_rebounds_and_blocks_more_than_a_guards():
    team = _team(("c", dict(pts=14, reb=11, ast=1.2, mins=30, blk=2.0)), ("pf", dict(pts=13, reb=8, ast=2.0, mins=28, blk=1.0)),
                 ("bg", dict(pts=12, reb=2.5, ast=6, mins=26, blk=0.2)))
    res = H.compute_vacated_shares(team, {"c": 1.0})
    big, guard = res["boost"]["pf"], res["boost"]["bg"]
    assert big["rebounds"] - 1.0 >= 0.25 and big["blocks"] - 1.0 >= 0.25
    assert big["rebounds"] - 1.0 > 4 * (guard["rebounds"] - 1.0)
    assert big["blocks"] - 1.0 > 4 * (guard["blocks"] - 1.0)
    assert guard["assists"] < big["rebounds"]
    assert max(b for d in res["boost"].values() for b in d.values()) <= H.MAX_ROLE_BOOST + 1e-12


def test_redistribution_edge_cases():
    team = _team(("a", dict(mins=30)), ("b", dict(mins=28)), ("scrub", dict(mins=8, pts=3)))
    assert H.compute_vacated_shares(team, {})["boost"] == {}
    assert all(v == 0.0 for v in H.compute_vacated_shares(team, {})["vacated"].values())
    res = H.compute_vacated_shares(team, {"scrub": 1.0, "ghost": 1.0})            # sub-12-minute and unknown players vacate nothing
    assert res["vacated"]["points"] == 0.0 and res["rotation"] == ["a", "b"]
    half = H.compute_vacated_shares(team, {"a": 0.5})["vacated"]["points"]
    full = H.compute_vacated_shares(team, {"a": 1.0})["vacated"]["points"]
    assert half == pytest.approx(full / 2)
    assert "a" not in H.compute_vacated_shares(team, {"a": 0.9})["boost"]["a"]       # >= 0.8 absent: absorbs nothing
    assert H.compute_vacated_shares(team, {"a": 0.9})["boost"]["b"]["points"] > 1.0


def test_engine_applies_stat_specific_boost_in_place_of_the_flat_multiplier():
    base = team_logs()
    hurt = team_logs()
    hurt._nba_abs = {"LAL": {cf.norm_name("Pat Guard"): 1.0}}
    b = {s: row(base, s, "Bo Backup")["proj_mean"] for s in ("assists", "rebounds")}
    h = {s: row(hurt, s, "Bo Backup")["proj_mean"] for s in ("assists", "rebounds")}
    c_b, c_h = row(base, "assists", "Cy Center")["proj_mean"], row(hurt, "assists", "Cy Center")["proj_mean"]
    assert h["assists"] / b["assists"] > 1.2 > c_h / c_b > 1.0 and h["assists"] / b["assists"] > h["rebounds"] / b["rebounds"]
    assert row(hurt, "points", "Pat Guard") is None                              # the scratch itself is still dropped


# --------------------------------------------------------------------------------------------- 3. attempt-first threes
def hot_rows(att=3.0, pct=0.35, n_cold=56, hot=(5.0, 3.0), n_hot=4):
    rows = mk_rows("Sniper", "LAL", n=n_cold, fg3a=att, fg3m=att * pct, noise=False, mins=30.0)
    hot_rows_ = mk_rows("Sniper", "LAL", n=n_hot, fg3a=hot[0], fg3m=hot[1], noise=False, mins=30.0, start_day=1)
    for i, r in enumerate(hot_rows_):
        r["_date"] = r["_sort"] = (datetime(2026, 3, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
    return rows + hot_rows_


def test_hot_four_game_stretch_is_shrunk_toward_the_baseline_not_projected_forward():
    rows = hot_rows()
    assert sum(r["three_points_made"] for r in rows[-4:]) == 12 and sum(r["three_points_attempted"] for r in rows[-4:]) == 20
    p3, att = H.shrunk_three_pct(rows)
    assert 0.35 <= p3 <= 0.39 and p3 < 0.45                                       # nowhere near the 60% stretch
    manual = (sum(r["three_points_made"] for r in rows) + 150 * 0.362) / (sum(r["three_points_attempted"] for r in rows) + 150)
    assert p3 == pytest.approx(manual) and att == pytest.approx(sum(r["three_points_attempted"] for r in rows))
    t = H.project_nba_threes(rows)
    assert t["mu_3pm"] == pytest.approx(t["mu_3pa"] * t["p3"])
    raw_made_ewm = H.ewm([r["three_points_made"] for r in rows], 8.0)
    assert t["mu_3pm"] < raw_made_ewm                                             # the plain made-3s EWM chases the streak


def test_genuine_volume_jump_is_trusted_and_missing_attempts_degrade_cleanly():
    flat = mk_rows("S", "LAL", n=40, fg3a=3.0, fg3m=1.05, noise=False)
    jump = flat[:30] + [dict(r, three_points_attempted=8.0, three_points_made=2.9) for r in flat[30:]]
    assert H.project_nba_threes(jump)["mu_3pa"] > 1.5 * H.project_nba_threes(flat)["mu_3pa"]
    bare = [{k: v for k, v in r.items() if k not in ("three_points_attempted",)} for r in flat]
    assert H.project_nba_threes(bare) is None and H.shrunk_three_pct(bare) is None
    assert H.project_nba_threes(flat[:3]) is None


def test_threes_row_uses_the_attempt_first_mean():
    idx = {cf.norm_name("Sniper"): hot_rows(), cf.norm_name("Opp Man"): mk_rows("Opp Man", "BOS", opp="LAL", seed=9)}
    logs = dp.GameLogs("nba", {"all": idx})
    start = datetime(2026, 4, 1, 1, 0, tzinfo=timezone.utc)
    r = dp.make_row("nba", "threes", "Sniper", cf.norm_name("Sniper"), idx[cf.norm_name("Sniper")], cf.NBA_STATS["threes"],
                    logs, 3.5, None, [], "LAL", "BOS", "nba:2026-03-31:BOS@LAL", start, 300.0)
    t = H.project_nba_threes(idx[cf.norm_name("Sniper")])
    opp_f = logs.context("threes").opp_factor.get("BOS", 1.0)
    assert r["proj_mean"] == pytest.approx(t["mu_3pm"] * opp_f, rel=0.02) and r["nba_model"] == dp.NBA_MODEL


def test_shot_diet_scale_widens_three_heavy_scorers_and_narrows_foul_line_scorers():
    shooter = mk_rows("S", "LAL", n=25, pts=20, fg3m=4.0, ftm=1.0, noise=False)
    slasher = mk_rows("T", "LAL", n=25, pts=20, fg3m=0.5, ftm=7.0, noise=False)
    assert H.shot_diet_sd_scale(shooter) > 1.0 > H.shot_diet_sd_scale(slasher)
    ref = mk_rows("R", "LAL", n=25, pts=20, fg3m=2.0, ftm=3.6, noise=False)
    assert H.shot_diet_sd_scale(ref) == pytest.approx(1.0, abs=0.01)
    assert H.shot_diet_sd_scale(shooter[:2]) == 1.0
    assert H.SHOT_DIET_CLIP[0] <= H.shot_diet_sd_scale(slasher) <= H.SHOT_DIET_CLIP[1]


# ------------------------------------------------------------------------------------------------ minutes distribution
def test_minutes_distribution_blowout_back_to_back_and_no_information():
    none = H.project_minutes_distribution(34.0)
    assert none["mean"] == 34.0 and none["factor"] == 1.0 and none["extra_rel_var"] == 0.0
    close, blow = H.project_minutes_distribution(34.0, spread=2.0), H.project_minutes_distribution(34.0, spread=20.0)
    assert blow["mean"] < close["mean"] < 34.0 and blow["extra_rel_var"] > close["extra_rel_var"] > 0.0
    assert blow["delta"] == pytest.approx(-3.8 * norm.cdf((20.0 - 13.5) / 10.0))
    bench = H.project_minutes_distribution(20.0, spread=20.0)
    assert bench["mean"] == 20.0 and bench["extra_rel_var"] > 0.0                  # only starters are trimmed
    b2b = H.project_minutes_distribution(34.0, rest=0)
    assert b2b["mean"] == pytest.approx(32.8) and H.project_minutes_distribution(25.0, rest=0)["mean"] == 25.0
    assert H.project_minutes_distribution(34.0, rest=2)["mean"] == 34.0
    assert H.project_minutes_distribution(0.0)["mean"] == 0.0
    assert H.project_minutes_distribution(60.0)["mean"] <= H.MAX_MEAN_MIN
    foulish = H.project_minutes_distribution(34.0, spread=15.0, pf36=4.5)["sd"]
    assert foulish > H.project_minutes_distribution(34.0, spread=15.0, pf36=1.5)["sd"]


def test_spread_trims_starter_projection_and_widens_the_distribution():
    flat_logs, blow_logs = team_logs(), team_logs()
    blow_logs.set_nba_spreads({"nba:2026-02-19:BOS@LAL": 22.0})
    a, b = row(flat_logs, "points", "Cy Center", 14.5), row(blow_logs, "points", "Cy Center", 14.5)
    assert b["proj_mean"] < a["proj_mean"]
    assert (b["proj_p90"] - b["proj_p10"]) / b["proj_mean"] > (a["proj_p90"] - a["proj_p10"]) / a["proj_mean"]


# ----------------------------------------------------------------------------------------------------- 4. leakage
def _poison(rows, cutoff):
    out = copy.deepcopy(rows)
    for r in out:
        if r["_date"] >= cutoff:
            for k in ("minutes", "points", "rebounds", "assists", "three_points_made", "three_points_attempted",
                      "free_throws_made", "steals", "blocks", "fouls"):
                r[k] = r[k] * 7.0 + 31.0
    return out


def test_module_functions_ignore_rows_on_or_after_as_of():
    rows = mk_rows("L", "LAL", n=40, seed=21)
    cutoff = "2026-02-05"
    bad = _poison(rows, cutoff)
    assert any(a != b for a, b in zip(rows, bad))
    assert H.shrunk_three_pct(rows, cutoff) == H.shrunk_three_pct(bad, cutoff)
    assert H.project_nba_threes(rows, cutoff) == H.project_nba_threes(bad, cutoff)
    assert H.shot_diet_sd_scale(rows, cutoff) == H.shot_diet_sd_scale(bad, cutoff)
    assert np.array_equal(H.within_player_corr(rows, cutoff), H.within_player_corr(bad, cutoff))
    assert H.ewm_minutes(rows, cutoff) == H.ewm_minutes(bad, cutoff)
    assert H.pf_per36(rows, cutoff) == H.pf_per36(bad, cutoff)
    # the unfiltered call DOES see the poison, so the equalities above are not vacuous
    assert H.shrunk_three_pct(rows) != H.shrunk_three_pct(bad)
    assert H.within_player_corr(rows) is not None and not np.array_equal(H.within_player_corr(rows), H.within_player_corr(bad))

    team = {"a": rows, "b": mk_rows("b", "LAL", n=40, seed=22, pts=22), "c": mk_rows("c", "LAL", n=40, seed=23, ast=8)}
    team_bad = {k: _poison(v, cutoff) for k, v in team.items()}
    ab = {"a": 1.0}
    assert H.compute_vacated_shares(team, ab, cutoff) == H.compute_vacated_shares(team_bad, ab, cutoff)
    assert H.compute_vacated_shares(team, ab) != H.compute_vacated_shares(team_bad, ab)


def test_engine_rows_before_the_cutoff_are_bit_for_bit_unchanged_by_later_data():
    """Replay mode cuts history before the tip's UTC day. League-level context (opponent factors, dispersion) is a
    pre-existing, not-as-of-filtered input, so it is pinned from the clean logs for both runs."""
    clean = team_logs()
    start = datetime(2026, 1, 20, 1, 0, tzinfo=timezone.utc)               # tip's UTC day 2026-01-20; rows run to Jan 30
    cutoff = start.date().isoformat()
    poisoned = dp.GameLogs("nba", {"all": {k: _poison(v, cutoff) for k, v in clean.idx["all"].items()}})
    for logs in (clean, poisoned):
        logs.nba_replay = True
        logs._nba_abs = {"LAL": {cf.norm_name("Pat Guard"): 1.0}}
        logs.set_nba_spreads({"nba:2026-02-19:BOS@LAL": 9.0})
    assert any(r["_date"] >= cutoff for rows in clean.idx["all"].values() for r in rows)    # the poison has rows to hit
    for s in ALL_STATS:
        clean.context(s)
    poisoned._ctx = dict(clean._ctx)
    fields = ("proj_mean", "proj_raw", "proj_p10", "proj_p90", "p_over_model", "usage_proj", "opp_factor", "n_games")
    n_checked = 0
    for player in ("Bo Backup", "Cy Center", "Pf Forward", "Wes Wing"):
        for s in ALL_STATS:
            a, b = row(clean, s, player, 7.5, start), row(poisoned, s, player, 7.5, start)
            assert (a is None) == (b is None)
            if a is not None:
                assert {k: a[k] for k in fields} == {k: b[k] for k in fields}, (player, s)
                n_checked += 1
    assert n_checked == 4 * len(ALL_STATS)


def test_replay_cutoff_is_what_removes_late_rows_and_live_serving_keeps_every_row():
    rows = mk_rows("Q", "LAL", n=40, seed=5)                                      # dates 2026-01-01 .. 02-09
    idx = {cf.norm_name("Q"): rows, cf.norm_name("Opp Man"): mk_rows("Opp Man", "BOS", opp="LAL", seed=9)}
    start = datetime(2026, 1, 20, 1, 0, tzinfo=timezone.utc)
    live, replay = dp.GameLogs("nba", {"all": idx}), dp.GameLogs("nba", {"all": idx})
    replay.nba_replay = True
    args = ("nba", "points", "Q", cf.norm_name("Q"), rows, cf.NBA_STATS["points"])
    tail = (None, None, [], "LAL", "BOS", "nba:2026-01-19:BOS@LAL", start, 300.0)
    a, b = dp.make_row(*args, live, 20.5, *tail[1:]), dp.make_row(*args, replay, 20.5, *tail[1:])
    only_early = dp.make_row(*args[:4], [r for r in rows if r["_date"] < "2026-01-20"], args[5], replay, 20.5, *tail[1:])
    assert b["proj_mean"] == only_early["proj_mean"] and a["n_games"] == 40 and b["n_games"] == 19


# --------------------------------------------------------------------------------------------- 5. blend weights
def test_blend_weights_are_per_sport_and_stat_with_defaults():
    # measured c of THIS engine on 362,777 real props (nba_engine_vs_market_2026-11-01.md, ship_noabs); ra / blocks are unmeasured priors
    assert dp.blend_c("nba", "threes") == 0.19 and dp.blend_c("nba", "rebounds") == 0.13 and dp.blend_c("nba", "assists") == 0.09
    assert dp.blend_c("nba", "pra") == 0.02 and dp.blend_c("nba", "points") == 0.02
    assert dp.blend_c("nba", "ra") == 0.11 and dp.blend_c("nba", "blocks") == 0.05
    assert dp.blend_c("nhl", "sog") == 0.30 and dp.blend_c("nhl", "goals") == 0.03       # NHL goals measured +0.03 (nhl_engine_vs_market)
    assert dp.blend_c("nhl", "shots_unlisted") == dp.BLEND_C
    assert dp.blend_c("nfl", "rec_yds") == dp.BLEND_C and dp.blend_c("nfl", "anytime_td") == dp.BLEND_C   # NFL untouched
    assert dp.blend_c("mlb", "k") == dp.BLEND_C and dp.blend_c(None, None) == dp.BLEND_C
    hi = dp.blend_p_over(0.70, 0.50, sport="nba", stat="threes")
    lo = dp.blend_p_over(0.70, 0.50, sport="nba", stat="points")
    assert hi > lo > 0.5
    assert dp.blend_p_over(0.70, 0.50, c=0.5, sport="nba", stat="threes") == pytest.approx(
        dp.blend_p_over(0.70, 0.50, c=0.5))                                       # an explicit c wins
    assert dp.blend_p_over(0.7, 0.5) == pytest.approx(dp.blend_p_over(0.7, 0.5, c=dp.BLEND_C))
    r = row(team_logs(), "threes", "Bo Backup", 1.5)
    assert r["p_over_blend"] is None                                              # no market probability on this row
    assert r["nba_model"] == dp.NBA_MODEL


def test_nba_rows_keep_the_public_row_schema():
    r = row(team_logs(), "pra", "Bo Backup", 22.5)
    for k in ("proj_mean", "proj_raw", "proj_p10", "proj_p90", "p_over_model", "usage_proj", "opp_factor", "n_games",
              "calib_mult", "engine", "validated"):
        assert k in r
    assert r["calib_mult"] == 1.0 and r["validated"] is False and 0.0 < r["p_over_model"] < 1.0
    assert r["proj_p10"] < r["proj_mean"] < r["proj_p90"]



# ------------------------------------------------------------------- blended EV (Official-tier gate of ev.model_picks)
def test_blend_side_ev_prices_the_best_quote_at_the_blended_posterior():
    best = {"over": ("fanduel", 2.10, 110), "under": ("draftkings", 1.80, -125)}
    p_blend = dp.blend_p_over(0.62, 0.50, 0.13)
    p_over, ev_over = dp.blend_side_ev(p_blend, "over", best)
    p_under, ev_under = dp.blend_side_ev(p_blend, "under", best)
    assert p_over == p_blend and p_under == pytest.approx(1 - p_blend)
    assert ev_over == pytest.approx(p_blend * 2.10 - 1) and ev_under == pytest.approx((1 - p_blend) * 1.80 - 1)
    assert dp.blend_side_ev(None, "over", best) == (None, None) and dp.blend_side_ev(p_blend, None, best) == (None, None)
    assert dp.blend_side_ev(p_blend, "over", {"over": None, "under": None})[1] is None


def test_a_low_c_stat_earns_far_less_blended_ev_than_a_high_c_stat_on_the_same_model_gap():
    """points (c 0.02) barely moves off the market; threes (c 0.19) does: the point of gating Official on blend_ev."""
    best = {"over": ("fanduel", 2.0, 100), "under": None}
    ev = {st: dp.blend_side_ev(dp.blend_p_over(0.62, 0.50, dp.blend_c("nba", st)), "over", best)[1] for st in ("points", "threes")}
    assert ev["points"] < 0.025 < ev["threes"]
