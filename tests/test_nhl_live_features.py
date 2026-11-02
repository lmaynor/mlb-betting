"""nhl.props.live_features: pre-game features for unplayed games == the training path's features, with no future leakage.

A small synthetic league (6 teams, 40 days, 3 games a day, 8 skaters + 2 goalies a team) with every column the builders read."""
import numpy as np
import pandas as pd
import pytest

from nhl.props import live_features as LF
from nhl.props import xgfeat as X

TEAMS = {"AAA": 1, "BBB": 2, "CCC": 3, "DDD": 4, "EEE": 5, "FFF": 6}
ABBR = list(TEAMS)
N_DAYS = 40
BASE = pd.Timestamp("2025-10-10")


def league(seed=3, days=N_DAYS):
    rng = np.random.default_rng(seed)
    games, sk, gl, xs, xgl, xt = [], [], [], [], [], []
    gid = 2000
    for d in range(days):
        date = (BASE + pd.Timedelta(days=d)).strftime("%Y-%m-%d")
        order = rng.permutation(len(ABBR))
        for k in range(3):
            home, away = ABBR[order[2 * k]], ABBR[order[2 * k + 1]]
            gid += 1
            games.append({"game_id": gid, "season_year": 2025, "game_type": 2, "date": date, "home_id": TEAMS[home],
                          "away_id": TEAMS[away], "home_abbr": home, "away_abbr": away})
            for team, is_home in ((home, True), (away, False)):
                tid = TEAMS[team]
                sog_f = float(rng.poisson(30))
                xt.append({"game_id": gid, "team_id": tid, "att_for": float(rng.poisson(55)), "xgf": float(rng.gamma(9, 0.33)),
                           "sog_for": sog_f, "xgf_sog": float(rng.gamma(9, 0.3)), "goals_for_pop": float(rng.poisson(3)),
                           "hd_for": float(rng.poisson(12)), "att_pp_for": float(rng.poisson(8)),
                           "att_against": float(rng.poisson(55)), "xga": float(rng.gamma(9, 0.33)),
                           "sog_against": float(rng.poisson(30)), "xga_sog": float(rng.gamma(9, 0.3)),
                           "goals_against_pop": float(rng.poisson(3)), "hd_against": float(rng.poisson(12)),
                           "att_pp_against": float(rng.poisson(8))})
                for j in range(8):
                    pid = tid * 100 + j
                    toi = float(rng.normal(1100 if j < 5 else 800, 60))
                    g, a = float(rng.poisson(0.3)), float(rng.poisson(0.4))
                    sg = float(rng.poisson(2.4))
                    sk.append({"game_id": gid, "season_year": 2025, "game_type": 2, "date": date, "team_abbr": team, "team_id": tid, "is_home": is_home,
                               "player_id": pid, "name": f"P{pid}", "position": "D" if j >= 6 else ("C" if j == 0 else "L"),
                               "toi_sec": toi, "goals": g, "assists": a, "points": g + a, "sog": sg})
                    att = float(rng.poisson(4))
                    xs.append({"game_id": gid, "player_id": pid, "attempts": att, "ixg": float(rng.gamma(2, 0.1)),
                               "ixg_ev": float(rng.gamma(2, 0.08)), "ixg_pp": float(rng.gamma(1, 0.05)), "att_ev": att - 1,
                               "att_pp": 1.0, "hd_att": float(rng.poisson(1)), "sog_pop": sg, "ixg_sog": float(rng.gamma(2, 0.1)),
                               "goals_pop": g})
                gpid = tid * 100 + (90 if rng.random() < 0.8 else 91)
                sa = float(rng.poisson(30))
                sv = float(rng.binomial(int(sa), 0.905))
                gl.append({"game_id": gid, "season_year": 2025, "game_type": 2, "date": date, "team_abbr": team, "team_id": tid, "is_home": is_home,
                           "player_id": gpid, "name": f"G{gpid}", "toi_sec": 3600.0, "starter": True, "shots_against": sa,
                           "saves": sv, "goals_against": sa - sv})
                xgl.append({"game_id": gid, "player_id": gpid, "shots_faced": sa, "xga": float(rng.gamma(9, 0.3)),
                            "ga_pop": sa - sv, "gsax": float(rng.normal(0, 0.8)), "sa_pk": float(rng.poisson(5)),
                            "hd_faced": float(rng.poisson(10))})
    masters = {"games": pd.DataFrame(games), "skaters": pd.DataFrame(sk), "goalies": pd.DataFrame(gl),
               "teams": pd.DataFrame()}
    xg = {"skater": pd.DataFrame(xs), "goalie": pd.DataFrame(xgl), "team": pd.DataFrame(xt)}
    return masters, xg


def hist_frames(masters, xg):
    tt = X.team_table(masters["games"], xg["team"])
    gt = X.goalie_table(masters["games"], masters["goalies"], xg["goalie"])
    st = X.skater_table(masters["games"], masters["skaters"], xg["skater"])
    return X.saves_features(gt, tt), X.skater_features(st, tt, gt, "expected")


def cut(masters, xg, day):
    keep = set(masters["games"].loc[masters["games"]["date"] < day, "game_id"])
    m = {k: (v[v["game_id"].isin(keep)] if len(v) else v) for k, v in masters.items()}
    return m, {k: v[v["game_id"].isin(keep)] for k, v in xg.items()}


def entities_from(hist_sk, hist_sv, teams):
    return {t: {"skaters": [(int(r.player_id), str(r.name), r.position) for r in hist_sk[hist_sk["team"] == t].itertuples()],
                "goalies": [(int(r.player_id), str(r.name)) for r in hist_sv[hist_sv["team"] == t].itertuples()]}
            for t in teams}


def live_for(masters, xg, day, ents=None):
    gday = masters["games"][masters["games"]["date"] == day]
    upcoming = [{"date": day, "home": h, "away": a} for h, a in zip(gday["home_abbr"], gday["away_abbr"])]
    m, x = cut(masters, xg, day)
    return LF.build_live(m, x, upcoming, entities=ents), upcoming


def assert_same(live, hist, cols, keys=("player_id", "team")):
    j = live.merge(hist, on=list(keys), suffixes=("_l", "_h"))
    assert len(j) == len(hist) == len(live) > 0
    for c in cols:
        a, b = j[f"{c}_l"].astype(float).to_numpy(), j[f"{c}_h"].astype(float).to_numpy()
        ok = (np.isnan(a) & np.isnan(b)) | np.isclose(a, b, rtol=1e-9, atol=1e-9)
        assert ok.all(), f"{c}: {int((~ok).sum())} rows differ"


@pytest.fixture(scope="module")
def world():
    masters, xg = league()
    sv, sk = hist_frames(masters, xg)
    return masters, xg, sv, sk


@pytest.mark.parametrize("day_idx", [12, 25, 38])
def test_live_features_equal_the_training_path_features(world, day_idx):
    masters, xg, sv, sk = world
    day = (BASE + pd.Timedelta(days=day_idx)).strftime("%Y-%m-%d")
    h_sk, h_sv = sk[sk["date"] == day], sv[sv["date"] == day]
    ents = entities_from(h_sk, h_sv, ABBR)
    live, _ = live_for(masters, xg, day, ents)
    assert_same(live["skater"], h_sk, X.SKATER_FEATURES)
    assert_same(live["saves"], h_sv, X.SAVES_FEATURES)
    assert (live["skater"]["game_id"] >= X.PLACEHOLDER_BASE).all() and (live["saves"]["game_id"] >= X.PLACEHOLDER_BASE).all()
    assert live["skater"][["goals", "assists", "points"]].isna().all().all()          # no outcome on an unplayed row


def test_live_features_ignore_everything_on_or_after_the_game_date(world):
    masters, xg, sv, sk = world
    day = (BASE + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    ents = entities_from(sk[sk["date"] == day], sv[sv["date"] == day], ABBR)
    clean, _ = live_for(masters, xg, day, ents)
    wild_m, wild_x = league(seed=99)                                  # a different future: same history through day 29? no -
    # keep the SAME history, replace the future: rows dated >= day come from another seed
    m2 = {k: v.copy() for k, v in masters.items()}
    for k in ("games", "skaters", "goalies"):
        other = wild_m[k]
        m2[k] = pd.concat([masters[k][masters[k]["date"] < day], other[other["date"] >= day]], ignore_index=True)
    x2 = {k: pd.concat([xg[k][xg[k]["game_id"].isin(set(masters["games"].loc[masters["games"]["date"] < day, "game_id"]))],
                        wild_x[k][wild_x[k]["game_id"].isin(set(wild_m["games"].loc[wild_m["games"]["date"] >= day, "game_id"]))]],
                       ignore_index=True) for k in xg}
    gday = masters["games"][masters["games"]["date"] == day]
    upcoming = [{"date": day, "home": h, "away": a} for h, a in zip(gday["home_abbr"], gday["away_abbr"])]
    m_cut, x_cut = cut(m2, x2, day)
    dirty = LF.build_live(m_cut, x_cut, upcoming, entities=ents)
    assert_same(dirty["skater"], clean["skater"], X.SKATER_FEATURES)
    assert_same(dirty["saves"], clean["saves"], X.SAVES_FEATURES)


def test_two_upcoming_games_for_a_team_still_build_and_the_first_is_unaffected(world):
    masters, xg, sv, sk = world
    d1 = (BASE + pd.Timedelta(days=36)).strftime("%Y-%m-%d")
    d2 = (BASE + pd.Timedelta(days=37)).strftime("%Y-%m-%d")
    ents = entities_from(sk[sk["date"] == d1], sv[sv["date"] == d1], ABBR)
    g1 = masters["games"][masters["games"]["date"] == d1].iloc[0]
    up1 = [{"date": d1, "home": g1.home_abbr, "away": g1.away_abbr}]
    up2 = up1 + [{"date": d2, "home": g1.home_abbr, "away": g1.away_abbr}]
    m, x = cut(masters, xg, d1)
    one = LF.build_live(m, x, up1, entities=ents)
    two = LF.build_live(m, x, up2, entities=ents)
    first_two = two["skater"][two["skater"]["date"] == d1]
    assert_same(first_two, one["skater"], X.SKATER_FEATURES)          # the later placeholder never feeds the earlier one
    assert len(two["skater"]) == 2 * len(one["skater"])


def test_current_entities_picks_recent_skaters_and_the_two_goalies_with_the_most_starts(world):
    masters, xg, sv, sk = world
    day = (BASE + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    e = LF.current_entities(masters, {"AAA"}, day)
    ids = {p for p, _, _ in e["AAA"]["skaters"]}
    assert ids == {100 + j for j in range(8)}
    assert 1 <= len(e["AAA"]["goalies"]) <= 2 and all(p in (190, 191) for p, _ in e["AAA"]["goalies"])
    e2 = LF.current_entities(masters, {"AAA"}, day, exclude={100})
    assert 100 not in {p for p, _, _ in e2["AAA"]["skaters"]}
    assert LF.current_entities(masters, {"AAA"}, "2000-01-01") == {}              # no history: nothing to score


def test_no_upcoming_games_returns_empty_frames(world):
    masters, xg, *_ = world
    out = LF.build_live(masters, xg, [])
    assert out["saves"].empty and out["skater"].empty and out["games"].empty
