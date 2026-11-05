"""Offline tests for the NBA joint game simulator (nba/sim): determinism, exact structural identities, marginal consistency with the
analytic engine, garbage-time minutes, SGP helpers, the fitter on a synthetic master, and the CLI. Synthetic numbers only."""
import json
import math

import numpy as np
import pandas as pd
import pytest

from nba.sim import fit as FIT
from nba.sim.joint_sim import (COMBOS, GameInput, JointDraws, Marginal, PlayerInput, game_from_dict, load_params, main,
                               simulate_game)

N = 3000


def _nb(mu, a):
    return Marginal("negbin", mu, math.sqrt(mu + a * mu * mu), a)


def _player(name, side, mins, pts, reb, ast, th):
    return PlayerInput(name, side, mins, {"points": Marginal("normal", pts, 0.40 * pts), "rebounds": _nb(reb, 0.22),
                                          "assists": _nb(ast, 0.28), "threes": _nb(th, 0.5)})


def _game(key="t:1", mu_margin=None):
    home = [_player("h1", "home", 35, 27, 6, 7, 3), _player("h2", "home", 34, 22, 5, 3, 2.5), _player("h3", "home", 32, 15, 9, 2, 1),
            _player("h4", "home", 28, 10, 8, 1.5, 0.5), _player("h5", "home", 22, 8, 3, 2, 1.0)]
    away = [_player("a1", "away", 36, 26, 7, 5, 3.2), _player("a2", "away", 33, 20, 4, 6, 2.2), _player("a3", "away", 30, 14, 8, 2, 1),
            _player("a4", "away", 27, 9, 9, 1, 0.3), _player("a5", "away", 20, 7, 3, 2, 1.2)]
    return GameInput(key, home + away, mu_margin=mu_margin)


@pytest.fixture(scope="module")
def draws():
    return simulate_game(_game(), n=N, seed=5)


def test_deterministic_and_seed_sensitive(draws):
    again = simulate_game(_game(), n=N, seed=5)
    other = simulate_game(_game(), n=N, seed=6)
    for key in (("h1", "points"), ("a2", "assists"), ("GAME", "total")):
        assert np.array_equal(draws.series(*key), again.series(*key))
    assert not np.array_equal(draws.series("h1", "points"), other.series("h1", "points"))
    # a different game key is a different (still reproducible) stream
    k2 = simulate_game(_game(key="t:2"), n=N, seed=5)
    assert not np.array_equal(draws.series("h1", "points"), k2.series("h1", "points"))


def test_labelled_unvalidated(draws):
    assert draws.meta["validated"] is False and "UNVALIDATED" in draws.meta["label"]


def test_combos_are_sums_of_the_same_draws(draws):
    for pl in ("h1", "a3"):
        for combo, members in COMBOS.items():
            expect = sum(draws.series(pl, m) for m in members)
            assert np.array_equal(draws.series(pl, combo), expect)


def test_points_constraints(draws):
    """Player points + the rest-of-roster bucket add up to the team total in every draw; points >= 3 x threes in every draw."""
    for side, prefix in (("home_points", "h"), ("away_points", "a")):
        team = draws.series("GAME", side)
        modeled = sum(draws.series(f"{prefix}{i}", "points") for i in range(1, 6))
        assert np.all(modeled <= team + 1e-9)
        # the rest bucket is a modest share of the team's points (fitted ~5%, floored at 2%)
        share = (team - modeled).mean() / team.mean()
        assert 0.02 <= share <= 0.35
    for pl in ("h1", "h2", "a1"):
        assert np.all(draws.series(pl, "points") >= 3.0 * draws.series(pl, "threes"))
    assert np.array_equal(draws.series("GAME", "total"), draws.series("GAME", "home_points") + draws.series("GAME", "away_points"))
    assert np.array_equal(draws.series("GAME", "margin"), draws.series("GAME", "home_points") - draws.series("GAME", "away_points"))


def test_structural_marginals_match_analytic_means(draws):
    for p in _game().players:
        for stat, m in p.marginals.items():
            x = draws.series(p.name, stat)
            assert abs(x.mean() - m.mu) <= max(0.04 * m.mu, 4 * x.std() / math.sqrt(N)), (p.name, stat, x.mean(), m.mu)
            # discrete count stats: the spread is solved too (rebounds / assists / threes reach their sd)
            if stat != "points":
                assert abs(x.std() / m.sd - 1.0) < 0.12, (p.name, stat, x.std(), m.sd)


def test_anchored_single_leg_equals_analytic(draws):
    an = draws.anchored(seed=5)
    for p in _game().players[:6]:
        for stat, m in p.marginals.items():
            for line in (math.floor(m.mu) - 1.5, math.floor(m.mu) + 0.5, math.floor(m.mu) + 2.5):
                if line < 0:
                    continue
                se = math.sqrt(max(m.p_over(line) * (1 - m.p_over(line)), 1e-4) / N)
                assert abs(an.p_over(p.name, stat, line) - m.p_over(line)) <= 3 * se + 2.0 / N


def test_anchoring_preserves_rank_order(draws):
    an = draws.anchored(seed=5)
    for key in (("h1", "points"), ("a2", "assists")):
        x, y = draws.series(*key), an.series(*key)
        grp = pd.DataFrame({"x": x, "y": y}).groupby("x")["y"].agg(["min", "max"]).sort_index()
        # ties in x are broken at random, so compare distinct x values: every y of a larger x is >= every y of a smaller x
        assert np.all(grp["min"].to_numpy()[1:] >= grp["max"].to_numpy()[:-1] - 1e-9)


def test_anchored_combo_is_sum_of_anchored_legs(draws):
    an = draws.anchored(seed=5)
    assert np.array_equal(an.series("h1", "pra"), an.series("h1", "points") + an.series("h1", "rebounds") + an.series("h1", "assists"))


def test_garbage_time_minutes_starters_lose_bench_gains():
    g = _game()
    g.players.append(_player("h6", "home", 15, 5, 2, 1, 0.5))
    d = simulate_game(g, n=6000, seed=9)
    ab = np.abs(d.series("GAME", "margin"))
    star, bench = d.series("h1", "minutes"), d.series("h6", "minutes")
    assert star[ab >= 20].mean() < star[ab < 5].mean() - 0.5
    assert bench[ab >= 20].mean() > bench[ab < 5].mean() - 1.0            # a deep-bench player does not lose what the starters lose
    # and the minutes anchor to the analytic means overall
    assert abs(star.mean() - 35.0) < 0.2


def test_pace_links_both_teams_and_the_total(draws):
    """A high-total game lifts everybody: both teams' points and a player's points are positively tied to the total."""
    assert draws.corr(("GAME", "home_points"), ("GAME", "away_points")) > 0.1
    assert draws.corr(("h1", "points"), ("GAME", "total")) > 0.1
    assert draws.corr(("a1", "rebounds"), ("GAME", "total")) > -0.2


def test_within_player_dependence_is_positive(draws):
    assert draws.corr(("h1", "points"), ("h1", "assists")) > 0.0
    assert draws.corr(("h2", "points"), ("h2", "threes")) > 0.4


def test_teammates_compete_for_one_pie(draws):
    """Teammates share a fixed team total, so their points are less dependent than opposing players' points."""
    assert draws.corr(("h1", "points"), ("h2", "points")) < draws.corr(("h1", "points"), ("a1", "points"))


def test_joint_api_identities(draws):
    a, b = ("h1", "points", 26.5), ("h2", "points", 21.5)
    j = draws.joint(a, b)
    assert j["p_joint"] <= min(j["p_a"], j["p_b"]) + 1e-12
    assert j["p_joint"] >= max(0.0, j["p_a"] + j["p_b"] - 1.0) - 1e-12
    assert math.isclose(j["p_indep"], j["p_a"] * j["p_b"])
    assert j["se"] > 0 and j["n"] == N
    same = draws.joint(a, a)
    assert math.isclose(same["p_joint"], same["p_a"])
    assert math.isclose(draws.joint(a, ("h1", "points", 26.5, "under"))["p_joint"], 0.0, abs_tol=1e-12)
    # an over leg and its under leg partition (half-point line): P(over) + P(under) = 1
    assert math.isclose(draws.p_over("h1", "points", 26.5) + draws.p_under("h1", "points", 26.5), 1.0)
    assert math.isclose(draws.joint_all([a, b, ("a1", "assists", 4.5)]), draws.joint_all([("a1", "assists", 4.5), b, a]))
    # game-level legs
    g = draws.joint(("h1", "pra", 38.5), ("GAME", "total", 218.5))
    assert 0.0 <= g["p_joint"] <= 1.0


def test_unknown_series_and_bad_inputs():
    with pytest.raises(KeyError):
        simulate_game(_game(), n=500, seed=1).series("nobody", "points")
    g = _game()
    g.players[0].team = "visitor"
    with pytest.raises(ValueError):
        simulate_game(g, n=500, seed=1)
    g2 = _game()
    del g2.players[0].marginals["rebounds"]
    with pytest.raises(ValueError):
        simulate_game(g2, n=500, seed=1)
    with pytest.raises(ValueError):
        simulate_game(_game(), n=500, seed=1, marginals="bogus")


def test_player_without_threes_has_no_threes_series_and_points_still_work():
    g = _game()
    del g.players[2].marginals["threes"]
    d = simulate_game(g, n=1500, seed=2)
    with pytest.raises(KeyError):
        d.series("h3", "threes")
    assert abs(d.series("h3", "points").mean() - 15.0) < 1.0


def test_conflicting_margin_input_stays_feasible():
    """A margin that disagrees wildly with the players' own means must not break the pies (rest bucket floored, no NaN)."""
    d = simulate_game(_game(mu_margin=-14.0), n=1500, seed=4)
    for key in (("h1", "points"), ("a1", "rebounds")):
        assert np.isfinite(d.series(*key)).all()
    assert abs(d.series("h1", "points").mean() - 27.0) < 1.5


def test_marginal_p_over_matches_the_board_function():
    from ev import daily_projections as DP
    ctx = DP.StatContext(alpha=0.25, cv=0.4)
    m = Marginal("normal", 21.3, 8.1)
    assert math.isclose(m.p_over(19.5), DP.p_over("normal", 21.3, 19.5, ctx, sd=8.1))
    nb = _nb(5.2, 0.31)
    assert math.isclose(nb.p_over(4.5), DP.p_over("negbin", 5.2, 4.5, ctx, alpha=0.31))


def test_game_from_dict_and_cli(tmp_path, capsys):
    spec = {"game_key": "cli:1", "mu_margin": 1.5, "players": [
        {"name": f"{s}{i}", "team": t, "minutes": 34 - 3 * i,
         "marginals": {"points": {"family": "normal", "mu": 24 - 3 * i, "sd": 8}, "rebounds": {"family": "negbin", "mu": 6 - i, "alpha": 0.2},
                       "assists": {"family": "negbin", "mu": 5 - i, "alpha": 0.3}}}
        for s, t in (("h", "home"), ("a", "away")) for i in range(4)]}
    gi = game_from_dict(spec)
    assert len(gi.players) == 8 and gi.players[0].marginals["points"].sd == 8
    path = tmp_path / "game.json"
    path.write_text(json.dumps(spec))
    rc = main(["--game", str(path), "--n", "1500", "--seed", "3", "--prop", "h0:pra:35.5", "--joint", "h0:points:23.5+a0:points:23.5",
               "--marginals", "anchored"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["validated"] is False and out["marginals"] == "anchored"
    assert 0.0 < out["props"][0]["p_over"] < 1.0
    assert 0.0 <= out["joint"][0]["p_joint"] <= 1.0


def test_committed_params_cover_what_the_simulator_reads():
    p = load_params()
    for k in ("game", "team", "minutes", "rest"):
        assert k in p
    assert p["fit_through_season_year"] <= 2023, "structural constants must be fitted before the held-out seasons"
    assert set(p["team"]["rebounds"]) >= {"b_pace", "b_points", "b_opp_points", "resid_sd"}
    mins = p["minutes"]
    assert len(mins["mean_ratio"]) == len(mins["mpg_bins"]) - 1 and len(mins["resid_quantiles"][0]) == len(mins["qgrid"])


# ------------------------------------------------------------------------------------------------ the fitter on a synthetic master
def _synthetic_masters(n_days=70, seed=1):
    rng = np.random.default_rng(seed)
    teams = ["AAA", "BBB", "CCC", "DDD"]
    prow, trow = [], []
    gid = 0
    for d in range(n_days):
        day = (pd.Timestamp("2022-11-01") + pd.Timedelta(days=d)).date().isoformat()
        order = list(rng.permutation(teams))
        for a, h in ((order[0], order[1]), (order[2], order[3])):
            gid += 1
            tot = {}
            for t in (a, h):
                pts = 0
                for k in range(9):
                    mins = max(34 - 3.2 * k + rng.normal(0, 3), 2.0)
                    p = max(int(rng.normal(24 - 2.4 * k, 6)), 0)
                    pts += p
                    prow.append({"game_id": f"g{gid}", "season_year": 2022, "season_type": "Regular Season", "date": day, "team_abbr": t,
                                 "player_id": f"{t}{k}", "played": True, "minutes": f"{int(mins)}:00", "points": p,
                                 "rebounds": int(rng.poisson(5)), "assists": int(rng.poisson(3)), "three_points_made": int(rng.poisson(1.5)),
                                 "turnovers": int(rng.poisson(1.5))})
                tot[t] = pts
            for t, o in ((a, h), (h, a)):
                trow.append({"game_id": f"g{gid}", "season_year": 2022, "season_type": "Regular Season", "date": day, "team_abbr": t,
                             "is_home": t == h, "points": tot[t], "three_points_made": 12, "assists": 25, "rebounds_offensive": 10,
                             "field_goals_attempted": 88, "free_throws_attempted": 22, "turnovers": 13})
    return pd.DataFrame(prow), pd.DataFrame(trow)


def test_fit_runs_on_a_synthetic_master_and_respects_the_cutoff():
    pm, tm = _synthetic_masters()
    params = FIT.fit(pm, tm, fit_through=2022)
    assert params["fit_through_season_year"] == 2022
    assert params["game"]["n_games"] > 50
    assert 0.0 < params["rest"]["points"] < 1.0
    m = params["minutes"]
    assert all(np.isfinite(np.array(row)).all() for row in m["mean_ratio"])
    # nothing at or after the cutoff may enter the fit: a cutoff before every game leaves nothing to fit
    with pytest.raises(Exception):
        FIT.fit(pm, tm, fit_through=2021)


def test_draws_object_is_plain_data():
    d = simulate_game(_game(), n=800, seed=1)
    assert isinstance(d, JointDraws) and d.n == 800
    assert set(d.players()) == {f"{s}{i}" for s in "ha" for i in range(1, 6)}
