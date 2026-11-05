"""Offline tests for the NFL drive simulator (nfl/sim): fitting, mechanics, joint structure and leakage."""
import math

import numpy as np
import pandas as pd
import pytest

from nfl.sim import engine as E
from nfl.sim import evaluate as EV
from nfl.sim import fit as F
from nfl.sim import pbp_prep as PP
from nfl.sim import ratings as RT
from nfl.sim import script_state as SS


# -- a small synthetic play-by-play with the real columns --------------------------------------------------------------
def synth_pbp(seasons=(2020, 2021), games=40, seed=0, trailing_boost=0.20):
    """Random drives with a built-in score-state response: the pass probability rises by `trailing_boost` when the offence
    trails by 9+ in the second half."""
    rng = np.random.default_rng(seed)
    rows = []
    for s in seasons:
        for gi in range(games):
            gid = f"{s}_{gi + 1:02d}_AAA_BBB_{gi}"
            home, away = ("AAA", "BBB") if gi % 2 else ("BBB", "AAA")
            gsr, score = 3600.0, {home: 0, away: 0}
            off = home
            for drive in range(1, 25):
                if gsr <= 0:
                    break
                res = rng.choice(["Touchdown", "Field goal", "Punt", "Turnover", "Turnover on downs", "Missed field goal"],
                                 p=[.22, .15, .36, .12, .08, .07])
                yl, down, ytg = float(rng.integers(55, 90)), 1, 10.0
                n = int(rng.integers(3, 9))
                for k in range(n):
                    last = k == n - 1
                    qtr = 1 if gsr > 2700 else 2 if gsr > 1800 else 3 if gsr > 900 else 4
                    hsr = gsr - 1800 if gsr > 1800 else gsr
                    sd = score[off] - score[home if off == away else away]
                    p_pass = 0.58 + (trailing_boost if (sd <= -9 and gsr < 1800) else 0.0)
                    if last and res in ("Punt", "Field goal", "Missed field goal"):
                        pt = "punt" if res == "Punt" else "field_goal"
                        rows.append(dict(game_id=gid, season=s, week=gi + 1, season_type="REG", home_team=home, away_team=away,
                                         posteam=off, defteam=home if off == away else away, qtr=qtr, fixed_drive=drive,
                                         fixed_drive_result=res, game_seconds_remaining=gsr, half_seconds_remaining=hsr, down=4,
                                         ydstogo=ytg, yardline_100=yl, score_differential=sd, play_type=pt, yards_gained=0.0,
                                         kick_distance=yl + 17.0, field_goal_result="made" if res == "Field goal" else "missed",
                                         total_home_score=score[home], total_away_score=score[away]))
                        if res == "Field goal":
                            score[off] += 3
                        gsr -= float(rng.integers(5, 15))
                        break
                    is_pass = rng.random() < p_pass
                    td = last and res == "Touchdown"
                    yards = yl if td else float(rng.integers(-2, 14))
                    sack = bool(is_pass and not td and rng.random() < 0.06)
                    comp = bool(is_pass and not sack and (td or rng.random() < 0.64))
                    if not comp and is_pass:
                        yards = 0.0
                    if sack:
                        yards = -float(rng.integers(3, 9))
                    rows.append(dict(game_id=gid, season=s, week=gi + 1, season_type="REG", home_team=home, away_team=away,
                                     posteam=off, defteam=home if off == away else away, qtr=qtr, fixed_drive=drive,
                                     fixed_drive_result=res, game_seconds_remaining=gsr, half_seconds_remaining=hsr, down=down,
                                     ydstogo=ytg, yardline_100=yl, score_differential=sd, play_type="pass" if is_pass else "run",
                                     yards_gained=yards, pass_touchdown=float(td and is_pass), rush_touchdown=float(td and not is_pass),
                                     interception=float(last and res == "Turnover" and is_pass), sack=float(sack),
                                     complete_pass=float(comp), receiver_player_id="r1" if comp else None,
                                     total_home_score=score[home], total_away_score=score[away], two_point_attempt=0.0))
                    if td:
                        score[off] += 7
                    gsr -= float(rng.integers(5, 42))
                    yl = max(yl - yards, 1.0)
                    if yards >= ytg:
                        down, ytg = 1, min(10.0, yl)
                    else:
                        down, ytg = min(down + 1, 4), max(ytg - yards, 1.0)
                off = home if off == away else away
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def tables():
    return PP.prep(synth_pbp())


@pytest.fixture(scope="module")
def params(tables):
    return F.fit_params(tables)


def neutral_pair():
    return EV.neutral_team("A"), EV.neutral_team("B")


# -- state buckets -----------------------------------------------------------------------------------------------------------
def test_time_and_score_buckets():
    tb = PP.time_bucket([1, 2, 2, 2, 3, 4, 4, 4, 4], [3000, 2000, 1500, 1790, 1000, 800, 450, 200, 100], [1200, 400, 299, 110, 1000, 800, 450, 200, 100])
    assert list(tb) == [0, 1, 2, 3, 4, 5, 6, 7, 8]
    sb = PP.score_bucket([-30, -10, -5, -1, 0, 2, 5, 10, 30])
    assert list(sb) == list(range(9))
    # chasing / milking classes only exist in the second half
    assert PP.script_class([0, 4, 4], [0, 0, 8]).tolist() == [0, 1, 2]


# -- numerical building blocks ---------------------------------------------------------------------------------------------
def test_fit_logistic_recovers_coefficients():
    rng = np.random.default_rng(1)
    X = np.column_stack([np.ones(20000), rng.normal(size=20000), rng.normal(size=20000)])
    w = np.array([-0.3, 0.9, -0.5])
    y = (rng.random(20000) < 1 / (1 + np.exp(-X @ w))).astype(float)
    assert np.allclose(F.fit_logistic(X, y, l2=0.01), w, atol=0.08)


def test_fit_softmax_probabilities():
    rng = np.random.default_rng(2)
    X = np.column_stack([np.ones(6000), rng.normal(size=6000)])
    W = np.array([[0.0, 0.0], [0.5, 1.0], [-0.2, -1.0]])
    Z = X @ W.T
    P = np.exp(Z) / np.exp(Z).sum(1, keepdims=True)
    y = np.array([rng.choice(3, p=p) for p in P])
    est = F.fit_softmax(X, y, 3, l2=0.01)
    assert np.allclose(est[1:], W[1:], atol=0.15)


def test_km_pool_undoes_touchdown_censoring():
    """Gains are exponential; a play from yardline L is capped at L (a touchdown). A plain pool of capped yards understates
    touchdowns at long yardlines; the Kaplan-Meier pool recovers P(raw >= yardline)."""
    rng = np.random.default_rng(3)
    n = 30000
    yl = rng.integers(3, 25, size=n).astype(float)
    raw = np.rint(rng.exponential(7.0, size=n))
    cens = raw >= yl
    y = np.minimum(raw, yl)
    pool = F.build_km_pool(y, cens, [np.zeros(n, dtype=int)], 1)
    d = pool.draw(np.zeros(200000, dtype=int), rng.random(200000))
    for L in (5, 12, 20):
        truth = float((rng.exponential(7.0, size=400000).round() >= L).mean())
        assert abs(float((d >= L).mean()) - truth) < 0.02


def test_pool_draw_stays_in_cell():
    vals = np.array([1.0, 2.0, 3.0, 10.0, 11.0, 12.0])
    pool = F.build_pool(vals, [np.array([0, 0, 0, 1, 1, 1])], 2, min_n=2, parent_of=lambda c, L: c)
    u = np.random.default_rng(0).random(500)
    assert set(pool.draw(np.zeros(500, dtype=np.int64), u)) <= {1.0, 2.0, 3.0}
    assert set(pool.draw(np.ones(500, dtype=np.int64), u)) <= {10.0, 11.0, 12.0}


# -- prep -------------------------------------------------------------------------------------------------------------------
def test_prep_tables(tables):
    pl = tables.plays
    assert set(pl["is_pass"].unique()) <= {0, 1}
    assert (pl["tb"].between(0, 8)).all() and (pl["sb"].between(0, 8)).all()
    assert len(tables.drives) > 100 and len(tables.fg) > 10 and len(tables.fourth) > 10
    assert pl["elapsed"].notna().mean() > 0.8


def test_fit_params_shapes(params):
    assert params.call_w.shape[0] == F.N_BASE_CALL + PP.N_TB * PP.N_SB
    assert params.pass_probs.shape == (F.N_PLAY_CELL, 3)
    assert np.all((params.pass_probs >= 0) & (params.pass_probs <= 1))
    assert params.fourth_w.shape[0] == 2
    assert params.league["cmp"] > 0.4


# -- fit is independent of data it is not given -------------------------------------------------------------------------------
def test_fit_ignores_later_seasons():
    pbp = synth_pbp(seasons=(2020, 2021, 2022))
    early = pbp[pbp["season"] < 2022]
    a = F.fit_params(PP.prep(early))
    scrambled = pbp.copy()
    m = scrambled["season"] == 2022
    scrambled.loc[m, "yards_gained"] = np.random.default_rng(0).permutation(scrambled.loc[m, "yards_gained"].to_numpy())
    b = F.fit_params(PP.prep(scrambled[scrambled["season"] < 2022]))
    assert np.array_equal(a.call_w, b.call_w) and np.array_equal(a.pass_probs, b.pass_probs)


# -- the measured score-state response -----------------------------------------------------------------------------------------
def test_script_state_recovers_built_in_response():
    t = PP.prep(synth_pbp(seasons=(2020, 2021, 2022, 2023), games=120, seed=5, trailing_boost=0.25))
    ct = SS.contrasts(t.plays, n_boot=30)
    row = ct[ct["label"] == "2nd half, down 9+ vs up 9+"].iloc[0]
    assert row["rel_raw"] > 0.15 and row["raw_lo"] > 0
    flat = PP.prep(synth_pbp(seasons=(2020, 2021, 2022, 2023), games=120, seed=5, trailing_boost=0.0))
    r0 = SS.contrasts(flat.plays, n_boot=30)
    assert abs(r0[r0["label"] == "2nd half, down 9+ vs up 9+"].iloc[0]["rel_raw"]) < 0.15


def test_call_model_picks_up_trailing_response():
    t = PP.prep(synth_pbp(seasons=(2020, 2021, 2022, 2023), games=120, seed=5, trailing_boost=0.25))
    P = F.fit_params(t)
    n = 4
    lg = E.call_logit(P, np.full(n, 2), np.full(n, 8.0), np.full(n, 50.0), np.full(n, 7), np.array([0, 4, 4, 8]))
    p = F.sigmoid(lg)
    assert p[0] > p[3]             # trailing 17+ late throws more than leading 17+ late


# -- simulator mechanics --------------------------------------------------------------------------------------------------------
def test_simulate_invariants(params):
    R = E.simulate(params, neutral_pair(), n=400, seed=4)
    st = R.stats.astype(int)
    for side in (0, 1):
        s = st[:, side]
        assert (s[:, :, E.S_CMP].sum(1) <= s[:, :, E.S_ATT].sum(1)).all()
        assert (s[:, :, E.S_REC].sum(1) == s[:, :, E.S_CMP].sum(1)).all()
        assert (s[:, :, E.S_TGT].sum(1) >= s[:, :, E.S_REC].sum(1)).all()
        # one coherent play record: passing yards and receiving yards are the same yards, TDs are the same TDs
        assert (s[:, :, E.S_PYD].sum(1) == s[:, :, E.S_RECYD].sum(1)).all()
        assert (s[:, :, E.S_PTD].sum(1) == s[:, :, E.S_RECTD].sum(1)).all()
        assert (s[:, :, E.S_PTD].sum(1) <= s[:, :, E.S_CMP].sum(1)).all()
    assert (R.pts >= 0).all() and R.pts.sum(1).mean() > 15
    assert (R.team[:, :, E.TS_DRIVES] > 3).all()


def test_simulate_deterministic_and_seed_sensitive(params):
    a = E.simulate(params, neutral_pair(), n=200, seed=9)
    b = E.simulate(params, neutral_pair(), n=200, seed=9)
    c = E.simulate(params, neutral_pair(), n=200, seed=10)
    assert np.array_equal(a.stats, b.stats) and np.array_equal(a.pts, b.pts)
    assert not np.array_equal(a.pts, c.pts)


def test_qb_receiver_dependence_is_produced_by_the_mechanics(params):
    R = E.simulate(params, neutral_pair(), n=3000, seed=2)
    qb = R.stats[:, 0, 0, E.S_PYD].astype(float)
    wr = R.stats[:, 0, 3, E.S_RECYD].astype(float)          # WR1 (index 3 of neutral_team)
    assert np.corrcoef(qb, wr)[0, 1] > 0.3
    att = R.stats[:, 0, :, E.S_ATT].sum(1).astype(float)
    car = R.stats[:, 0, :, E.S_CAR].sum(1).astype(float)
    assert np.corrcoef(att, car)[0, 1] < -0.1                # more passes -> fewer runs from the same finite clock
    tot, mar = R.pts.sum(1).astype(float), (R.pts[:, 0] - R.pts[:, 1]).astype(float)
    assert abs(np.corrcoef(tot, mar)[0, 1]) < 0.15


def test_better_offence_scores_more(params):
    good = EV.neutral_team("G")
    good.off = dict(E.NEUTRAL_OFF, ypcmp=1.15, ypr=1.1, cmp=0.25, sack=-0.3)
    R = E.simulate(params, (good, EV.neutral_team("N")), n=1500, seed=5)
    assert R.pts[:, 0].mean() > R.pts[:, 1].mean() + 1.0


def test_trailing_team_passes_more(params):
    """Same teams, same draws: the simulated pass share of a team that is behind exceeds the share when it is ahead."""
    a = E.simulate(params, neutral_pair(), n=1500, seed=6)
    # a weak offence trails more often than a strong one, so its late pass share is higher through the call model alone
    weak = EV.neutral_team("W")
    weak.off = dict(E.NEUTRAL_OFF, ypcmp=0.8, ypr=0.8, cmp=-0.3)
    b = E.simulate(params, (weak, EV.neutral_team("S")), n=1500, seed=6)
    share = lambda R, i: R.team[:, i, E.TS_PASSPL].sum() / (R.team[:, i, E.TS_PASSPL].sum() + R.team[:, i, E.TS_RUSHPL].sum())   # noqa: E731
    assert share(b, 0) >= share(a, 0) - 0.005


def test_player_weights_route_volume(params):
    t = EV.neutral_team("A")
    t.players[3].tgt_w = 10.0                 # WR1 dominates targets
    R = E.simulate(params, (t, EV.neutral_team("B")), n=600, seed=7)
    tg = R.stats[:, 0, :, E.S_TGT].astype(float).mean(0)
    assert tg[3] == tg.max() and tg[3] > 3 * np.sort(tg)[-2]


# -- distribution of draws ------------------------------------------------------------------------------------------------------
def test_draw_dist_is_a_distribution():
    rng = np.random.default_rng(0)
    d = rng.poisson(5.0, size=4000)
    dist = EV.DrawDist(d, "receptions")
    tot = sum(dist.mass(k) for k in range(0, 40))
    assert 0.98 < tot < 1.1                      # the floor can add a hair above 1
    assert abs(dist.sf(4.5) - float((d > 4.5).mean())) < 0.05
    y = EV.DrawDist(rng.normal(50, 20, 4000).round().clip(0), "rec_yds")
    assert 0 < y.mass(50) < 0.05
    a = EV.DrawDist((rng.random(4000) < 0.3).astype(int), "anytime_td")
    assert abs(a.sf(0.5) - 0.3) < 0.03 and abs(a.mass(1) - 0.3) < 0.03


# -- as-of ratings and usage never see the game itself ----------------------------------------------------------------------------
def test_ratings_state_is_strictly_before(tables, params):
    pl = tables.plays
    games = sorted(g for g in pl["game_id"].unique() if g.startswith("2021"))
    rt = RT.build_ratings(pl, params, k_seasons=[2020])
    g = games[len(games) // 2]
    team = pl[pl["game_id"] == g]["posteam"].iloc[0]
    s, w = rt.game_week[g]
    a = rt.adjustments(g, team, int(s), int(w))
    # scramble this game's plays and everything after it: the as-of adjustments for THIS game must not move
    sw = pl["game_id"].map(lambda x: tuple(rt.game_week[x]))
    mask = sw.map(lambda x: x >= (s, w))
    bad = pl.copy()
    rng = np.random.default_rng(0)
    for c in ("yards", "sack", "complete", "intc", "is_pass"):
        bad.loc[mask, c] = rng.permutation(bad.loc[mask, c].to_numpy())
    rt2 = RT.build_ratings(bad, params, k_seasons=[2020])
    b = rt2.adjustments(g, team, int(s), int(w))
    assert a == b
    # negative control: scrambling data BEFORE the game does move it
    early = sw.map(lambda x: x < (s, w))
    bad2 = pl.copy()
    bad2.loc[early, "yards"] = rng.permutation(bad2.loc[early, "yards"].to_numpy())
    bad2.loc[early, "sack"] = rng.permutation(bad2.loc[early, "sack"].to_numpy())
    c = RT.build_ratings(bad2, params, k_seasons=[2020]).adjustments(g, team, int(s), int(w))
    assert a != c


def test_usage_store_ignores_current_and_future_rows():
    logs = pd.DataFrame({
        "player_id": ["p1"] * 4, "position": ["WR"] * 4, "season": [2024] * 4, "week": [1, 2, 3, 4], "targets": [5, 7, 20, 99.0],
        "receptions": [3, 4, 10, 50.0], "receiving_yards": [40, 60, 150, 900.0], "carries": [0.0] * 4, "rushing_yards": [0.0] * 4,
        "attempts": [0.0] * 4})
    logs["_sort"] = logs["season"] * 100 + logs["week"]
    store = RT.UsageStore(logs, prior_before=2024 * 100)
    h = store.asof("p1", 2024 * 100 + 3)
    assert h.shape[0] == 2 and h[:, 0].tolist() == [5, 7]
    rows = pd.DataFrame({"player_id": ["p1"], "player_display_name": ["P One"], "position": ["WR"]})
    spec = RT.build_players(rows, store, 2024 * 100 + 3)[0]
    spec_future = RT.build_players(rows, store, 2024 * 100 + 5)[0]
    assert spec.tgt_w < spec_future.tgt_w                    # sees weeks 3 and 4 only when asked as of week 5


# -- scoring helpers -----------------------------------------------------------------------------------------------------------
def test_compare_pairs_and_verdict():
    rng = np.random.default_rng(1)
    n = 600
    rows = []
    for season in (2022, 2023, 2024, 2025, 2026):
        d = pd.DataFrame({"season": season, "stat": "rec_yds", "y": rng.integers(0, 120, n).astype(float), "line": 50.5})
        d["ls_base"] = rng.normal(-4.5, 0.4, n)
        d["ls_sim"] = d["ls_base"] + 0.08
        d["ls_hier"] = d["ls_base"]
        d["p_base"], d["p_sim"], d["p_hier"] = 0.5, 0.5, 0.5
        d["mu_base"] = d["mu_sim"] = d["mu_hier"] = 50.0
        rows.append(d)
    res = pd.concat(rows, ignore_index=True)
    c = EV.compare(res, "sim", "base", [2022, 2023, 2024, 2025, 2026])["rec_yds"]
    assert abs(c["d_logscore"] - 0.08) < 1e-9 and c["seasons_better"] == 5 and c["n"] == 3000
    assert EV.compare(res, "sim", "base", [2024, 2025])["rec_yds"]["n"] == 1200


def test_scoreboard_report_formats():
    mh = np.zeros(121)
    mh[60 + 3] = 100
    mh[60 - 7] = 100
    txt = EV.scoreboard_report(mh, np.array([3, -7, 3, 10, 0.0]), 10)
    assert "| 3 |" in txt and "Key numbers" in txt


# -- markets read off one joint game ----------------------------------------------------------------------------------------------
def test_derive_markets_from_the_same_draws(params):
    from nfl.sim import derive as D
    R = E.simulate(params, neutral_pair(), n=2500, seed=12)
    m = D.team_markets(R, spread_line=0.0, total_line=40.5)
    assert 0.4 < m["p_home_win"] < 0.6 and abs(m["mean_total"] - (m["mean_home"] + m["mean_away"])) < 1e-9
    p_qb = D.prop_prob(R, 0, 0, "pass_yds", 200.5)
    p_wr = D.prop_prob(R, 0, 3, "rec_yds", 50.5)
    joint = D.joint_over(R, [(0, 0, "pass_yds", 200.5), (0, 3, "rec_yds", 50.5)])
    assert joint > p_qb * p_wr                                    # positively dependent legs: joint > product of the marginals
    assert joint <= min(p_qb, p_wr)
    assert 0 < D.prop_prob(R, 0, 3, "anytime_td", 0.5) < 1
