"""Follow-up to the hierarchical NBA engine: stale-absence discount, team-context factors, positional DvP, zero-inflated
steals/blocks, the overtime regime, the replay harness helpers and the opt-in coherent PRA in the trained-model scorer. Offline."""
import copy
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from scipy.stats import nbinom

from ev import daily_projections as dp
from mlb_core.data import cockpit_form as cf
from nba.props import engine_eval as EE
from nba.props import hierarchical as H
from nba.props import score as S
from nba.props import team_context as TC
from tests.test_nba_props_hierarchical import START, mk_rows, row, team_logs
from tests.test_nba_props_score import NOW as SCORE_NOW, XWALK, Stub, make_resources


# ----------------------------------------------------------------------------------------- stale-absence discount
def _dated(rows, keep):
    return [r for r in rows if keep(r["_date"])]


def test_an_absence_already_in_the_recent_games_earns_no_boost_and_a_new_one_does():
    def team(pg_rows):
        t = {"pg": pg_rows, "bg": mk_rows("bg", "LAL", n=40, pts=12, reb=2.5, ast=6, mins=26, noise=False)}
        for i in range(4):                                                        # enough actives that nothing hits the cap
            t[f"w{i}"] = mk_rows(f"w{i}", "LAL", n=40, pts=12, reb=4, ast=5.0, mins=27, noise=False)
        return t
    pg = mk_rows("pg", "LAL", n=40, pts=20, reb=3, ast=10, mins=34, noise=False)
    last10 = [r["_date"] for r in team(pg)["bg"][-10:]]
    fresh = H.compute_vacated_shares(team(pg), {"pg": 1.0})["boost"]["bg"]["assists"]
    assert 1.05 < fresh < H.MAX_ROLE_BOOST                                        # he played in every one of the last 10
    stale = H.compute_vacated_shares(team(_dated(pg, lambda d: d < last10[0])), {"pg": 1.0})["boost"]["bg"]["assists"]
    assert stale == 1.0                                                           # missed ALL ten: already in the numbers
    half = H.compute_vacated_shares(team(_dated(pg, lambda d: d < last10[5])), {"pg": 1.0})["boost"]["bg"]["assists"]
    assert half - 1.0 == pytest.approx((fresh - 1.0) * 0.5, rel=1e-9)             # the boost is linear in the NEW share


def test_fresh_fraction_edge_cases():
    assert H._fresh_fraction([], [], None) == 1.0
    assert H._fresh_fraction(["2026-01-05"], [], None) == 1.0                    # no rows to judge by: unadjusted
    rows = [{"_date": "2026-01-02"}, {"_date": "2026-01-03"}]
    assert H._fresh_fraction(["2026-01-03", "2026-01-04"], rows, None) == 0.5
    assert H._fresh_fraction(["2026-01-03"], rows, "2026-01-03") == 0.0          # as_of cuts the 01-03 row


# ------------------------------------------------------------------------------------------------ team context
def _team_table():
    t = {}
    for team, opp, base in (("AAA", "BBB", 40.0), ("BBB", "AAA", 38.0)):
        for k in range(2):
            rows = []
            for i in range(14):
                d = (datetime(2026, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
                rows.append({"team_abbr": team, "_date": d, "_sort": d, "_opp": opp, "field_goals_made": (base + i) / 2,
                             "field_goals_attempted": (base + i) / 2 * 2.1, "free_throws_made": 4.0,
                             "free_throws_attempted": 5.0})
            t[f"{team}{k}"] = rows
    return t


def test_team_games_factors_are_ratios_clipped_and_one_when_unknown():
    table = _team_table()
    tg = TC.TeamGames(table)
    assert len(tg.dates["AAA"]) == 14 and tg.fgm["AAA"][0] == pytest.approx(40.0)         # two players summed per team-game
    rows = table["AAA0"]
    f = tg.pool_factor(rows, "AAA", "BBB", "2026-01-15")
    g = tg.shotmaking_factor(rows, "AAA", "BBB", "2026-01-15")
    assert TC.RATIO_CLIP[0] <= f <= TC.RATIO_CLIP[1] and TC.RATIO_CLIP[0] <= g <= TC.RATIO_CLIP[1]
    w10 = lambda x, hl: float(np.sum(0.5 ** ((len(x) - 1 - np.arange(len(x))) / hl) * x) / np.sum(0.5 ** ((len(x) - 1 - np.arange(len(x))) / hl)))
    makes = tg.fgm["AAA"]
    assert g == pytest.approx(min(max(w10(makes, 10.0) / w10(makes[-20:], 8.0), TC.RATIO_CLIP[0]), TC.RATIO_CLIP[1]))
    assert g < 1.0                                                                # a long-half-life mean of a rising series < a short one
    assert tg.pool_factor(rows, "AAA", "ZZZ", "2026-01-15") == 1.0                # unknown opponent
    assert tg.pool_factor(rows, "AAA", "BBB", "2026-01-15", gamma=0.5) == pytest.approx(f ** 0.5)
    assert tg.shotmaking_factor(rows[:3], "AAA", "BBB", "2026-01-03") == 1.0      # < MIN_TEAM_GAMES prior games


def test_team_games_ignore_the_target_day_and_later():
    table = _team_table()
    day = "2026-01-10"
    bad = copy.deepcopy(table)
    for rows in bad.values():
        for r in rows:
            if r["_date"] >= day:
                r["field_goals_made"] *= 5
                r["field_goals_attempted"] *= 5
    a, b = TC.TeamGames(table), TC.TeamGames(bad)
    for fn in ("pool_factor", "shotmaking_factor"):
        assert getattr(a, fn)(table["AAA0"], "AAA", "BBB", day) == getattr(b, fn)(bad["AAA0"], "AAA", "BBB", day)
    assert a.shotmaking_factor(table["AAA0"], "AAA", "BBB", "2026-01-14") != b.shotmaking_factor(bad["AAA0"], "AAA", "BBB",
                                                                                                "2026-01-14")


def _logs_with_box_totals():
    logs = team_logs()
    for rows in logs.idx["all"].values():
        for r in rows:
            r.update(field_goals_made=6.0, field_goals_attempted=13.0, free_throws_made=2.0, free_throws_attempted=3.0)
    return logs


def test_pool_and_shot_switches_move_only_their_stat():
    logs = _logs_with_box_totals()
    for name, rows_ in logs.idx["all"].items():                                   # both teams' recent shooting turns cold
        for r in rows_[-8:]:
            r["field_goals_made"] = 3.0 if name == "opp man" else 3.5
    stats = ("rebounds", "assists", "points")

    def proj(**kw):
        with dp.nba_features(**kw):
            return {s: row(logs, s, "Pf Forward")["proj_mean"] for s in stats}
    base = proj(pool_gamma=0.0, shot_gamma=0.0)
    pooled = proj(pool_gamma=1.0, shot_gamma=0.0)
    assert pooled["assists"] == base["assists"] and pooled["points"] == base["points"] and pooled["rebounds"] != base["rebounds"]
    shot = proj(pool_gamma=0.0, shot_gamma=1.0)
    assert shot["rebounds"] == base["rebounds"] and shot["points"] == base["points"] and shot["assists"] != base["assists"]
    with pytest.raises(KeyError):
        with dp.nba_features(nope=1):
            pass


def test_shipped_defaults_follow_the_walk_forward_evidence():
    assert dp.NBA_FEATURES == {"dvp_k": None, "pool_gamma": 0.0, "shot_gamma": 0.5, "zinb": False, "eta_scale": 0.4,
                               "eta_off": ("steals", "blocks"), "trk_ast": 0.0, "trk_reb": 0.0}
    with dp.nba_features(pool_gamma=1.0):
        assert dp.NBA_FEATURES["pool_gamma"] == 1.0
    assert dp.NBA_FEATURES["pool_gamma"] == 0.0                                   # restored on exit
    base, hurt = team_logs(), team_logs()
    hurt._nba_abs = {"LAL": {cf.norm_name("Pat Guard"): 1.0}}
    for stat in ("steals", "blocks"):                                             # no absence boost where the replay said it hurts
        assert row(hurt, stat, "Bo Backup")["proj_mean"] == row(base, stat, "Bo Backup")["proj_mean"]
    mild = row(hurt, "assists", "Wes Wing")["proj_mean"]
    assert mild > row(base, "assists", "Wes Wing")["proj_mean"]
    with dp.nba_features(eta_scale=1.0):
        strong = row(hurt, "assists", "Wes Wing")["proj_mean"]
    assert strong > mild                                                          # the shipped strength is the weaker one


# ------------------------------------------------------------------------------------------------ positional DvP
def _pos_logs():
    idx = {}
    for i in range(6):
        for pos, pts in (("PG", 18.0), ("C", 12.0)):
            nm = f"{pos}{i}"
            rows = mk_rows(nm, "LAL", n=30, pts=pts, seed=i, noise=False, opp="BOS")
            for k, r in enumerate(rows):
                r["position"] = pos
                r["_opp"] = "BOS" if k % 2 == 0 else "NYK"
                if r["_opp"] == "BOS":
                    r["points"] += 4.0 if pos == "PG" else -4.0                   # guards score MORE against BOS, centers LESS
            idx[cf.norm_name(nm)] = rows
    idx["opp"] = mk_rows("Opp", "BOS", n=30, opp="LAL", seed=99)
    return dp.GameLogs("nba", {"all": idx})


def test_context_records_opponent_position_ratios_and_dvp_splits_the_factor():
    logs = _pos_logs()
    ctx = logs.context("points")
    g, b = ctx.nba_opp_pos_raw[("BOS", "G")], ctx.nba_opp_pos_raw[("BOS", "B")]
    assert g > 1.0 > b and ctx.nba_opp_pos_cnt[("BOS", "G")] > 0
    pn = cf.norm_name("PG0")
    rows, sdef = logs.rows_for("points", pn)
    kw = dict(sport="nba", stat="points", player="PG0", pn=pn, rows=rows, sdef=sdef, logs=logs, line=None, cons=None, at_pt=[],
              team="LAL", opp="BOS", gk="nba:x:BOS@LAL", start=START, mins=300.0, has_line=False)
    off = dp.make_row(**kw)["proj_mean"]
    with dp.nba_features(dvp_k=50.0):
        guard_on = dp.make_row(**kw)["opp_factor"]
        center_on = dp.make_row(**{**kw, "player": "C0", "pn": cf.norm_name("C0"),
                                   "rows": logs.rows_for("points", cf.norm_name("C0"))[0]})["opp_factor"]
        on = dp.make_row(**kw)["proj_mean"]
    assert guard_on > center_on and on != off
    assert all(dp.OPP_CLIP[0] <= f <= dp.OPP_CLIP[1] for f in (guard_on, center_on))
    with dp.nba_features(dvp_k=1e9):                                              # an enormous k = no position information
        assert dp.make_row(**kw)["proj_mean"] == pytest.approx(off, rel=1e-3)


def test_position_group_missing_falls_back_to_the_team_factor():
    logs = team_logs()                                                            # fixture rows carry no position
    with dp.nba_features(dvp_k=100.0):
        a = row(logs, "points", "Bo Backup")["proj_mean"]
    assert a == row(logs, "points", "Bo Backup")["proj_mean"]


# ------------------------------------------------------------------------------------------- zero-inflated count
def test_fit_zinb_recovers_structural_zeros_and_reports_none_without_them():
    rng = np.random.default_rng(3)
    base = rng.uniform(0.8, 1.6, 6000)
    n, pi_true = 1 / 0.25, 0.22
    nb = rng.negative_binomial(n, n / (n + base / (1 - pi_true)))
    y = np.where(rng.random(6000) < pi_true, 0, nb)
    pi, alpha = dp._fit_zinb(list(zip(y, y.mean() * np.ones(6000) * 0 + base * (1 - pi_true) / (1 - pi_true))))
    assert 0.12 < pi < 0.32 and 0.1 < alpha < 0.5
    plain = rng.negative_binomial(n, n / (n + base))
    pi0, _ = dp._fit_zinb(list(zip(plain, base)))
    assert pi0 < 0.08


def test_zinb_distribution_is_mean_preserving_and_has_the_extra_zeros():
    ctx = dp.StatContext(alpha=0.3)
    mu, zi = 1.2, 0.2
    p_zi = dp.p_over("negbin", mu, 0.5, ctx, alpha=0.3, zi=zi)
    n = 1 / 0.3
    assert p_zi == pytest.approx((1 - zi) * (1 - nbinom.cdf(0, n, n / (n + mu / (1 - zi)))))
    assert p_zi < dp.p_over("negbin", mu, 0.5, ctx, alpha=0.3)                    # inflated zeros: fewer overs at the 0.5
    ks = np.arange(0, 80)
    pmf = zi * (ks == 0) + (1 - zi) * nbinom.pmf(ks, n, n / (n + mu / (1 - zi)))
    assert float((ks * pmf).sum()) == pytest.approx(mu, rel=1e-3)
    lo, hi = dp.proj_range("negbin", mu, ctx, alpha=0.3, zi=zi)
    assert lo == 0.0 and hi >= 1.0
    assert dp.p_over("negbin", mu, -1.0, ctx, alpha=0.3, zi=zi) == 1.0


def test_zinb_switch_changes_steals_only_when_the_context_found_inflation():
    logs = team_logs()
    ctx = logs.context("steals")
    ctx.nba_zi_pi, ctx.nba_zi_alpha = 0.18, 0.25
    off = row(logs, "steals", "Pf Forward", 0.5)
    with dp.nba_features(zinb=True):
        on = row(logs, "steals", "Pf Forward", 0.5)
        pts_on = row(logs, "points", "Pf Forward", 20.5)
    assert on["proj_mean"] == off["proj_mean"] and on["p_over_model"] != off["p_over_model"]
    assert pts_on["p_over_model"] == row(logs, "points", "Pf Forward", 20.5)["p_over_model"]
    ctx.nba_zi_pi = 0.0
    with dp.nba_features(zinb=True):
        assert row(logs, "steals", "Pf Forward", 0.5)["p_over_model"] == off["p_over_model"]


# ----------------------------------------------------------------------------------------------- overtime regime
def test_overtime_adds_right_tail_variance_that_grows_in_tight_games_only_when_spread_is_known(monkeypatch):
    assert H.project_minutes_distribution(34.0)["extra_rel_var"] == 0.0           # no spread: typical OT rate in BOTH terms
    monkeypatch.setattr(H, "BLOWOUT_BENCH_GAIN", 0.0)                             # isolate the OT regime from the blowout one
    monkeypatch.setattr(H, "BLOWOUT_MIN_CV", H.BASE_MIN_CV)
    tight = H.project_minutes_distribution(20.0, spread=0.5)
    ref = H.project_minutes_distribution(20.0, spread=H.OT_SPREAD_REF)
    wide = H.project_minutes_distribution(20.0, spread=24.0)
    assert tight["extra_rel_var"] > ref["extra_rel_var"] >= 0.0
    assert tight["mean"] == 20.0 and ref["mean"] == 20.0                          # OT never moves the mean (history holds it)
    assert H.OT_WEIGHT_CLIP[0] <= H.OT_WEIGHT_REF <= H.OT_WEIGHT_CLIP[1]
    assert wide["extra_rel_var"] >= 0.0


# -------------------------------------------------------------------------------------- replay-harness helpers
def _fake_rows():
    rng = np.random.default_rng(1)
    out = []
    for v, mu_shift in (("hier", 0.0), ("better", -0.3)):
        for i in range(300):
            y = float(rng.poisson(5))
            out.append((v, "points", f"p{i % 40}", f"2026-01-{i % 28 + 1:02d}-{i}", 2024, y, y + rng.normal(0, 1) + mu_shift,
                        -2.0 + (0.05 if v == "better" else 0.0) + rng.normal(0, 0.1), 0.5, 1.0, abs(rng.normal(0, 1))))
    return pd.DataFrame(out, columns=["variant", "stat", "player", "date", "season", "y", "mu", "logp", "p_over", "over",
                                      "abs_err"])


def test_compare_pairs_rows_and_reports_a_clustered_gain():
    df = _fake_rows()
    df["date"] = df["date"].str.rsplit("-", n=1).str[0]
    df.loc[df.variant == "better", "date"] = df.loc[df.variant == "hier", "date"].values
    df.loc[df.variant == "better", "player"] = df.loc[df.variant == "hier", "player"].values
    c = EE.compare(df, "hier", "better", "points")
    assert c["n"] >= 250 and c["d_logscore"] > 0.03 and c["se_logscore"] > 0 and c["z_logscore"] > 3
    assert EE.compare(df, "hier", "missing", "points") is None
    lv = EE.levels(df)
    assert {"mae", "bias", "logscore", "brier"} <= set(lv.columns)
    text = EE.report(df, "unit")
    assert "ORACLE" in text and "| points | better |" in text and "adopt bar" in text


def test_choose_picks_the_best_grid_point_and_ignores_losers(monkeypatch):
    fake = {("dvp_75", "points"): {"d_logscore": 0.01, "n": 100}, ("dvp_150", "points"): {"d_logscore": 0.02, "n": 100},
            ("pool_1", "rebounds"): {"d_logscore": -0.01, "n": 100}, ("zinb", "steals"): {"d_logscore": 0.004, "n": 100}}
    monkeypatch.setattr(EE, "compare", lambda df, base, v, s: fake.get((v, s)))
    df = pd.DataFrame({"variant": ["hier", "dvp_75", "dvp_150", "pool_1", "zinb"]})
    assert EE.choose(df) == {"dvp_150": {"dvp_k": 150.0}, "zinb": {"zinb": True}}


def test_log_score_and_old_projection_shapes():
    ctx = dp.StatContext(alpha=0.3, cv=0.4)
    p = EE._logp({"family": "normal", "mu": 20.0, "sd": 5.0}, 20)
    assert p == pytest.approx(math.log(0.0796), abs=0.01)
    nb = EE._logp({"family": "negbin", "mu": 2.0, "alpha": 0.3, "zi": 0.0}, 2)
    zi = EE._logp({"family": "negbin", "mu": 2.0, "alpha": 0.3, "zi": 0.2}, 0)
    assert nb < 0 and zi < 0 and EE._logp({"family": "negbin", "mu": 0.0, "alpha": 0.3}, 3) < -10
    rows = mk_rows("A", "LAL", n=30, seed=4)
    a, b = EE.old_projection("points", rows, ctx, "BOS", 0.0), EE.old_projection("points", rows, ctx, "BOS", 3.0)
    assert b["mu"] == pytest.approx(a["mu"] * 1.12) and a["family"] == "normal"
    assert EE.old_projection("rebounds", rows, ctx, "BOS", 0.0)["family"] == "negbin"
    assert EE.old_projection("points", rows[:2], ctx, "BOS", 0.0) is None


# --------------------------------------------------------------------------------- coherent PRA in the scorer
def test_coherent_pra_is_the_exact_component_sum_with_positive_covariance():
    preds = {"points": (np.array([20.0, 10.0]), np.array([49.0, 25.0]), "nbinom"),
             "rebounds": (np.array([6.0, 8.0]), np.array([9.0, 16.0]), "nbinom"),
             "assists": (np.array([5.0, 2.0]), np.array([6.0, 3.0]), "nbinom")}
    mu, var, fam = S.coherent_pra(preds)
    assert fam == "normal_coherent" and np.allclose(mu, [31.0, 20.0])
    indep = preds["points"][1] + preds["rebounds"][1] + preds["assists"][1]
    assert (var > indep).all()
    sd = [np.sqrt(preds[k][1]) for k in ("points", "rebounds", "assists")]
    expect = indep + 2 * (0.20 * sd[0] * sd[1] + 0.18 * sd[0] * sd[2] + 0.22 * sd[1] * sd[2])
    assert np.allclose(var, expect)
    assert S.coherent_pra({"points": preds["points"]}) is None


def test_normal_outcome_probs_sum_to_one_and_push_only_on_whole_lines():
    po, pu, pp = S.normal_outcome_probs(24.5, 25.0, 36.0)
    assert pp == 0.0 and po + pu == pytest.approx(1.0) and 0.45 < po < 0.6
    po2, pu2, pp2 = S.normal_outcome_probs(25.0, 25.0, 36.0)
    assert pp2 > 0.05 and po2 + pu2 + pp2 == pytest.approx(1.0)
    assert S.normal_outcome_probs(24.5, 40.0, 4.0)[0] > 0.99


def test_scorer_coherent_switch_is_off_by_default_and_rewrites_only_pra():
    import json
    from tests.test_nba_props_score import FIX
    res, _ = make_resources()
    events = json.load(open(FIX))
    off, st0 = S.score_events(copy.deepcopy(events), SCORE_NOW, res)
    on, st1 = S.score_events(copy.deepcopy(events), SCORE_NOW, res, coherent_combos=True)
    assert set(off["family"]) <= {"nbinom"} and "normal_coherent" in set(on["family"])
    pra_on = on[on["stat"] == "pra"]
    mus = {k: Stub.PARAMS[k][0] for k in ("points", "rebounds", "assists")}
    assert len(pra_on) and (pra_on["family"] == "normal_coherent").all()
    assert (pra_on["mu"] == round(sum(mus.values()), 3)).all()
    assert (on[on["stat"] != "pra"]["family"] == "nbinom").all()
    cols = ["book", "player", "stat", "point", "side", "p_model", "mu", "var"]
    a = off[off["stat"] != "pra"].sort_values(cols[:4]).reset_index(drop=True)[cols]
    b = on[on["stat"] != "pra"].sort_values(cols[:4]).reset_index(drop=True)[cols]
    pd.testing.assert_frame_equal(a, b)
    assert st0["rows"] == st1["rows"]
