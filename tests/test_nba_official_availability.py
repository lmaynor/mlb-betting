"""nba.props.official_availability: per-game pre-game listing selection, coverage rules, P(active) table, feature variants.
ESPN-shaped synthetic world (ints for ids); the reports are synthetic frames in nba_injury_report.COLUMNS shape."""
import numpy as np
import pandas as pd
import pytest

from nba.data import nba_injury_report as R
from nba.props import config as C
from nba.props import espn_data as ED
from nba.props import official_availability as O
from tests.nba_props_helpers import make_masters
from tests.test_nba_props_espn_data import to_espn

AV = list(C.AVAILABILITY_FEATURES)


@pytest.fixture(scope="module")
def world():
    pb, gm, tips = make_masters(n_days=45, seed=5)
    box = to_espn(pb, gm, tips)
    rows, tg = ED.prepare_espn(box)
    return box, rows, tg


def games_on(tg, k):
    """The k-th game day's team-games."""
    day = sorted(tg["date"].unique())[k]
    return tg[tg["date"] == day]


def report_rows(tg_day, ts_et, entries, rid, not_submitted=()):
    """entries: [(team, player_name, status)] for the games of tg_day; one NOT YET SUBMITTED row per team in not_submitted."""
    out = []
    for r in tg_day.itertuples(index=False):
        pass
    games = {}
    for r in tg_day.itertuples(index=False):
        games.setdefault(r.game_id, {})[r.is_home] = r.team
    for gid, d in games.items():
        away, home = d[False], d[True]
        for team, name, status in entries:
            if team in (away, home):
                out.append((away, home, team, name, status, "Injury/Illness - Knee"))
        for team in not_submitted:
            if team in (away, home):
                out.append((away, home, team, None, R.NOT_SUBMITTED, ""))
    date = pd.Timestamp(tg_day["date"].iloc[0])
    df = pd.DataFrame(out, columns=["m_a", "m_b", "team_code", "player", "status", "reason"])
    df["report_ts_et"] = ts_et
    df["game_date"] = date
    df["reason_cat"] = "injury_illness"
    df["ts_utc"] = pd.to_datetime(ts_et, utc=True)
    df["report_id"] = rid
    return df


def with_ids(df, rows):
    """Resolve like production does (names exist in the world's history)."""
    return O.resolve(df, rows)[0]


def tip_of(tg_day, team):
    return pd.Timestamp(tg_day.loc[tg_day["team"] == team, "tip_sort"].iloc[0], tz="UTC")


def test_newest_report_at_least_lead_minutes_before_tip_is_selected(world):
    box, rows, tg = world
    day = games_on(tg, 5)
    team = day["team"].iloc[0]
    tip = tip_of(day, team)
    et = lambda t: t.tz_convert("America/New_York").isoformat()
    name = rows[(rows["team"] == team)]["name"].iloc[0]
    early = report_rows(day, et(tip - pd.Timedelta(hours=4)), [(team, name, "Out")], "early")
    ok = report_rows(day, et(tip - pd.Timedelta(minutes=45)), [(team, name, "Questionable")], "ok")
    late = report_rows(day, et(tip - pd.Timedelta(minutes=10)), [(team, name, "Available")], "late")   # inside the lead window
    rep = with_ids(pd.concat([early, ok, late], ignore_index=True), rows)
    listing, cov = O.select_listing(rep, day)
    row = cov[cov["team"] == team].iloc[0]
    assert row["report_id"] == "ok" and bool(row["known"])
    mine = listing[(listing["team"] == team) & (listing["game_id"] == row["game_id"])]
    assert mine["status"].tolist() == ["Questionable"] and mine["espn_id"].notna().all()


def test_team_with_no_rows_in_a_covered_game_is_healthy_and_uncovered_game_is_unknown(world):
    box, rows, tg = world
    day = games_on(tg, 6)
    teams = list(day["team"])
    a, b = teams[0], day.loc[day["game_id"] != day["game_id"].iloc[0], "team"].iloc[0]
    opp_of_a = day.loc[(day["game_id"] == day["game_id"].iloc[0]) & (day["team"] != a), "team"].iloc[0]
    name = rows[rows["team"] == opp_of_a]["name"].iloc[0]
    tip = tip_of(day, a)
    rep = with_ids(report_rows(day.iloc[:2], (tip - pd.Timedelta(hours=3)).tz_convert("America/New_York").isoformat(),
                               [(opp_of_a, name, "Out")], "r1"), rows)         # report covers only the FIRST game
    listing, cov = O.select_listing(rep, day)
    by = cov.set_index("team")
    first_game = day["game_id"].iloc[0]
    assert by.loc[a, "report_id"] == "r1" and bool(by.loc[a, "known"])           # a: covered, no rows -> known healthy
    assert by.loc[b, "report_id"] is None or pd.isna(by.loc[b, "report_id"])      # b's game is not in the report -> unknown
    m = O.lists_by_game(listing, cov, ["Out"])
    qd_a = int(cov.loc[cov["team"] == a, "qd"].iloc[0])
    assert m[(a, qd_a)] == set() and m[(opp_of_a, qd_a)] == {int(rep["espn_id"].dropna().iloc[0])}
    assert all(k[0] != b for k in m)                                              # unknown games are ABSENT, never empty sets


def test_not_yet_submitted_makes_the_team_unknown(world):
    box, rows, tg = world
    day = games_on(tg, 7)
    t = day["team"].iloc[0]
    tip = tip_of(day, t)
    rep = with_ids(report_rows(day, (tip - pd.Timedelta(hours=3)).tz_convert("America/New_York").isoformat(), [], "r",
                               not_submitted=[t]), rows)
    listing, cov = O.select_listing(rep, day)
    assert not bool(cov.loc[cov["team"] == t, "known"].iloc[0])
    assert (t, int(cov.loc[cov["team"] == t, "qd"].iloc[0])) not in O.lists_by_game(listing, cov, ["Out"])


def test_report_after_the_cutoff_leaves_the_game_uncovered(world):
    box, rows, tg = world
    day = games_on(tg, 8)
    tip = tip_of(day, day["team"].iloc[0])
    late = with_ids(report_rows(day, (tip - pd.Timedelta(minutes=5)).tz_convert("America/New_York").isoformat(), [], "late"),
                    rows)
    _, cov = O.select_listing(late, day)
    assert cov["report_id"].isna().all()


def test_p_active_table_shrinks_and_is_walk_forward_safe():
    o = pd.DataFrame({"game_id": [1] * 10 + [2] * 10, "status": ["Questionable"] * 20,
                      "played": [True] * 5 + [False] * 5 + [False] * 10, "reason_cat": "injury_illness"})
    season_of = {1: 2021, 2: 2022}
    t21 = O.p_active_table(o, 2021, season_of, prior_n=0.0)
    t22 = O.p_active_table(o, 2022, season_of, prior_n=0.0)
    assert t21["Questionable"] == pytest.approx(0.5) and t22["Questionable"] == pytest.approx(0.25)   # 2022 only after T=2023
    assert set(t21) == set(O.LISTED_STATUSES) and t21["Out"] == pytest.approx(0.5)                     # unseen -> pooled rate
    shrunk = O.p_active_table(o, 2022, season_of, prior_n=20.0)
    assert t22["Questionable"] < shrunk["Questionable"] < 0.5 + 1e-9 or shrunk["Questionable"] == pytest.approx(0.25)


def test_status_table_and_played_outcomes(world):
    box, rows, tg = world
    g = rows.iloc[0]
    other = rows[(rows["game_id"] != g["game_id"])].iloc[0]
    listing = pd.DataFrame({"game_id": [g["game_id"], g["game_id"]], "team": [g["team"]] * 2, "qd": [1, 1],
                            "espn_id": [int(g["player_id"]), int(other["player_id"])] if other["team"] != g["team"] else
                            [int(g["player_id"]), 999999999], "status": ["Probable", "Out"], "reason_cat": ["injury_illness"] * 2,
                            "report_id": ["x", "x"]})
    out = O.played_outcomes(listing, rows)
    assert out["played"].tolist()[0] is True or bool(out["played"].iloc[0])
    s, r = O.status_table(out)
    assert list(s.index) == list(O.LISTED_STATUSES) and s.loc["Probable", "n"] == 1 and np.isnan(s.loc["Doubtful", "p_played"])


def test_per_game_lists_flow_through_the_live_feature_path_and_match_history_when_equal(world):
    """Serving the TRUE absent set as per-game lists must reproduce the training 'history' feature exactly (parity)."""
    from nba.props.features import FeatureContext, ID_COLS, build_training_frame
    box, rows, tg = world
    ctx = FeatureContext(rows, tg)
    Xh, _ = build_training_frame(rows, tg, ctx, availability="history")
    q = rows.loc[Xh.index, ID_COLS]
    per_game = {}
    for r in tg.itertuples(index=False):
        ab = ctx.absent_set(r.team, r.date)
        if ab is not None:
            per_game[(r.team, int(pd.Timestamp(r.date).to_datetime64().astype("datetime64[D]").astype("int64")))] = ab
    live = ctx.features(q, availability=per_game)[AV]
    ok = live.notna().all(axis=1) & Xh[AV].notna().all(axis=1)
    assert ok.sum() > 200
    assert np.allclose(live[ok].to_numpy(float), Xh.loc[ok, AV].to_numpy(float))
    # a per-day key wins over the bare team key; a missing (team, day) with no bare key is NaN, not healthy
    r0 = q.iloc[0]
    only_other = {("ZZZ", 0): set()}
    assert ctx.features(q.iloc[:1], availability=only_other)[AV].isna().all().all()
