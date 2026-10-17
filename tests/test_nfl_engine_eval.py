"""Offline tests for nfl.props.engine_eval (replay of ev.daily_projections) and the range/params additions."""
import math

import numpy as np
import pandas as pd
import pytest

from ev import daily_projections as DP
from nfl.props import engine_eval as EE

TEAMS = ["AAA", "BBB", "CCC", "DDD"]


def _frame(seed=0, seasons=(2024, 2025), weeks=10):
    rng = np.random.default_rng(seed)
    rows = []
    for s in seasons:
        for w in range(1, weeks + 1):
            for i, team in enumerate(TEAMS):
                opp = TEAMS[(i + w) % 4] if TEAMS[(i + w) % 4] != team else TEAMS[(i + 1) % 4]
                for pid, pos in ((f"wr_{team}", "WR"), (f"rb_{team}", "RB"), (f"qb_{team}", "QB")):
                    t = int(rng.integers(5, 12)) if pos == "WR" else 0
                    c = int(rng.integers(10, 20)) if pos == "RB" else 0
                    a = int(rng.integers(28, 40)) if pos == "QB" else 0
                    rows.append({"player_id": pid, "player_display_name": pid.replace("_", " "), "player_name": pid,
                                 "team": team, "season": s, "week": w, "season_type": "REG", "opponent_team": opp,
                                 "passing_yards": a * 7.0, "passing_tds": float(rng.integers(0, 4)) if a else 0.0,
                                 "attempts": float(a), "rushing_yards": c * 4.0, "carries": float(c),
                                 "receiving_yards": t * 9.0 + float(rng.integers(0, 15)), "receptions": max(t - 3, 0.0),
                                 "targets": float(t), "rushing_tds": float(rng.integers(0, 2)) if c else 0.0,
                                 "receiving_tds": float(rng.integers(0, 2)) if t else 0.0, "position": pos})
    df = pd.DataFrame(rows)
    df["_name"] = df["player_display_name"].map(EE.cf.norm_name)
    df["_opp"] = df["opponent_team"]
    df["_sort"] = df["season"] * 100 + df["week"]
    return df.sort_values(["season", "week"], kind="stable").reset_index(drop=True)


def test_replay_produces_rows_only_for_eligible_player_stats():
    res = EE.replay(_frame(), [2025], stats=["rec_yds", "pass_yds", "rush_yds"])
    assert set(res["stat"]) == {"rec_yds", "pass_yds", "rush_yds"}
    # WRs have 5-12 targets (>= 4 usage) so they are rec_yds-eligible; an RB with 0 targets is not
    rec = res[res["stat"] == "rec_yds"]
    assert rec["player"].str.startswith("wr").all()
    assert (res["n_games"] >= DP.MIN_GAMES).all()
    assert {"mu__default", "mu_l5", "mu_szn", "alpha", "cv", "y"} <= set(res.columns)


def test_replay_uses_only_games_before_the_target_week():
    df = _frame()
    base = EE.replay(df, [2025], stats=["rec_yds"])
    wk = 6
    d2 = df.copy()
    m = (d2["season"] == 2025) & (d2["week"] == wk)
    d2.loc[m, ["receiving_yards", "targets", "receptions"]] = 777.0          # scramble the target week's outcomes
    pert = EE.replay(d2, [2025], stats=["rec_yds"])
    key = ["week", "player"]
    a, b = base.set_index(key), pert.set_index(key)
    early = a.index.get_level_values("week") <= wk
    common = a.index[early].intersection(b.index)
    assert len(common) > 0
    np.testing.assert_allclose(a.loc[common, "mu__default"], b.loc[common, "mu__default"])      # no look-ahead
    late = a.index[~early].intersection(b.index)
    assert not np.allclose(a.loc[late, "mu__default"], b.loc[late, "mu__default"])             # the test has power


def test_default_variant_equals_the_serving_functions():
    df = _frame()
    res = EE.replay(df, [2025], stats=["receptions"])
    r = res[(res["week"] == 8)].iloc[0]
    hist = df[(df["_sort"] < 202508) & (df["_name"] == r["player"]) & df["season"].isin([2024, 2025])]
    rows = hist.to_dict("records")
    h = DP._hist(rows, EE.cf.NFL_STATS["receptions"])
    assert r["mu__default"] > 0 and len(h) == r["n_games"]
    # the variant column is the serving function's value (opponent factor aside): same history, same halflife
    nopp = EE.replay(df, [2025], [EE.Variant("nopp", 3.0, 0.5, None)], stats=["receptions"])
    r2 = nopp[(nopp["week"] == 8) & (nopp["player"] == r["player"])].iloc[0]
    assert r2["mu__nopp"] == pytest.approx(DP.project_mu(h, 3.0, 1.0)["mu"])


def test_ewm_weight_one_ignores_usage_and_param_defaults_are_unchanged():
    hist = [(v, u, 2025, "BBB") for v, u in [(40, 5), (60, 6), (30, 4), (80, 8), (50, 5), (70, 7)]]
    a = DP.project_mu(hist, 3.0, 1.0)["mu"]
    assert DP.project_mu(hist, 3.0, 1.0, ewm_weight=DP.EWM_WEIGHT)["mu"] == pytest.approx(a)
    only_ewm = DP.project_mu(hist, 3.0, 1.0, ewm_weight=1.0)["mu"]
    w = 0.5 ** ((len(hist) - 1 - np.arange(len(hist))) / 3.0)
    assert only_ewm == pytest.approx(float(np.sum(w * np.array([h[0] for h in hist])) / w.sum()))
    assert only_ewm != pytest.approx(a)


def test_opp_shrink_k_is_injectable_and_raw_ratio_is_exposed():
    idx = {"all": {}}
    for i in range(30):
        rows = [{"season": 2025, "_opp": "BBB" if g % 2 else "CCC", "targets": 5.0, "receiving_yards": 80.0 if g % 2 else 40.0,
                 "team": "AAA", "_sort": 202500 + g} for g in range(12)]
        idx["all"][f"p{i}"] = rows
    lo = DP.GameLogs("nfl", idx, opp_shrink_k=1.0).context("rec_yds")
    hi = DP.GameLogs("nfl", idx, opp_shrink_k=1e6).context("rec_yds")
    assert lo.opp_raw["BBB"] > 1.0 > lo.opp_raw["CCC"] and lo.opp_cnt["BBB"] == 180
    assert lo.opp_factor["BBB"] > hi.opp_factor["BBB"] >= 1.0          # more shrinkage -> closer to 1


def test_proj_range_properties():
    ctx = DP.StatContext(alpha=0.25, cv=0.5)
    lo, hi = DP.proj_range("negbin", 4.0, ctx)
    assert 0 <= lo < 4.0 < hi and float(lo).is_integer()
    ylo, yhi = DP.proj_range("normal", 60.0, ctx)
    assert ylo == pytest.approx(60 - 1.2816 * 30, abs=0.01) or ylo == 0.0
    assert yhi == pytest.approx(60 + 1.2816 * 30, abs=0.01)
    assert DP.proj_range("normal", 3.0, DP.StatContext(cv=2.0))[0] == 0.0                # yards floor at 0
    assert DP.proj_range("anytime", 0.3, ctx) == (None, None)
    assert DP.proj_range("negbin", 0.0, ctx) == (0.0, 0.0) and DP.proj_range("normal", None, ctx) == (None, None)


def _res(n=400, seed=1):
    rng = np.random.default_rng(seed)
    y = rng.poisson(4.0, n).astype(float)
    return pd.DataFrame({"season": 2024, "stat": "receptions", "y": y, "alpha": 0.2, "cv": 0.5, "mu_l5": 4.0, "mu_szn": 4.0,
                         "mu__default": 4.0 + rng.normal(0, 1.5, n).clip(-3, 3),
                         "mu__legacy_v1": 4.0 + rng.normal(0, 1.5, n).clip(-3, 3), "mu__oracle": y * 0.5 + 2.0,
                         "mu__bad": np.full(n, 9.0)})


def test_paired_sign_conventions():
    r = _res()
    good = EE.paired(r, "receptions", "mu__oracle", "mu__default")
    bad = EE.paired(r, "receptions", "mu__bad", "mu__default")
    assert good["d_logscore"] > 0 > good["d_mae"] and bad["d_logscore"] < 0 < bad["d_mae"]
    assert good["d_logscore_se"] > 0 and good["n"] == len(r)
    assert EE.paired(r.head(50), "receptions", "mu__oracle", "mu__default") is None        # too few rows


def test_select_variants_uses_train_seasons_only_and_defaults_otherwise():
    train, held = _res(seed=1), _res(seed=2)
    train["season"], held["season"] = 2022, 2025
    held["mu__oracle"] = np.full(len(held), 9.0)                  # "best" on train, garbage on the held-out season
    res = pd.concat([train, held], ignore_index=True)
    variants = [EE.SHIPPED, EE.LEGACY, EE.Variant("oracle"), EE.Variant("bad")]
    chosen = EE.select_variants(res, variants, [2022])
    assert chosen["receptions"] == "oracle"                       # picked on 2022 only
    assert chosen["pass_yds"] == EE.LEGACY.name                   # no rows for that stat -> keep the original
    ev = EE.evaluate(res, variants, tune_through=2022, test_seasons=[2022, 2025])
    h = ev["heldout"]["receptions"]
    assert h is not None and h["d_logscore"] < 0                  # held-out season exposes it: NOT adopted
    assert "ADOPT" not in EE.render(ev, [2022, 2025], len(res))
    assert ev["shipped_vs_legacy_held"]["receptions"] is not None


def test_baselines_are_floored_so_a_zero_mean_is_not_an_infinite_loss():
    r = _res()
    r["mu_l5"] = 0.0
    r["mu_szn"] = 0.0
    ev = EE.evaluate(r, [EE.SHIPPED], tune_through=2024, test_seasons=[2024])
    p = ev["baselines"]["receptions"]["vs_l5"]
    assert p is not None and math.isfinite(p["d_logscore"]) and abs(p["d_logscore"]) < 50


def test_variant_resolves_shipped_per_stat_and_legacy_uniformly():
    assert EE.SHIPPED.resolve("rec_yds") == (7.0, 1.0, 200.0)                      # validated override
    assert EE.SHIPPED.resolve("rush_yds") == (DP.HALFLIFE["nfl"], DP.EWM_WEIGHT, DP.OPP_SHRINK_K)
    assert EE.LEGACY.resolve("rec_yds") == (3.0, 0.5, 60.0) == EE.LEGACY.resolve("anytime_td")
    assert DP.stat_params("nfl", "anytime_td") == (7.0, 0.3, 200.0)
    assert DP.stat_params("nhl", "sog") == (DP.HALFLIFE["nhl"], DP.EWM_WEIGHT, DP.OPP_SHRINK_K)   # other sports untouched


def test_adopt_bar_is_multiplicity_corrected_and_anytime_td_uses_brier():
    assert EE.ADOPT_Z == pytest.approx(2.638, abs=0.01)
    assert EE._secondary_ok("rec_yds", {"d_mae": -0.1, "d_brier": 0.01})
    assert not EE._secondary_ok("rec_yds", {"d_mae": 0.1, "d_brier": -0.01})
    assert EE._secondary_ok("anytime_td", {"d_mae": 0.002, "d_brier": -0.003})        # MAE is not a proper score for 0/1
