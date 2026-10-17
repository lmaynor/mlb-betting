"""NBA availability features (team_missing_*): definition, leakage, own-row exclusion, train/live parity, versioning."""
import numpy as np
import pandas as pd
import pytest

from nba.props import config as C
from nba.props import features as F
from tests.nba_props_helpers import et_day_of, make_masters, prepared, truncate_before

AV = list(C.AVAILABILITY_FEATURES)


@pytest.fixture(scope="module")
def world():
    # BOS-0 sits a long stretch -> a clear "rotation player absent" situation for every other BOS row in it
    pb, gm, tips = make_masters(skip={"BOS-0": set(range(14, 30))})
    rows, tg = prepared(pb, gm, tips)
    ctx = F.FeatureContext(rows, tg)
    X, meta = F.build_training_frame(rows, tg, ctx, availability="history")
    return pb, gm, tips, rows, tg, ctx, X, meta


def _eq(a, b, cols=None):
    cols = cols or list(a.index)
    for c in cols:
        x, y = float(a[c]), float(b[c])
        assert (np.isnan(x) and np.isnan(y)) or np.isclose(x, y, rtol=1e-9, atol=1e-9), (c, x, y)


def _absent_case(world):
    """A (row index, team, date, absent pid) where a rotation player sat the game and the row is some OTHER player."""
    pb, gm, tips, rows, tg, ctx, X, meta = world
    for ridx in meta.index:
        r = rows.loc[ridx]
        ab = ctx.absent_set(r["team"], r["date"])
        if ab and "BOS-0" in ab and r["team"] == "BOS" and r["player_id"] != "BOS-0":
            return ridx, r, ab
    pytest.fail("no absent-rotation case in the synthetic world")


def _trailing(rows, pid, day):
    h = rows[(rows["player_id"] == pid) & (rows["date"] < day)].sort_values(["tip_sort", "game_id"]).tail(10)
    return (h["min"].mean(), h["points"].mean() + C.USG_PROXY_AST_WEIGHT * h["assists"].mean() + h["rebounds"].mean())


def test_default_feature_set_is_unchanged_and_availability_is_opt_in(world):
    pb, gm, tips, rows, tg, ctx, X, meta = world
    Xd, _ = F.build_training_frame(rows, tg, ctx)
    assert not set(AV) & set(Xd.columns)
    assert set(AV) <= set(X.columns)
    assert list(X.columns[:len(Xd.columns)]) == list(Xd.columns) or set(Xd.columns) <= set(X.columns)
    # every v1 column is identical with or without the opt-in block (old artifacts read the same numbers)
    for c in Xd.columns:
        np.testing.assert_allclose(X[c].to_numpy(float), Xd[c].to_numpy(float), equal_nan=True)
    assert F.feature_columns("v1") is None
    v2 = F.feature_columns("v2", base=list(Xd.columns))
    assert v2[:len(Xd.columns)] == list(Xd.columns) and v2[-3:] == AV
    with pytest.raises(ValueError):
        F.feature_columns("v9")


def test_definition_matches_a_hand_computation(world):
    """Sum of the absent rotation players' trailing-L10 minutes and (PTS + 1.5 AST + REB) per game, strictly pre-game."""
    pb, gm, tips, rows, tg, ctx, X, meta = world
    ridx, r, ab = _absent_case(world)
    exp_min = exp_usg = 0.0
    n = 0
    for pid in ab:
        mn, us = _trailing(rows, pid, r["date"])
        if np.isfinite(mn) and mn >= C.ROTATION_MIN_L10:
            exp_min += mn
            exp_usg += us
            n += 1
    assert n >= 1
    assert X.loc[ridx, "team_missing_min_l10"] == pytest.approx(exp_min)
    assert X.loc[ridx, "team_missing_usg_proxy_l10"] == pytest.approx(exp_usg)
    assert X.loc[ridx, "team_missing_n_rotation"] == n


def test_nobody_absent_gives_zero_and_unknown_gives_nan(world):
    pb, gm, tips, rows, tg, ctx, X, meta = world
    f = X.join(meta[["player_id", "team", "date"]])
    assert (f["team_missing_min_l10"].dropna() >= 0).all()
    assert (f["team_missing_min_l10"] == 0).any()                    # a fully-healthy rotation is 0, not NaN
    # a game that is not in the history cannot be derived from "who dressed" -> NaN, not 0
    q = pd.DataFrame([{"player_id": "BOS-1", "date": pd.Timestamp("2026-06-01"), "team": "BOS", "opp": "LAL",
                       "is_home": True, "season_year": 2025, "season_type": "Regular Season"}])
    assert ctx.features(q, availability="history")[AV].isna().all().all()


def test_scrambling_a_games_outcomes_never_changes_features_at_or_before_it(world):
    """Stats and minutes of every game on/after the cutoff are scrambled WHILE KEEPING who played (inactives are known
    pre-tip). Features of every row dated on/before the cutoff must not move; later rows must (non-vacuous)."""
    pb, gm, tips, rows, tg, ctx, X, meta = world
    cutoff = pd.Timestamp(rows["date"].sort_values().iloc[len(rows) // 2])
    pb2 = pb.copy()
    day = et_day_of(gm["game_id"], tips)
    late = (pb2["game_id"].map(day) >= cutoff) & pb2["played"]
    for c in ("points", "rebounds", "assists", "three_points_made", "field_goals_attempted", "free_throws_attempted",
              "turnovers", "rebounds_offensive"):
        pb2.loc[late, c] = pb2.loc[late, c] * 9 + 4
    pb2.loc[late, "minutes"] = "41:10"
    rows2, tg2 = prepared(pb2, gm, tips)
    X2, meta2 = F.build_training_frame(rows2, tg2, availability="history")
    a = X[AV].join(meta[["player_id", "game_id", "date"]]).set_index(["player_id", "game_id"])
    b = X2[AV].join(meta2[["player_id", "game_id", "date"]]).set_index(["player_id", "game_id"])
    early = a.index[a["date"] <= cutoff]
    np.testing.assert_allclose(a.loc[early, AV].to_numpy(float), b.loc[early, AV].to_numpy(float), equal_nan=True)
    later = a.index[a["date"] > cutoff]
    assert not np.allclose(a.loc[later, AV].to_numpy(float), b.loc[later, AV].to_numpy(float), equal_nan=True)


def test_who_dressed_changes_other_players_features_but_a_players_own_minutes_do_not_change_his_own(world):
    pb, gm, tips, rows, tg, ctx, X, meta = world
    ridx, r, ab = _absent_case(world)
    # (a) put the absent player BACK into the game: the OTHER player's missing-minutes feature must drop
    gid = r["game_id"]
    pb2 = pb.copy()
    m = (pb2["game_id"] == gid) & (pb2["player_id"] == "BOS-0")
    pb2.loc[m, ["minutes", "played", "points"]] = ["30:00", True, 12]
    rows2, tg2 = prepared(pb2, gm, tips)
    X2, meta2 = F.build_training_frame(rows2, tg2, availability="history")
    k2 = meta2[(meta2["player_id"] == r["player_id"]) & (meta2["game_id"] == gid)].index[0]
    assert X2.loc[k2, "team_missing_min_l10"] < X.loc[ridx, "team_missing_min_l10"]
    # (b) a player's OWN minutes in the game feed nobody's feature for that same game, least of all his own
    pb3 = pb.copy()
    own = (pb3["game_id"] == gid) & (pb3["player_id"] == r["player_id"])
    pb3.loc[own, ["minutes", "points", "rebounds", "assists"]] = ["47:00", 71, 30, 20]
    rows3, tg3 = prepared(pb3, gm, tips)
    X3, meta3 = F.build_training_frame(rows3, tg3, availability="history")
    k3 = meta3[(meta3["player_id"] == r["player_id"]) & (meta3["game_id"] == gid)].index[0]
    _eq(X.loc[ridx], X3.loc[k3], AV)


def test_live_path_equals_training_path_for_the_same_absent_set(world):
    """Parity: history truncated to before the game (live), features(availability={team: absent}) == training values."""
    pb, gm, tips, rows, tg, ctx, X, meta = world
    checked = 0
    for ridx in meta.index[::17]:
        r = rows.loc[ridx]
        absent = ctx.absent_set(r["team"], r["date"])
        if absent is None:
            continue
        pb_t, gm_t, tips_t = truncate_before(pb, gm, tips, pd.Timestamp(r["date"]))
        rows_t, tg_t = prepared(pb_t, gm_t, tips_t)
        ctx_t = F.FeatureContext(rows_t, tg_t)
        q = pd.DataFrame([{c: r[c] for c in F.ID_COLS}])
        live = ctx_t.features(q, availability={r["team"]: absent}).iloc[0]
        _eq(X.loc[ridx], live, AV)
        # extra non-rotation ids on the injury list change nothing; other teams' lists are irrelevant
        other = "MIA" if r["team"] != "MIA" else "BOS"
        noisy = ctx_t.features(q, availability={r["team"]: set(absent) | {"nobody-1", "nobody-2"}, other: {"x"}}).iloc[0]
        _eq(live, noisy, AV)
        checked += 1
    assert checked >= 8
    assert any(len(ctx.absent_set(rows.loc[i, "team"], rows.loc[i, "date"]) or ()) for i in meta.index)


def test_live_path_excludes_the_query_player_even_if_he_is_on_the_injury_list(world):
    pb, gm, tips, rows, tg, ctx, X, meta = world
    ridx, r, ab = _absent_case(world)
    pb_t, gm_t, tips_t = truncate_before(pb, gm, tips, pd.Timestamp(r["date"]))
    rows_t, tg_t = prepared(pb_t, gm_t, tips_t)
    ctx_t = F.FeatureContext(rows_t, tg_t)
    # query = BOS-0 himself while BOS-0 is on the list: his feature is the SUM OVER THE OTHERS (not himself)
    q = pd.DataFrame([{**{c: r[c] for c in F.ID_COLS}, "player_id": "BOS-0"}])
    with_self = ctx_t.features(q, availability={"BOS": ab}).iloc[0]
    others = ctx_t.features(q, availability={"BOS": set(ab) - {"BOS-0"}}).iloc[0]
    _eq(with_self, others, AV)
    # and for a different player the absent BOS-0 DOES count
    q2 = pd.DataFrame([{**{c: r[c] for c in F.ID_COLS}, "player_id": "BOS-1"}])
    assert ctx_t.features(q2, availability={"BOS": ab}).iloc[0]["team_missing_min_l10"] > \
        ctx_t.features(q2, availability={"BOS": set()}).iloc[0]["team_missing_min_l10"]


def test_unknown_availability_is_nan_not_healthy(world):
    pb, gm, tips, rows, tg, ctx, X, meta = world
    r = rows.loc[meta.index[len(meta) // 2]]
    q = pd.DataFrame([{c: r[c] for c in F.ID_COLS}])
    assert ctx.features(q, availability={}).iloc[0][AV].isna().all()                 # report unusable -> {} -> NaN
    assert ctx.features(q, availability={"MIA": set()}).iloc[0][AV].isna().all()     # this team not covered -> NaN
    assert (ctx.features(q, availability={r["team"]: set()}).iloc[0][AV].iloc[:2] == 0).all()   # known healthy -> 0
    with pytest.raises(ValueError):
        ctx.features(q, availability="bogus")


def test_offseason_gap_is_nan_not_a_guess():
    pb1, gm1, tips1 = make_masters(n_days=40, start="2025-03-01", season_year=2024)
    pb2, gm2, tips2 = make_masters(n_days=40, start="2025-11-01", season_year=2025, seed=9)
    for d in (gm2, pb2):
        d["game_id"] = "n" + d["game_id"]
    tips2.index = ["n" + i for i in tips2.index]
    pb, gm, tips = pd.concat([pb1, pb2]), pd.concat([gm1, gm2]), pd.concat([tips1, tips2])
    rows, tg = prepared(pb, gm, tips)
    X, meta = F.build_training_frame(rows, tg, availability="history")
    f = X.join(meta[["date", "team"]])
    first_new = f[f["date"] == f["date"][f["date"] >= pd.Timestamp("2025-11-01")].min()]
    assert first_new[AV].isna().all().all()
    assert f[f["date"] > first_new["date"].iloc[0] + pd.Timedelta(days=10)]["team_missing_min_l10"].notna().any()


def test_query_dates_of_any_datetime_resolution_work():
    """Regression for the pandas-3 MergeError: Timestamps parsed from strings are datetime64[us/s], history is [ns]."""
    pb, gm, tips = make_masters()
    rows, tg = prepared(pb, gm, tips)
    ctx = F.FeatureContext(rows, tg)
    base = {"player_id": "BOS-1", "team": "BOS", "opp": "LAL", "is_home": True, "season_year": 2025,
            "season_type": "Regular Season"}
    outs = []
    for unit in ("s", "us", "ns"):
        d = np.datetime64("2025-12-05", unit)
        q = pd.DataFrame([{**base, "date": d}])
        assert q["date"].dtype.kind == "M"
        outs.append(ctx.features(q).iloc[0])
    for o in outs[1:]:
        _eq(outs[0], o)
    # and a history whose own date column is datetime64[us]
    rows_us = rows.assign(date=rows["date"].astype("datetime64[us]"))
    tg_us = tg.assign(date=tg["date"].astype("datetime64[us]"))
    ctx_us = F.FeatureContext(rows_us, tg_us)
    _eq(outs[0], ctx_us.features(pd.DataFrame([{**base, "date": pd.Timestamp("2025-12-05")}])).iloc[0])


def test_availability_from_injuries_maps_espn_ids_through_the_bridge():
    class FakeBridge:
        def sb_id(self, e):
            return {1: ("sb-1", "ok"), 2: ("sb-2", "ok")}.get(e, (None, "unknown"))

    m = F.availability_from_injuries({"BOS": {1, 99}, "LAL": set(), "MIA": [2]}, FakeBridge())
    assert m == {"BOS": {"sb-1"}, "LAL": set(), "MIA": {"sb-2"}}


def test_weighted_live_availability_is_the_expectation_of_the_absence_indicator(world):
    """{id: P(absent)} scales each listed rotation player's contribution; weight 1.0 equals the plain set path and
    weight 0.35 gives 0.35x the sums (the features are linear in the absence indicators)."""
    pb, gm, tips, rows, tg, ctx, X, meta = world
    ridx, r, ab = _absent_case(world)
    pb_t, gm_t, tips_t = truncate_before(pb, gm, tips, pd.Timestamp(r["date"]))
    rows_t, tg_t = prepared(pb_t, gm_t, tips_t)
    ctx_t = F.FeatureContext(rows_t, tg_t)
    q = pd.DataFrame([{c: r[c] for c in F.ID_COLS}])
    plain = ctx_t.features(q, availability={r["team"]: set(ab)}).iloc[0]
    full = ctx_t.features(q, availability={r["team"]: {p: 1.0 for p in ab}}).iloc[0]
    part = ctx_t.features(q, availability={r["team"]: {p: 0.35 for p in ab}}).iloc[0]
    zero = ctx_t.features(q, availability={r["team"]: {p: 0.0 for p in ab}}).iloc[0]
    _eq(plain, full, AV)
    for c in AV:
        assert float(part[c]) == pytest.approx(0.35 * float(plain[c]))
        assert float(zero[c]) == 0.0
    assert float(plain["team_missing_min_l10"]) > 0


def test_espn_status_weights_and_bridge_mapping():
    from nba.data import espn_injuries as EI
    fr = pd.DataFrame({"team_abbrev": ["BOS", "BOS", "BOS", "BOS", "MIA"], "espn_player_id": [1, 2, 3, 4, 5],
                       "status": ["Out", "Doubtful", "Questionable", "Probable", "Day-To-Day"]})
    fr.attrs["ok"] = True
    w = EI.absence_weights_by_team(fr)
    assert w["BOS"] == {1: 1.0, 2: 0.75, 3: 0.35}                  # Probable carries no weight
    assert w["MIA"] == {5: 0.25} and w["LAL"] == {}                 # every team keyed when the report is usable
    bad = fr.copy()
    bad.attrs["ok"] = False
    assert EI.absence_weights_by_team(bad) == {}                    # unknown is not healthy

    class Bridge:
        def sb_id(self, e):
            return (f"sb{e}", None) if e != 2 else (None, "unbridged")

    out = F.availability_from_injuries({"BOS": w["BOS"], "LAL": {}}, Bridge())
    assert out == {"BOS": {"sb1": 1.0, "sb3": 0.35}, "LAL": {}}
    assert F.availability_from_injuries({"BOS": {1, 2}}, Bridge()) == {"BOS": {"sb1"}}      # plain sets unchanged
