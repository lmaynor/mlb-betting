"""NHL v2 features: sog_xg_l10, high_danger_sog_l10 (shot-quality proxy) and team_missing_sog_l10 (absent top-6 F / top-4 D).
Leakage, own-row exclusion, placeholder (train == serve) parity, NaN-safety, and that the default feature set is untouched."""
import numpy as np
import pandas as pd
import pytest

from nhl.props import features as F
from nhl.props import gamelog as G

V2 = F.V2_EXTRA
N_F, N_D = 9, 5


def synth(n_days=44, seed=1, skip=None):
    """4 teams x (9 F + 5 D); ranks fixed by TOI (F0 highest ... F8 lowest; D0 highest). `skip` = {player_id: set(game idx)}."""
    rng = np.random.default_rng(seed)
    skip = skip or {}
    base = pd.Timestamp("2025-10-10")
    teams = ["AAA", "BBB", "CCC", "DDD"]
    pairings = [(("AAA", "BBB"), ("CCC", "DDD")), (("AAA", "CCC"), ("BBB", "DDD")), (("AAA", "DDD"), ("BBB", "CCC"))]
    rows, gid, gi = [], 2025020000, 0
    for d in range(0, n_days, 2):
        date = (base + pd.Timedelta(days=d)).strftime("%Y-%m-%d")
        for home, away in pairings[(d // 2) % 3]:
            gid += 1
            gi += 1
            for team, opp, is_home in ((home, away, 1), (away, home, 0)):
                for k in range(N_F + N_D):
                    pid = teams.index(team) * 100 + k
                    if gi in skip.get(pid, set()):
                        continue
                    is_d = k >= N_F
                    rank = k - N_F if is_d else k
                    toi = (1400 - 110 * rank) if is_d else (1250 - 60 * rank)
                    sog = float(rng.poisson(1.0 + (0.5 if not is_d else 0.0) + 0.4 * (rank == 0)))
                    rows.append({"player_id": pid, "game_id": gid, "date": date, "team": team, "opp": opp,
                                 "is_home": is_home, "position": "D" if is_d else ("C" if k % 3 == 0 else "L"),
                                 "toi_sec": float(toi + rng.integers(-5, 6)), "sog": sog,
                                 "attempts": sog * 2.0, "pp_attempts": 0.0, "goals": 0, "assists": 0,
                                 "sog_xg": sog * (0.12 + 0.01 * rng.random()), "hd_sog": float(rng.binomial(int(sog), 0.3))})
    return pd.DataFrame(rows, columns=G.GAMELOG_COLS)


@pytest.fixture(scope="module")
def world():
    # F0 (top forward) and F7 (not top-6) and D4 (not top-4) and D0 (top D) of AAA all miss a long stretch
    skip = {0: set(range(15, 24)), 7: set(range(15, 24)), 13: set(range(15, 24)), 9: set(range(15, 24)),
            # the very last game day (gi 43/44): AAA F2 + D3, BBB F3 + D0 sit it out (serve-parity case)
            2: {43, 44}, 12: {43, 44}, 103: {43, 44}, 109: {43, 44}}
    gl = synth(skip=skip)
    return gl, F.build_features(gl, extras=True)


def _row(feat, pid, gid):
    return feat[(feat["player_id"] == pid) & (feat["game_id"] == gid)].iloc[0]


def _trailing_sog(gl, pid, date):
    h = gl[(gl["player_id"] == pid) & (gl["date"] < date)].sort_values(["date", "game_id"]).tail(10)
    return h["sog"].mean()


def test_default_feature_set_unchanged_and_v2_is_opt_in(world):
    gl, f2 = world
    f1 = F.build_features(gl)
    assert list(f1.columns) == F.KEYS + F.FEATURES + ["position", "sog", "toi_sec"]
    assert not set(V2) & set(f1.columns)
    assert F.FEATURES_V2 == F.FEATURES + V2 and F.feature_list("v1") == F.FEATURES and F.feature_list("v2") == F.FEATURES_V2
    pd.testing.assert_frame_equal(f1[F.FEATURES].astype(float), f2[F.FEATURES].astype(float))   # v1 numbers identical
    with pytest.raises(ValueError):
        F.feature_list("v9")


def test_only_absent_TOP_players_count_and_by_their_trailing_sog(world):
    gl, f2 = world
    gids = sorted(gl["game_id"].unique())
    # game index 19 (inside the stretch): AAA misses F0 (top-6 F), F7 (7th F -> not top-6), D0 (top-4 D), D4 (5th D -> not top-4)
    g = gl[gl["team"] == "AAA"].sort_values(["date", "game_id"])
    gid = g["game_id"].unique()[8]            # some AAA game in the middle of the stretch
    date = g[g["game_id"] == gid]["date"].iloc[0]
    present = set(g[g["game_id"] == gid]["player_id"])
    absent = {p for p in (0, 7, 9, 13) if p not in present}
    assert absent == {0, 7, 9, 13}, "fixture drifted: pick a game inside the skip window"
    other = int(sorted(present)[0])
    expect = _trailing_sog(gl, 0, date) + _trailing_sog(gl, 9, date)          # F0 and D0 only (ranked by trailing TOI)
    got = _row(f2, other, gid)["team_missing_sog_l10"]
    assert got == pytest.approx(expect)


def test_healthy_lineup_is_zero_and_first_games_are_nan(world):
    gl, f2 = world
    assert (f2["team_missing_sog_l10"].dropna() >= 0).all() and (f2["team_missing_sog_l10"] == 0).any()
    first = f2[f2["date"] == f2["date"].min()]
    assert first["team_missing_sog_l10"].isna().all()                   # no previous team game -> unknown, not 0


def test_scrambling_outcomes_never_changes_features_at_or_before_the_game(world):
    gl, f2 = world
    cutoff = sorted(gl["date"].unique())[18]
    mut = gl.copy()
    late = mut["date"] >= cutoff                                          # everyone STILL dressed; only outcomes change
    mut.loc[late, ["sog", "toi_sec", "attempts", "pp_attempts", "sog_xg", "hd_sog"]] = 777.0
    f3 = F.build_features(mut, extras=True)
    a = f2.set_index(["player_id", "game_id"])
    b = f3.set_index(["player_id", "game_id"])
    early = a.index[a["date"] <= cutoff]
    pd.testing.assert_frame_equal(a.loc[early, F.FEATURES_V2].astype(float), b.loc[early, F.FEATURES_V2].astype(float))
    later = a.index[a["date"] > cutoff]
    for c in ("sog_xg_l10", "high_danger_sog_l10"):
        assert not np.allclose(a.loc[later, c].to_numpy(float), b.loc[later, c].to_numpy(float), equal_nan=True)


def test_who_dressed_matters_for_others_and_own_stats_never_feed_own_feature(world):
    gl, f2 = world
    g = gl[gl["team"] == "AAA"].sort_values(["date", "game_id"])
    gid = g["game_id"].unique()[8]
    other = int(sorted(set(g[g["game_id"] == gid]["player_id"]))[1])
    # (a) F0 plays that game after all -> the other player's feature drops by F0's trailing sog
    date = g[g["game_id"] == gid]["date"].iloc[0]
    extra = gl[(gl["player_id"] == 1) & (gl["game_id"] == gid)].copy()
    extra["player_id"] = 0
    extra["position"] = "C"
    f_back = F.build_features(pd.concat([gl, extra], ignore_index=True), extras=True)
    drop = _row(f2, other, gid)["team_missing_sog_l10"] - _row(f_back, other, gid)["team_missing_sog_l10"]
    assert drop == pytest.approx(_trailing_sog(gl, 0, date))
    # (b) the row's OWN outcome in the game does not change his own features
    mut = gl.copy()
    own = (mut["player_id"] == other) & (mut["game_id"] == gid)
    mut.loc[own, ["sog", "toi_sec", "sog_xg", "hd_sog", "attempts"]] = 999.0
    f_own = F.build_features(mut, extras=True)
    pd.testing.assert_series_equal(_row(f2, other, gid)[F.FEATURES_V2].astype(float),
                                   _row(f_own, other, gid)[F.FEATURES_V2].astype(float))


def test_placeholder_rows_get_the_same_features_the_played_game_gets_when_given_the_same_absent_set(world):
    """Serve path: upcoming game = NaN-outcome placeholder rows (quoted players only) + the injury/scratch list."""
    gl, f2 = world
    last = gl["date"].max()
    history = gl[gl["date"] < last]
    played = gl[gl["date"] == last]
    teams = played["team"].unique()
    # training-path absent set per team: rotation players not in the last game's lineup
    trained = f2[f2["date"] == last].set_index(["player_id", "game_id"])
    ph = played.groupby("team").head(3).copy()                            # only 3 quoted players per team
    ph[["sog", "toi_sec", "attempts", "pp_attempts", "goals", "assists", "sog_xg", "hd_sog"]] = np.nan
    prev = history[history["date"] >= sorted(history["date"].unique())[-5]]
    inj = {}
    for t in teams:
        rot = set(prev[prev["team"] == t]["player_id"])
        inj[t] = rot - set(played[played["team"] == t]["player_id"])
    served = F.build_features(pd.concat([history, ph], ignore_index=True), extras=True, availability=inj)
    served = served.set_index(["player_id", "game_id"]).loc[ph.set_index(["player_id", "game_id"]).index]
    cols = F.FEATURES_V2
    pd.testing.assert_frame_equal(served[cols].astype(float), trained.loc[served.index, cols].astype(float))
    assert any(len(v) for v in inj.values())                              # non-vacuous: someone really was out
    # lineup mode cannot know a placeholder game's lineup -> NaN, never a guess
    nolive = F.build_features(pd.concat([history, ph], ignore_index=True), extras=True)
    assert nolive[nolive["game_id"].isin(ph["game_id"])]["team_missing_sog_l10"].isna().all()
    # a team missing from the mapping, or an unusable report ({}), is unknown -> NaN
    assert F.build_features(pd.concat([history, ph], ignore_index=True), extras=True, availability={}) \
        .pipe(lambda x: x[x["game_id"].isin(ph["game_id"])]["team_missing_sog_l10"].isna().all())


def test_live_path_drops_the_query_player_from_the_absent_set(world):
    gl, f2 = world
    last = gl["date"].max()
    history = gl[gl["date"] < last]
    ph = gl[(gl["date"] == last) & (gl["team"] == "AAA")].head(2).copy()
    ph[["sog", "toi_sec", "attempts", "pp_attempts", "goals", "assists", "sog_xg", "hd_sog"]] = np.nan
    me = int(ph["player_id"].iloc[0])
    a = F.build_features(pd.concat([history, ph], ignore_index=True), extras=True, availability={"AAA": {me, 0}})
    b = F.build_features(pd.concat([history, ph], ignore_index=True), extras=True, availability={"AAA": {0}})
    ka = a[(a["player_id"] == me) & (a["game_id"] == ph["game_id"].iloc[0])]["team_missing_sog_l10"].iloc[0]
    kb = b[(b["player_id"] == me) & (b["game_id"] == ph["game_id"].iloc[0])]["team_missing_sog_l10"].iloc[0]
    assert ka == pytest.approx(kb)


def test_shot_features_are_prior_games_only_and_nan_safe(world):
    gl, f2 = world
    p = gl[gl["player_id"] == 3].sort_values(["date", "game_id"]).reset_index(drop=True)
    k = 14
    row = p.iloc[k]
    assert _row(f2, 3, row["game_id"])["sog_xg_l10"] == pytest.approx(p["sog_xg"].iloc[k - 10:k].mean())
    assert _row(f2, 3, row["game_id"])["high_danger_sog_l10"] == pytest.approx(p["hd_sog"].iloc[k - 10:k].mean())
    # a season/game set with no shot geometry -> NaN features, no crash; an old frame without the columns -> NaN too
    nos = gl.copy()
    nos[["sog_xg", "hd_sog"]] = np.nan
    fn = F.build_features(nos, extras=True)
    assert fn["sog_xg_l10"].isna().all() and fn["high_danger_sog_l10"].isna().all() and fn["team_missing_sog_l10"].notna().any()
    old = F.build_features(gl.drop(columns=["sog_xg", "hd_sog"]), extras=True)
    assert old["sog_xg_l10"].isna().all()


# ------------------------------------------------------------------------------------ gamelog shot-quality loader
def _masters():
    games = pd.DataFrame([{"game_id": 1, "date": "2025-10-10", "game_type": 2, "away_abbr": "AAA", "home_abbr": "BBB"},
                          {"game_id": 2, "date": "2025-10-12", "game_type": 2, "away_abbr": "AAA", "home_abbr": "BBB"},
                          {"game_id": 3, "date": "2025-10-14", "game_type": 2, "away_abbr": "AAA", "home_abbr": "BBB"}])
    sk = pd.DataFrame([{"game_id": g, "date": d, "team_abbr": "AAA", "is_home": False, "player_id": p, "position": "C",
                        "toi_sec": 1000.0, "played": True, "goals": 0, "assists": 0, "sog": 2}
                       for g, d in ((1, "2025-10-10"), (2, "2025-10-12"), (3, "2025-10-14")) for p in (7, 8)])
    base = dict(skaters_for=5, skaters_against=5, empty_net=False)
    shots = pd.DataFrame([
        # game 1: P7 has a slot SOG, a point SOG, a block and a miss; P8 only an empty-net goal
        {"game_id": 1, "event": "sog", "shooter_id": 7, "dist_ft": 10.0, "angle_deg": 10.0, **base},
        {"game_id": 1, "event": "sog", "shooter_id": 7, "dist_ft": 60.0, "angle_deg": 5.0, **base},
        {"game_id": 1, "event": "block", "shooter_id": 7, "dist_ft": 8.0, "angle_deg": 3.0, **base},
        {"game_id": 1, "event": "miss", "shooter_id": 7, "dist_ft": 12.0, "angle_deg": 8.0, **base},
        {"game_id": 1, "event": "goal", "shooter_id": 8, "dist_ft": 50.0, "angle_deg": 2.0, "skaters_for": 5,
         "skaters_against": 5, "empty_net": "True"},                          # CSV round-trip string
        # game 2: shots exist but P7/P8 had none that qualify; P8 has a far shot only
        {"game_id": 2, "event": "sog", "shooter_id": 8, "dist_ft": 70.0, "angle_deg": 40.0, **base},
        # game 3: no geometry at all
        {"game_id": 3, "event": "sog", "shooter_id": 7, "dist_ft": np.nan, "angle_deg": np.nan, **base},
    ])
    return games, sk, shots


def test_xg_proxy_is_monotone_and_nan_safe():
    assert G.xg_proxy(10, 10) > G.xg_proxy(30, 10) > G.xg_proxy(60, 10)
    assert G.xg_proxy(15, 5) > G.xg_proxy(15, 60)
    assert np.isnan(G.xg_proxy(np.nan, 5)) and 0 < float(G.xg_proxy(10, 10)) < 0.5


def test_from_masters_shot_quality_columns():
    games, sk, shots = _masters()
    gl = G.from_masters(games, sk, shots).set_index(["game_id", "player_id"])
    p7g1 = gl.loc[(1, 7)]
    assert p7g1["sog_xg"] == pytest.approx(float(G.xg_proxy(10, 10)) + float(G.xg_proxy(60, 5)))   # SOG only: no block/miss
    assert p7g1["hd_sog"] == 1.0                                                                    # the slot shot
    assert gl.loc[(1, 8), "sog_xg"] == 0.0 and gl.loc[(1, 8), "hd_sog"] == 0.0                      # empty net excluded, not NaN
    assert gl.loc[(2, 7), "sog_xg"] == 0.0                                                          # game has geometry, no shot
    assert gl.loc[(2, 8), "hd_sog"] == 0.0                                                          # far shot is not high danger
    assert np.isnan(gl.loc[(3, 7), "sog_xg"]) and np.isnan(gl.loc[(3, 8), "hd_sog"])                # no geometry -> unknown
    # no shots table at all -> all NaN (NaN-safe), and the legacy columns are untouched
    none = G.from_masters(games, sk)
    assert none["sog_xg"].isna().all() and none["hd_sog"].isna().all() and list(none.columns) == G.GAMELOG_COLS
