"""Offline tests for nfl.features.props_features (synthetic frames; no GCS)."""
import numpy as np
import pandas as pd
import pytest

from nfl.features import props_features as F

TEAMS = ["AAA", "BBB", "CCC", "DDD"]
# week -> list of (home, away). AAA and BBB have a bye in week 3. Week 8 is UNPLAYED (upcoming).
SCHEDULE = {
    1: [("AAA", "BBB"), ("CCC", "DDD")],
    2: [("AAA", "CCC"), ("BBB", "DDD")],
    3: [("CCC", "DDD")],
    4: [("BBB", "AAA"), ("DDD", "CCC")],
    5: [("AAA", "DDD"), ("BBB", "CCC")],
    6: [("CCC", "AAA"), ("DDD", "BBB")],
    7: [("AAA", "BBB"), ("CCC", "DDD")],
    8: [("BBB", "AAA"), ("DDD", "CCC")],
}
PLAYED_WEEKS = range(1, 8)
SEASON = 2024


def _gid(week, home, away):
    return f"{SEASON}_{week:02d}_{away}_{home}"


def make_frames(seed=0, missing=None):
    """missing: set of (player_id, week) that DNP."""
    rng = np.random.default_rng(seed)
    missing = missing or set()
    sched, ps, ts, sn, inj = [], [], [], [], []
    players = []
    for w, games in SCHEDULE.items():
        for home, away in games:
            gid = _gid(w, home, away)
            played = w in PLAYED_WEEKS
            sched.append({
                "game_id": gid, "season": SEASON, "week": w, "gameday": f"2024-09-{w:02d}", "game_type": "REG",
                "home_team": home, "away_team": away,
                "home_score": float(rng.integers(10, 35)) if played else np.nan,
                "away_score": float(rng.integers(10, 35)) if played else np.nan,
                "spread_line": 3.0, "total_line": 46.0, "roof": "outdoors" if w % 2 else "dome",
                "temp": 60.0, "wind": 5.0, "home_rest": 7, "away_rest": 7, "div_game": 0,
                "home_qb_id": f"qb_{home}", "away_qb_id": f"qb_{away}"})
            if not played:
                continue
            for team, opp in ((home, away), (away, home)):
                ts.append({"game_id": gid, "team": team, "season": SEASON, "week": w, "attempts": 33.0,
                           "sacks_suffered": 2.0, "carries": 25.0, "passing_yards": 250.0, "rushing_yards": 110.0,
                           "passing_tds": 2.0, "rushing_tds": 1.0})
                roster = [(f"qb_{team}", "QB"), (f"rb_{team}", "RB"), (f"wr1_{team}", "WR"), (f"wr2_{team}", "WR"),
                          (f"te_{team}", "TE")]
                for pid, pos in roster:
                    if (pid, w) in missing:
                        continue
                    row = {"player_id": pid, "player_display_name": pid, "position": pos, "season": SEASON,
                           "week": w, "season_type": "REG", "game_id": gid, "team": team, "opponent_team": opp,
                           "attempts": 0, "completions": 0, "passing_yards": 0, "passing_tds": 0,
                           "passing_interceptions": 0, "sacks_suffered": 0, "passing_epa": 0.0, "carries": 0,
                           "rushing_yards": 0, "rushing_tds": 0, "targets": 0, "receptions": 0,
                           "receiving_yards": 0, "receiving_tds": 0, "receiving_air_yards": 0, "target_share": 0.0,
                           "air_yards_share": 0.0, "wopr": 0.0, "special_teams_tds": 0, "def_tds": 0,
                           "fumble_recovery_tds": 0}
                    if pos == "QB":
                        a = int(rng.integers(25, 45))
                        row.update(attempts=a, completions=a - 10, passing_yards=int(a * 7), passing_tds=int(rng.integers(0, 4)),
                                   carries=int(rng.integers(0, 6)), rushing_yards=int(rng.integers(0, 30)))
                    elif pos == "RB":
                        c = int(rng.integers(8, 25))
                        t = int(rng.integers(1, 7))
                        row.update(carries=c, rushing_yards=int(c * 4.2), rushing_tds=int(rng.integers(0, 2)),
                                   targets=t, receptions=max(t - 1, 0), receiving_yards=int(t * 6))
                    else:
                        t = int(rng.integers(2, 12))
                        row.update(targets=t, receptions=max(t - 3, 0), receiving_yards=int(t * 9),
                                   receiving_tds=int(rng.integers(0, 2)), target_share=t / 33.0)
                    ps.append(row)
                    sn.append({"game_id": gid, "season": SEASON, "week": w, "player": pid,
                               "pfr_player_id": f"pfr_{pid}", "position": pos, "team": team, "opponent": opp,
                               "offense_snaps": 40.0 + float(rng.integers(0, 30)), "offense_pct": 0.8})
    for team in TEAMS:
        for pid, _ in ((f"qb_{team}", 0), (f"rb_{team}", 0), (f"wr1_{team}", 0), (f"wr2_{team}", 0), (f"te_{team}", 0)):
            players.append({"gsis_id": pid, "pfr_id": f"pfr_{pid}", "birth_date": "1998-05-01", "draft_pick": 40.0,
                            "rookie_season": 2020})
    return F.Frames(pd.DataFrame(ps), pd.DataFrame(sched), pd.DataFrame(ts), pd.DataFrame(sn),
                    pd.DataFrame(inj, columns=["season", "week", "team", "gsis_id", "report_status",
                                               "practice_status", "game_type"]),
                    pd.DataFrame(players), None)


@pytest.fixture(scope="module")
def frames():
    return make_frames()


@pytest.fixture(scope="module")
def built(frames):
    return F.build_from_frames(frames, [SEASON])


def test_one_row_per_player_game_and_columns(built):
    assert not built.duplicated(["player_id", "game_id"]).any()
    for c in F.META_COLS + F.Y_COLS + F.ELIG_COLS:
        assert c in built.columns
    # 4 teams x 5 skill players x games played (AAA/BBB miss week 3)
    n_team_games = sum(len(g) * 2 for w, g in SCHEDULE.items() if w in PLAYED_WEEKS)
    assert len(built) == n_team_games * 5
    assert set(built["position"]) == {"QB", "RB", "WR", "TE"}


def test_no_raw_current_game_columns_leak_into_features(built):
    feats = set(F.feature_columns(built))
    raw = set(F.STAT_COLS) | {"snaps", "snap_pct", "td_count", "rz_car", "gl_car", "rz_tgt", "tgt_post",
                              "carries_post", "rec_post", "has_stat_row", "ord", "widx"}
    assert not (feats & raw), feats & raw
    assert all(not c.startswith("y_") for c in feats)


def test_first_game_has_no_history_and_l3_matches_manual(built):
    first = built[(built["player_id"] == "wr1_AAA") & (built["week"] == 1)].iloc[0]
    assert np.isnan(first["tgt_l3"]) and np.isnan(first["tgt_ewm"]) and first["n_prior_games"] == 0
    p = built[built["player_id"] == "wr1_AAA"].sort_values("week").reset_index(drop=True)
    # AAA plays weeks 1,2,4,5,6,7 (bye 3). Row at week 5 is the 4th game; l3 = games 1..3 = weeks 1,2,4
    actual_targets = p["y_receptions"]  # sanity that targets exist
    assert len(p) == 6
    ps_frames = make_frames()
    tg = ps_frames.player_stats.query("player_id == 'wr1_AAA'").sort_values("week")["targets"].to_numpy()
    assert p.loc[3, "tgt_l3"] == pytest.approx(tg[:3].mean())
    assert p.loc[3, "tgt_szn"] == pytest.approx(tg[:3].mean())
    assert p.loc[5, "tgt_l5"] == pytest.approx(tg[0:5].mean())


def test_bye_week_is_not_a_missed_game(built):
    p = built[built["player_id"] == "wr1_AAA"].sort_values("week")
    assert (p["games_missed_gap"].dropna() == 0).all()      # bye (week 3) does not count as missed


def test_dnp_week_shows_as_missed_game_and_trailing_skips_it():
    fr = make_frames(missing={("wr1_CCC", 4)})
    out = F.build_from_frames(fr, [SEASON])
    p = out[out["player_id"] == "wr1_CCC"].sort_values("week").reset_index(drop=True)
    assert 4 not in set(p["week"])
    wk5 = p[p["week"] == 5].iloc[0]
    assert wk5["games_missed_gap"] == 1
    tg = fr.player_stats.query("player_id == 'wr1_CCC'").sort_values("week")
    prev3 = tg[tg["week"] < 5]["targets"].to_numpy()[-3:]
    assert wk5["tgt_l3"] == pytest.approx(prev3.mean())


def test_game_environment_signs(built):
    row = built[(built["game_id"] == _gid(1, "AAA", "BBB")) & (built["team"] == "AAA")].iloc[0]
    away = built[(built["game_id"] == _gid(1, "AAA", "BBB")) & (built["team"] == "BBB")].iloc[0]
    assert row["is_home"] == 1 and away["is_home"] == 0
    assert row["team_spread"] == 3.0 and away["team_spread"] == -3.0           # home-favoured positive
    assert row["implied_team_total"] == pytest.approx(24.5) and away["implied_team_total"] == pytest.approx(21.5)
    assert row["implied_team_total"] + row["implied_opp_total"] == pytest.approx(46.0)
    dome = built[(built["week"] == 2)].iloc[0]
    assert dome["indoor"] == 1 and np.isnan(dome["temp_f"])


def test_targets(frames, built):
    r = built[(built["player_id"] == "rb_AAA") & (built["week"] == 2)].iloc[0]
    s = frames.player_stats.query("player_id == 'rb_AAA' and week == 2").iloc[0]
    assert r["y_rush_yds"] == s["rushing_yards"] and r["y_receptions"] == s["receptions"]
    assert r["y_td_count"] == s["rushing_tds"] + s["receiving_tds"]
    assert r["y_anytime_td"] == float(s["rushing_tds"] + s["receiving_tds"] >= 1)


def test_team_and_opponent_features_are_pre_game(built):
    w1 = built[built["week"] == 1]
    assert w1["team_off_plays_ewm"].isna().all() and w1["opp_alw_plays_ewm"].isna().all()
    w2 = built[(built["week"] == 2) & (built["player_id"] == "wr1_AAA")].iloc[0]
    assert w2["team_off_plays_ewm"] == pytest.approx(33 + 2 + 25)      # one prior game of 60 plays


def test_leakage_perturbing_a_week_changes_no_earlier_feature(frames):
    for wk in (3, 5):
        r = F.leakage_check(frames, [SEASON], SEASON, wk, seed=wk)
        assert r["changed_cols"] == [], r["changed_cols"]
        assert r["n_checked"] > 0
        assert r["n_changed_after"] > 0, "perturbation had no downstream effect -> the test has no power"


def test_leakage_check_detects_a_deliberately_leaky_feature(frames, monkeypatch):
    """Power test: a builder that sneaks the current game's targets into a feature must fail the check."""
    real = F.build_from_frames

    def leaky(fr, emit, include_upcoming=False, **kw):
        out = real(fr, emit, include_upcoming=include_upcoming, **kw)
        cur = fr.player_stats.set_index(["player_id", "game_id"])["targets"]
        out["tgt_leak"] = [cur.get((p, g), np.nan) for p, g in zip(out["player_id"], out["game_id"])]
        return out

    monkeypatch.setattr(F, "build_from_frames", leaky)
    r = F.leakage_check(frames, [SEASON], SEASON, 4, seed=1)
    assert "tgt_leak" in r["changed_cols"]


def test_upcoming_rows_have_features_but_no_targets():
    fr = make_frames()
    out = F.build_from_frames(fr, [SEASON], include_upcoming=True)
    up = out[out["is_upcoming"]]
    assert len(up) > 0 and set(up["week"]) == {8}
    assert up[F.Y_COLS].isna().all().all()
    assert up["tgt_ewm"].notna().all() and up["team_spread"].notna().all()
    # a player's upcoming-row trailing history equals the history seen at his next played row would be
    r = up[up["player_id"] == "wr1_AAA"].iloc[0]
    hist = fr.player_stats.query("player_id == 'wr1_AAA'").sort_values("week")["targets"].to_numpy()
    assert r["tgt_l5"] == pytest.approx(hist[-5:].mean())
    # played rows are unchanged by adding upcoming rows
    base = F.build_from_frames(fr, [SEASON], include_upcoming=False)
    merged = base.merge(out[~out["is_upcoming"]], on=["player_id", "game_id"], suffixes=("", "_u"))
    for c in ("tgt_l5", "tgt_ewm", "team_off_plays_ewm", "opp_alw_pts_ewm"):
        np.testing.assert_allclose(merged[c].to_numpy(float), merged[f"{c}_u"].to_numpy(float), equal_nan=True)


def test_injuries_teammate_out_feature():
    fr = make_frames()
    # wr1_AAA is ruled Out for week 5 (he has no stats that week), so wr2_AAA/te_AAA see his lost usage
    ps = fr.player_stats
    fr.player_stats = ps[~((ps["player_id"] == "wr1_AAA") & (ps["week"] == 5))].copy()
    fr.snap_counts = fr.snap_counts[~((fr.snap_counts["player"] == "wr1_AAA") & (fr.snap_counts["week"] == 5))]
    fr.injuries = pd.DataFrame([{"season": SEASON, "week": 5, "team": "AAA", "gsis_id": "wr1_AAA",
                                 "report_status": "Out", "practice_status": "Did Not Participate In Practice",
                                 "game_type": "REG"}])
    out = F.build_from_frames(fr, [SEASON])
    row = out[(out["player_id"] == "wr2_AAA") & (out["week"] == 5)].iloc[0]
    assert row["tm_out_tgt"] > 1.0 and row["tm_out_n"] == 1 and row["inj_report_available"] == 1
    other = out[(out["player_id"] == "wr2_AAA") & (out["week"] == 6)].iloc[0]
    assert other["inj_report_available"] == 0 and np.isnan(other["tm_out_tgt"])


def test_aggregate_pbp_redzone():
    pbp = pd.DataFrame({
        "game_id": ["g"] * 4, "yardline_100": [5, 2, 15, 30], "rush_attempt": [1, 1, 0, 0], "pass_attempt": [0, 0, 1, 1],
        "qb_kneel": [0, 0, 0, 0], "rusher_player_id": ["rb", "rb", None, None],
        "receiver_player_id": [None, None, "wr", "wr"]})
    out = F.aggregate_pbp_redzone(pbp).set_index("player_id")
    assert out.loc["rb", "rz_car"] == 2 and out.loc["rb", "gl_car"] == 1
    assert out.loc["wr", "rz_tgt"] == 1


# ================================================================================================ teammate-absence usage
TM_COLS = F.TEAM_MISSING_COLS


def _inj_row(week, team, pid, status, practice="Did Not Participate In Practice"):
    return {"season": SEASON, "week": week, "team": team, "gsis_id": pid, "report_status": status,
            "practice_status": practice, "game_type": "REG"}


def _with_injuries(fr, entries, drop_rows=True):
    """entries: (week, team, pid, status). drop_rows removes the player's stat + snap rows for that week (a real
    'Out' has none). Every team-week of weeks 1..7 gets a filler 'healthy' row so a report 'exists' everywhere."""
    fr = F.Frames(fr.player_stats.copy(), fr.schedules, fr.team_stats, fr.snap_counts.copy(), None, fr.players, None)
    rows = []
    for w, games in SCHEDULE.items():
        if w not in PLAYED_WEEKS:
            continue
        for home, away in games:
            for t in (home, away):
                rows.append(_inj_row(w, t, f"qb_{t}", None, "Full Participation in Practice"))
    for e in entries:
        rows.append(_inj_row(*e))
        if drop_rows and e[3] is not None:
            w, _, pid, _ = e
            fr.player_stats = fr.player_stats[~((fr.player_stats["player_id"] == pid) & (fr.player_stats["week"] == w))]
            fr.snap_counts = fr.snap_counts[~((fr.snap_counts["player"] == pid) & (fr.snap_counts["week"] == w))]
    fr.injuries = pd.DataFrame(rows)
    return fr


def _expected_share(fr, pid, upto_week, stat, n=5):
    ps = fr.player_stats
    ps = ps[(ps["week"] < upto_week) & ps["position"].isin(F.SKILL_POS)]
    tot = ps.groupby(["game_id", "team"])[stat].transform("sum")
    mine = ps[ps["player_id"] == pid]
    sh = (mine[stat] / tot[mine.index]).to_numpy()
    return float(np.mean(sh[-n:]))


def test_team_missing_shares_values_and_defaults_exclusion():
    fr = _with_injuries(make_frames(), [(5, "AAA", "wr1_AAA", "Out")])
    out = F.build_from_frames(fr, [SEASON])
    row = out[(out["player_id"] == "wr2_AAA") & (out["week"] == 5)].iloc[0]
    want = _expected_share(fr, "wr1_AAA", 5, "targets")
    assert 0.05 < want < 0.5
    assert row["team_missing_target_share_l5"] == pytest.approx(want)
    assert row["team_missing_rush_share_l5"] == pytest.approx(0.0)         # wr1 has no carries
    assert row["team_missing_pass_att_share_l5"] == pytest.approx(0.0)
    # other team / other week unaffected: report exists, nobody absent -> 0 (not NaN)
    ccc = out[(out["player_id"] == "wr2_CCC") & (out["week"] == 5)].iloc[0]
    assert ccc["team_missing_target_share_l5"] == 0.0
    # the default model feature list does NOT contain the experimental columns; opt-in does
    assert not set(TM_COLS) & set(F.feature_columns(out))
    assert set(TM_COLS) <= set(F.feature_columns(out, include_experimental=True))


def test_team_missing_rush_and_pass_att_shares():
    fr = _with_injuries(make_frames(), [(6, "CCC", "rb_CCC", "Doubtful"), (6, "DDD", "qb_DDD", "Out")])
    out = F.build_from_frames(fr, [SEASON])
    r = out[(out["player_id"] == "wr1_CCC") & (out["week"] == 6)].iloc[0]
    assert r["team_missing_rush_share_l5"] == pytest.approx(_expected_share(fr, "rb_CCC", 6, "carries"))
    assert r["team_missing_rush_share_l5"] > 0.2
    d = out[(out["player_id"] == "rb_DDD") & (out["week"] == 6)].iloc[0]
    assert d["team_missing_pass_att_share_l5"] == pytest.approx(_expected_share(fr, "qb_DDD", 6, "attempts"))
    assert d["team_missing_pass_att_share_l5"] > 0.9            # the lone QB threw ~all attempts


def test_team_missing_status_mapping():
    # Questionable and blank do NOT count; Doubtful / Out / IR aliases (any case) do
    ents = [(5, "AAA", "wr1_AAA", "Questionable"), (5, "BBB", "wr1_BBB", "OUT"), (5, "CCC", "wr1_CCC", " doubtful "),
            (5, "DDD", "wr1_DDD", "Injured Reserve")]
    out = F.build_from_frames(_with_injuries(make_frames(), ents, drop_rows=False), [SEASON])
    wk5 = out[(out["week"] == 5) & (out["player_id"] == "te_AAA")].iloc[0]
    assert wk5["team_missing_target_share_l5"] == 0.0                    # Questionable only
    for team, other in (("BBB", "te_BBB"), ("CCC", "te_CCC"), ("DDD", "te_DDD")):
        r = out[(out["week"] == 5) & (out["player_id"] == other)].iloc[0]
        assert r["team_missing_target_share_l5"] > 0.05, team


def test_team_missing_own_listing_never_feeds_own_row():
    # wr1_AAA is listed Out but has a stat row (he played anyway): he must not see himself, teammates still do
    fr = _with_injuries(make_frames(), [(5, "AAA", "wr1_AAA", "Out")], drop_rows=False)
    out = F.build_from_frames(fr, [SEASON])
    me = out[(out["player_id"] == "wr1_AAA") & (out["week"] == 5)].iloc[0]
    mate = out[(out["player_id"] == "wr2_AAA") & (out["week"] == 5)].iloc[0]
    for c in TM_COLS:
        assert me[c] == 0.0
    assert mate["team_missing_target_share_l5"] > 0.05
    # two absent teammates: each only sees the OTHER one
    fr2 = _with_injuries(make_frames(), [(5, "AAA", "wr1_AAA", "Out"), (5, "AAA", "wr2_AAA", "Out")], drop_rows=False)
    o2 = F.build_from_frames(fr2, [SEASON])
    a = o2[(o2["player_id"] == "wr1_AAA") & (o2["week"] == 5)].iloc[0]["team_missing_target_share_l5"]
    b = o2[(o2["player_id"] == "wr2_AAA") & (o2["week"] == 5)].iloc[0]["team_missing_target_share_l5"]
    assert a == pytest.approx(_expected_share(fr2, "wr2_AAA", 5, "targets"))
    assert b == pytest.approx(_expected_share(fr2, "wr1_AAA", 5, "targets"))


def test_team_missing_is_nan_without_injury_data_never_zero():
    fr = make_frames()                                               # empty injuries frame
    out = F.build_from_frames(fr, [SEASON])
    assert out[TM_COLS].isna().all().all()
    fr.injuries = None
    assert F.build_from_frames(fr, [SEASON])[TM_COLS].isna().all().all()
    # report present for week 5 only (a season with gaps): weeks without a report are NaN, week 5 is a number
    fr2 = make_frames()
    fr2.injuries = pd.DataFrame([_inj_row(5, "AAA", "wr1_AAA", "Out")])
    o = F.build_from_frames(fr2, [SEASON])
    aaa5 = o[(o["team"] == "AAA") & (o["week"] == 5)]
    assert aaa5["team_missing_target_share_l5"].notna().all()
    assert o[(o["team"] == "AAA") & (o["week"] != 5)]["team_missing_target_share_l5"].isna().all()
    assert o[(o["team"] == "BBB") & (o["week"] == 5)]["team_missing_target_share_l5"].isna().all()


def test_team_missing_unknown_usage_contributes_zero_and_other_team_history_ignored():
    # absent at week 1: no prior appearance -> contributes 0 (not NaN, report exists)
    out = F.build_from_frames(_with_injuries(make_frames(), [(1, "AAA", "wr1_AAA", "Out")]), [SEASON])
    r = out[(out["player_id"] == "wr2_AAA") & (out["week"] == 1)].iloc[0]
    assert r["team_missing_target_share_l5"] == 0.0


def test_injury_entry_for_week_w_affects_week_w_only_and_outcomes_of_w_never_do():
    base_fr = _with_injuries(make_frames(), [])
    inj_fr = _with_injuries(make_frames(), [(5, "AAA", "wr1_AAA", "Out")])
    b = F.build_from_frames(base_fr, [SEASON]).set_index(["player_id", "game_id"])
    i = F.build_from_frames(inj_fr, [SEASON]).set_index(["player_id", "game_id"])
    # (a) the week-5 injury entry changes week-5 teammate features ...
    wr2_5 = (("wr2_AAA"), _gid(5, "AAA", "DDD"))
    assert i.loc[wr2_5, "team_missing_target_share_l5"] > b.loc[wr2_5, "team_missing_target_share_l5"]
    # ... and NO feature of any row from weeks 1-4 (it is not a post-hoc fact about the past) ...
    common = b.index.intersection(i.index)
    early = [k for k in common if b.loc[k, "week"] < 5 and k[0] != "wr1_AAA"]
    for c in TM_COLS:
        np.testing.assert_array_equal(b.loc[early, c].to_numpy(float), i.loc[early, c].to_numpy(float))
    # ... and week-6 features only through the player's absence (his week-5 row is gone), never the report flag itself
    assert i.loc[("wr2_AAA", _gid(6, "CCC", "AAA")), "team_missing_target_share_l5"] == 0.0

    # (b) outcomes of week 5 never reach week-5 (or earlier) rows: scramble them and compare
    fr = _with_injuries(make_frames(), [(5, "AAA", "wr1_AAA", "Out"), (6, "AAA", "wr1_AAA", "Out")], drop_rows=False)
    out0 = F.build_from_frames(fr, [SEASON]).set_index(["player_id", "game_id"])
    out1 = F.build_from_frames(F.perturb_outcomes(fr, SEASON, 5, seed=11), [SEASON]).set_index(["player_id", "game_id"])
    common = out0.index.intersection(out1.index)
    upto5 = [k for k in common if out0.loc[k, "week"] <= 5]
    for c in TM_COLS:
        np.testing.assert_array_equal(out0.loc[upto5, c].to_numpy(float), out1.loc[upto5, c].to_numpy(float))
    # power: wr1_AAA is listed out in week 6 and his usage comes from his (scrambled) week-5 line -> week 6 moves
    k = ("wr2_AAA", _gid(6, "CCC", "AAA"))
    assert out0.loc[k, "team_missing_target_share_l5"] != out1.loc[k, "team_missing_target_share_l5"]


@pytest.mark.parametrize("drop_rows", [True, False])      # False: listed players still HAVE a (scrambled) stat row
@pytest.mark.parametrize("wk", [3, 4, 6])
def test_leakage_with_injury_reports_scrambles_outcomes_and_snaps(wk, drop_rows):
    """The existing leakage contract, on frames whose injury reports make the NEW features non-trivial. perturb_outcomes
    scrambles the week's stat lines, team stats, scores AND snap counts; no feature at/before the week may move."""
    fr = _with_injuries(make_frames(), [(2, "AAA", "wr1_AAA", "Out"), (4, "CCC", "rb_CCC", "Doubtful"),
                                        (5, "BBB", "wr2_BBB", "Out"), (6, "DDD", "te_DDD", "Out"),
                                        (7, "AAA", "rb_AAA", "Out")], drop_rows=drop_rows)
    pert = F.perturb_outcomes(fr, SEASON, wk, seed=wk)
    sn0 = fr.snap_counts[fr.snap_counts["week"] == wk]["offense_snaps"].to_numpy()
    sn1 = pert.snap_counts[pert.snap_counts["week"] == wk]["offense_snaps"].to_numpy()
    assert not np.array_equal(sn0, sn1), "the snap counts of the scrambled week were not perturbed"
    r = F.leakage_check(fr, [SEASON], SEASON, wk, seed=wk)
    assert r["changed_cols"] == [], r["changed_cols"]
    assert r["n_checked"] > 0 and r["n_changed_after"] > 0
    built = F.build_from_frames(fr, [SEASON])
    assert built[TM_COLS].notna().all().all() and (built["team_missing_target_share_l5"] > 0).any()
    assert set(TM_COLS) <= set(F.feature_columns(built, include_experimental=True))   # the check covered them


def test_new_stat_targets_present_and_correct(frames, built):
    r = built[(built["player_id"] == "qb_AAA") & (built["week"] == 2)].iloc[0]
    s = frames.player_stats.query("player_id == 'qb_AAA' and week == 2").iloc[0]
    assert r["y_pass_attempts"] == s["attempts"] and r["y_completions"] == s["completions"]
    assert r["y_rush_attempts"] == s["carries"]
    up = F.build_from_frames(frames, [SEASON], include_upcoming=True)
    assert up[up["is_upcoming"]][["y_pass_attempts", "y_completions", "y_rush_attempts"]].isna().all().all()
