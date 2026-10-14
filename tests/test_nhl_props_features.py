"""NHL SOG features: leakage safety, train/serve parity (placeholder rows), no market features."""
import numpy as np
import pandas as pd
import pytest

from nhl.props import features as F
from nhl.props.gamelog import GAMELOG_COLS, game_type_from_id, team_games

TEAMS = ["AAA", "BBB", "CCC", "DDD"]


def synth_gamelog(n_days=40, seed=0):
    """4 teams, 2 games/day alternating pairings, 6 skaters per team; deterministic shot counts."""
    rng = np.random.default_rng(seed)
    rows, gid = [], 2025020000
    base = pd.Timestamp("2025-10-10")
    pairings = [(("AAA", "BBB"), ("CCC", "DDD")), (("AAA", "CCC"), ("BBB", "DDD")), (("AAA", "DDD"), ("BBB", "CCC"))]
    for d in range(n_days):
        if d % 2 == 1:          # off day for everyone -> rest_days of 2 is common
            continue
        date = (base + pd.Timedelta(days=d)).strftime("%Y-%m-%d")
        for home, away in pairings[(d // 2) % 3]:
            gid += 1
            for team, opp, is_home in ((home, away, 1), (away, home, 0)):
                for k in range(6):
                    pid = TEAMS.index(team) * 100 + k
                    rows.append({"player_id": pid, "game_id": gid, "date": date, "team": team, "opp": opp,
                                 "is_home": is_home, "position": "D" if k < 2 else ("C" if k < 4 else "L"),
                                 "toi_sec": float(rng.integers(600, 1300)), "sog": float(rng.poisson(1.0 + 0.3 * (k >= 2))),
                                 "attempts": float(rng.poisson(2.5)), "pp_attempts": float(rng.poisson(0.4)),
                                 "goals": 0, "assists": 0})
    return pd.DataFrame(rows, columns=GAMELOG_COLS)


def test_game_type_from_id():
    assert game_type_from_id(2025020780) == 2 and game_type_from_id(2025010001) == 1 and game_type_from_id(2024030111) == 3


def test_no_market_derived_feature_in_the_model():
    banned = ("line", "price", "odds", "implied", "consensus", "prob", "total", "fair")
    assert not [f for f in F.FEATURES if any(b in f for b in banned)]


def test_features_never_see_their_own_game_or_later_ones():
    gl = synth_gamelog()
    base = F.build_features(gl).set_index(["player_id", "game_id"])
    # pick a mid-history target row and corrupt its OWN outcome + every later game's outcomes
    target = gl[(gl.player_id == 2) & (gl.date == gl.date.unique()[10])].iloc[0]
    tdate = target["date"]
    mut = gl.copy()
    m_own_and_later = mut["date"] >= tdate
    mut.loc[m_own_and_later, ["sog", "toi_sec", "attempts", "pp_attempts"]] = 9999.0
    new = F.build_features(mut).set_index(["player_id", "game_id"])
    key = (int(target["player_id"]), int(target["game_id"]))
    a, b = base.loc[key, F.FEATURES], new.loc[key, F.FEATURES]
    pd.testing.assert_series_equal(a.astype(float), b.astype(float), check_names=False)
    # ... and EVERY row dated on/before the corrupted date is also unchanged (nothing flows backwards)
    early = base.index[base["date"] <= tdate]
    pd.testing.assert_frame_equal(base.loc[early, F.FEATURES].astype(float), new.loc[early, F.FEATURES].astype(float))
    # sanity: the mutation DOES change later rows (so the test is not vacuous)
    later = base.index[base["date"] > tdate][-1]
    assert not np.isclose(base.loc[later, "sog_l5"], new.loc[later, "sog_l5"])


def test_rolling_values_are_prior_games_only():
    gl = synth_gamelog()
    f = F.build_features(gl).set_index(["player_id", "game_id"])
    p = gl[gl.player_id == 3].sort_values(["date", "game_id"]).reset_index(drop=True)
    k = 12
    row = p.iloc[k]
    expect = p["sog"].iloc[k - 5:k].mean()
    assert f.loc[(3, int(row["game_id"])), "sog_l5"] == pytest.approx(expect)
    assert f.loc[(3, int(row["game_id"])), "n_prior"] == k
    # first appearance has no history
    first = p.iloc[0]
    assert np.isnan(f.loc[(3, int(first["game_id"])), "sog_l5"]) and f.loc[(3, int(first["game_id"])), "n_prior"] == 0


def test_team_features_use_prior_games_only_and_rest_days():
    gl = synth_gamelog()
    f = F.build_features(gl)
    tg = team_games(gl)
    t = tg[tg.team == "AAA"].reset_index(drop=True)
    row = t.iloc[8]
    expect_sf = t["sf"].iloc[0:8].mean() if len(t.iloc[:8]) <= 10 else t["sf"].iloc[-10:].mean()
    got = f[(f.team == "AAA") & (f.game_id == row["game_id"])]["team_sf_l10"].iloc[0]
    assert got == pytest.approx(expect_sf)
    # games are two days apart -> rest_days 2
    assert f[(f.team == "AAA") & (f.game_id == row["game_id"])]["rest_days"].iloc[0] == 2


def test_placeholder_row_features_equal_the_features_once_the_game_is_played():
    """Train/serve parity: an upcoming game (NaN outcome) gets the SAME features it will have after it is played."""
    gl = synth_gamelog()
    last_date = gl["date"].max()
    played = gl[gl["date"] == last_date]
    history = gl[gl["date"] < last_date]
    ph = played.copy()
    ph[["sog", "toi_sec", "attempts", "pp_attempts", "goals", "assists"]] = np.nan
    served = F.build_features(pd.concat([history, ph], ignore_index=True)).set_index(["player_id", "game_id"])
    trained = F.build_features(gl).set_index(["player_id", "game_id"])
    idx = played.set_index(["player_id", "game_id"]).index
    pd.testing.assert_frame_equal(served.loc[idx, F.FEATURES].astype(float), trained.loc[idx, F.FEATURES].astype(float))


def test_league_environment_feature_is_prior_days_only():
    gl = synth_gamelog()
    f = F.build_features(gl)
    first_day = f[f["date"] == f["date"].min()]
    assert first_day["lg_sf_l30"].isna().all()
    d2 = sorted(f["date"].unique())[1]
    day1_mean = team_games(gl).query("date == @f.date.min()")["sf"].mean()
    assert f[f["date"] == d2]["lg_sf_l30"].iloc[0] == pytest.approx(day1_mean)
