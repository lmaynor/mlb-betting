"""Skater TOI splits from shift charts (nhl.data.toi_splits): clock parsing, shift cleaning, the per-second state timeline (PP / PK /
EV, empty net, too-many-men), the pbp anchor, the reconciliation checks, the resumable offline runner and the GCS-write gate.
Everything runs offline on synthetic charts."""
import gzip
import json

import numpy as np
import pandas as pd
import pytest

from nhl.data import toi_splits as T

HOME, AWAY = 10, 20


def shift_row(pid, team_id, abbr, period, a, b, type_code=517):
    f = lambda s: f"{s // 60:02d}:{s % 60:02d}"            # noqa: E731
    return {"playerId": pid, "teamId": team_id, "teamAbbrev": abbr, "period": period, "startTime": f(a), "endTime": f(b),
            "typeCode": type_code}


def roster(home_sk, away_sk, home_g=901, away_g=902):
    rs = [{"playerId": p, "teamId": HOME, "positionCode": "C"} for p in home_sk] + \
         [{"playerId": p, "teamId": AWAY, "positionCode": "D"} for p in away_sk] + \
         [{"playerId": home_g, "teamId": HOME, "positionCode": "G"}, {"playerId": away_g, "teamId": AWAY, "positionCode": "G"}]
    return rs


def pbp_of(rs, plays=None, game_type=2):
    return {"homeTeam": {"id": HOME}, "awayTeam": {"id": AWAY}, "rosterSpots": rs, "plays": plays or [], "gameType": game_type}


def one_period_game(away_box=(100, 220), goalie_out=None):
    """Period 1 only (0:00-20:00). Five home skaters 1..5 and five away skaters 11..15 on the ice all period; away skater 15 sits in the
    box over `away_box` (a power play for HOME). Goalies 901 (home) / 902 (away) in net all period unless `goalie_out=(team, a, b)`."""
    rows = []
    for p in range(1, 6):
        rows.append(shift_row(p, HOME, "HHH", 1, 0, 1200))
    for p in range(11, 16):
        if p == 15 and away_box:
            rows += [shift_row(p, AWAY, "AAA", 1, 0, away_box[0]), shift_row(p, AWAY, "AAA", 1, away_box[1], 1200)]
        else:
            rows.append(shift_row(p, AWAY, "AAA", 1, 0, 1200))
    rows.append(shift_row(901, HOME, "HHH", 1, 0, 1200))
    if goalie_out and goalie_out[0] == "away":
        rows += [shift_row(902, AWAY, "AAA", 1, 0, goalie_out[1]), shift_row(902, AWAY, "AAA", 1, goalie_out[2], 1200)]
    else:
        rows.append(shift_row(902, AWAY, "AAA", 1, 0, 1200))
    return {"data": rows}, roster(range(1, 6), range(11, 16))


# ---------------------------------------------------------------------------------------------------------------------
def test_parse_clock():
    assert T.parse_clock("00:28") == 28 and T.parse_clock("19:59") == 1199 and T.parse_clock("20:00") == 1200
    assert T.parse_clock(None) is None and T.parse_clock("abc") is None and T.parse_clock("") is None


def test_shifts_frame_drops_goal_rows_zero_length_and_offsets_periods():
    chart = {"data": [shift_row(1, HOME, "H", 1, 10, 50), shift_row(1, HOME, "H", 2, 0, 30), shift_row(1, HOME, "H", 1, 60, 60),
                      shift_row(2, HOME, "H", 1, 5, 5, type_code=505), {"playerId": 3, "teamId": HOME, "period": 1, "startTime": None,
                                                                         "endTime": "00:10", "typeCode": 517}]}
    f = T.shifts_frame(chart)
    assert sorted(zip(f["start"], f["end"])) == [(10, 50), (1200, 1230)]            # period 2 starts at 1200 on the abs axis
    assert T.shifts_frame(None).empty and T.shifts_frame({"data": []}).empty


def test_exact_duplicate_shift_rows_are_merged_not_double_counted():
    """Live data bug (game 2024020302): one 57 s shift listed twice made a team look like it had six skaters."""
    chart = {"data": [shift_row(8, AWAY, "A", 1, 973, 1030), shift_row(8, AWAY, "A", 1, 973, 1030), shift_row(8, AWAY, "A", 1, 1030, 1100)]}
    f = T.shifts_frame(chart)
    assert sorted(zip(f["start"], f["end"])) == [(973, 1030), (1030, 1100)]        # duplicate gone; touching shifts stay two shifts
    assert (f["end"] - f["start"]).sum() == 127


def test_power_play_penalty_kill_and_even_strength_seconds():
    chart, rs = one_period_game()
    df, qa = T.build_game(1, chart, pbp_of(rs))
    assert qa["status"] == "ok" and len(df) == 10
    by = df.set_index("player_id")
    for p in range(1, 6):                                       # home: 120 s of power play
        assert (by.loc[p, "toi_pp"], by.loc[p, "toi_ev"], by.loc[p, "toi_pk"], by.loc[p, "toi_total"]) == (120, 1080, 0, 1200)
    for p in range(11, 15):                                     # away skaters on the ice for the kill
        assert (by.loc[p, "toi_pp"], by.loc[p, "toi_ev"], by.loc[p, "toi_pk"]) == (0, 1080, 120)
    assert (by.loc[15, "toi_pk"], by.loc[15, "toi_total"], by.loc[15, "toi_ev"]) == (0, 1080, 1080)    # in the box: no PK time
    assert (by.loc[1, "team_pp_s"], by.loc[11, "team_pk_s"], by.loc[11, "team_pp_s"]) == (120, 120, 0)
    assert qa["home_pp_s"] == 120 and qa["away_pp_s"] == 0
    # EV 5v5 seconds exclude the 120 PP seconds
    assert by.loc[1, "toi_ev5"] == 1080
    # the three parts always sum to the shift total
    assert ((df["toi_ev"] + df["toi_pp"] + df["toi_pk"]) == df["toi_total"]).all()


def test_goalies_are_not_skaters_and_no_penalty_means_all_even_strength():
    chart, rs = one_period_game(away_box=None)
    df, _ = T.build_game(1, chart, pbp_of(rs))
    assert set(df["player_id"]) == set(range(1, 6)) | set(range(11, 16))             # 901 / 902 absent
    assert (df["toi_pp"] == 0).all() and (df["toi_pk"] == 0).all() and (df["toi_ev"] == 1200).all()
    assert (df["pp_unit_guess"] == 0).all() and df["pp_share"].isna().all()


def test_pulled_goalie_with_an_extra_attacker_is_even_strength_not_a_power_play():
    """Away pulls its goalie 1000-1100 and sends a 6th skater: 6v5 skaters, effective 5v5 -> EV (the powerplay.py rule)."""
    chart, rs = one_period_game(away_box=None, goalie_out=("away", 1000, 1100))
    chart["data"].append(shift_row(16, AWAY, "AAA", 1, 1000, 1100))
    rs = rs + [{"playerId": 16, "teamId": AWAY, "positionCode": "L"}]
    df, _ = T.build_game(1, chart, pbp_of(rs))
    assert (df["toi_pp"] == 0).all() and (df["toi_pk"] == 0).all()
    # ... but the same 6th skater while the goalie STAYS in net is a real skater-count edge (too many men): PP for the other side
    chart2, rs2 = one_period_game(away_box=None)
    chart2["data"].append(shift_row(16, AWAY, "AAA", 1, 1000, 1100))
    df2, _ = T.build_game(1, chart2, pbp_of(rs2 + [{"playerId": 16, "teamId": AWAY, "positionCode": "L"}]), anchor=False)
    assert df2.set_index("player_id").loc[16, "toi_pp"] == 100


def test_line_change_flicker_does_not_create_a_penalty():
    """A 2-second gap and overlap at a line change must not read as 4v5 / 6v5 (the rolling median)."""
    chart, rs = one_period_game(away_box=None)
    chart["data"] = [r for r in chart["data"] if r["playerId"] != 11] + [
        shift_row(11, AWAY, "AAA", 1, 0, 598), shift_row(11, AWAY, "AAA", 1, 600, 1200)]             # 2 s gap at 598-600
    df, _ = T.build_game(1, chart, pbp_of(rs))
    assert (df["toi_pp"] == 0).all() and (df["toi_pk"] == 0).all()


def test_anchor_to_pbp_pins_a_glitch_between_two_events_that_agree():
    chart, rs = one_period_game(away_box=None)
    chart["data"].append(shift_row(16, AWAY, "AAA", 1, 300, 360))                    # a phantom 6th away skater for 60 s
    rs = rs + [{"playerId": 16, "teamId": AWAY, "positionCode": "L"}]
    plays = [{"periodDescriptor": {"number": 1, "periodType": "REG"}, "timeInPeriod": "04:00", "situationCode": "1551", "typeDescKey": "hit"},
             {"periodDescriptor": {"number": 1, "periodType": "REG"}, "timeInPeriod": "08:00", "situationCode": "1551", "typeDescKey": "hit"}]
    raw, _ = T.build_game(1, chart, pbp_of(rs, plays), anchor=False)
    pinned, qa = T.build_game(1, chart, pbp_of(rs, plays), anchor=True)
    assert raw.set_index("player_id").loc[11, "toi_pp"] == 60 and pinned["toi_pp"].sum() == 0
    assert qa["pbp_events"] == 2                                                      # the agreement stat is computed on the RAW counts


def test_strength_agreement_counts_matching_events():
    chart, rs = one_period_game()
    plays = [{"periodDescriptor": {"number": 1, "periodType": "REG"}, "timeInPeriod": "02:00", "situationCode": "1451"},   # away 4 skaters, home 5
             {"periodDescriptor": {"number": 1, "periodType": "REG"}, "timeInPeriod": "10:00", "situationCode": "1541"},   # WRONG (5v4 away PP)
             {"periodDescriptor": {"number": 1, "periodType": "SO"}, "timeInPeriod": "00:00", "situationCode": "1551"},     # shootout: skipped
             {"periodDescriptor": {"number": 1, "periodType": "REG"}, "timeInPeriod": "11:00", "situationCode": None}]      # unparseable
    sh, ro = T.shifts_frame(chart), T.roster_frame(pbp_of(rs))
    tl = T.timeline(sh, ro, HOME, AWAY)
    ag = T.strength_agreement(tl, {"plays": plays})
    assert ag == {"n": 2, "agree": 1, "state_agree": 1}


def test_pp_unit_guess_from_share_of_team_power_play_time():
    """Home PP lasts 200 s (away skater 15 in the box 100-300). Home skaters 1, 2, 3, 5 stay on for all of it; skater 4 sits 100-140 and
    skater 6 takes his place for those 40 s -> 4 has 160 s (share 0.8, PP1), 6 has 40 s (share 0.2, PP2)."""
    rows = [shift_row(p, HOME, "HHH", 1, 0, 1200) for p in (1, 2, 3, 5)]
    rows += [shift_row(4, HOME, "HHH", 1, 0, 100), shift_row(4, HOME, "HHH", 1, 140, 1200), shift_row(6, HOME, "HHH", 1, 100, 140)]
    rows += [shift_row(p, AWAY, "AAA", 1, 0, 1200) for p in (11, 12, 13, 14)]
    rows += [shift_row(15, AWAY, "AAA", 1, 0, 100), shift_row(15, AWAY, "AAA", 1, 300, 1200), shift_row(901, HOME, "HHH", 1, 0, 1200),
             shift_row(902, AWAY, "AAA", 1, 0, 1200)]
    df, _ = T.build_game(1, {"data": rows}, pbp_of(roster(range(1, 7), range(11, 16))))
    by = df.set_index("player_id")
    assert by.loc[1, "team_pp_s"] == 200 and by.loc[1, "pp_share"] == pytest.approx(1.0) and by.loc[1, "pp_unit_guess"] == 1
    assert by.loc[4, "toi_pp"] == 160 and by.loc[4, "pp_share"] == pytest.approx(0.8) and by.loc[4, "pp_unit_guess"] == 1
    assert by.loc[6, "toi_pp"] == 40 and by.loc[6, "pp_share"] == pytest.approx(0.2) and by.loc[6, "pp_unit_guess"] == 2
    assert (by.loc[11:14, "pp_unit_guess"] == 0).all()                                 # the penalty killers have no PP time


def test_pp_unit_threshold_is_inclusive_at_half_and_incidental_time_is_unit_zero():
    """PP = 100-220 (120 s). Skater 5 sits 130-190 and 6 covers it (60 s each = exactly 0.5 -> both PP1); skater 1 sits 110-115 and
    skater 7 covers those 5 s (under MIN_PP_S -> unit 0)."""
    rows = [shift_row(p, HOME, "HHH", 1, 0, 1200) for p in (2, 3, 4)]
    rows += [shift_row(5, HOME, "HHH", 1, 0, 130), shift_row(5, HOME, "HHH", 1, 190, 1200), shift_row(6, HOME, "HHH", 1, 130, 190)]
    rows += [shift_row(1, HOME, "HHH", 1, 0, 110), shift_row(1, HOME, "HHH", 1, 115, 1200), shift_row(7, HOME, "HHH", 1, 110, 115)]
    rows += [shift_row(p, AWAY, "AAA", 1, 0, 1200) for p in (11, 12, 13, 14)]
    rows += [shift_row(15, AWAY, "AAA", 1, 0, 100), shift_row(15, AWAY, "AAA", 1, 220, 1200), shift_row(901, HOME, "HHH", 1, 0, 1200),
             shift_row(902, AWAY, "AAA", 1, 0, 1200)]
    df, _ = T.build_game(1, {"data": rows}, pbp_of(roster(range(1, 8), range(11, 16))))
    by = df.set_index("player_id")
    assert by.loc[6, "team_pp_s"] == 120
    assert (by.loc[5, "toi_pp"], by.loc[6, "toi_pp"]) == (60, 60) and by.loc[5, "pp_unit_guess"] == 1 and by.loc[6, "pp_unit_guess"] == 1
    assert by.loc[7, "toi_pp"] == 5 and by.loc[7, "pp_unit_guess"] == 0 and by.loc[1, "toi_pp"] == 115


def test_overtime_and_playoff_periods_use_the_abs_axis_and_regular_season_drops_shootout():
    rows = [shift_row(p, HOME, "HHH", 4, 0, 300) for p in range(1, 4)] + [shift_row(p, AWAY, "AAA", 4, 0, 300) for p in range(11, 14)]
    rows += [shift_row(901, HOME, "HHH", 4, 0, 300), shift_row(902, AWAY, "AAA", 4, 0, 300)]
    rows += [shift_row(1, HOME, "HHH", 5, 0, 60)]                                      # a "period 5" row in a regular-season game
    rs = roster(range(1, 4), range(11, 14))
    reg, _ = T.build_game(1, {"data": rows}, pbp_of(rs, game_type=2))
    assert reg.set_index("player_id").loc[1, "toi_total"] == 300 and (reg["toi_pp"] == 0).all()    # 3v3 = even strength
    po, _ = T.build_game(1, {"data": rows}, pbp_of(rs, game_type=3))
    assert po.set_index("player_id").loc[1, "toi_total"] == 360                                       # playoff period 5 = a second OT


def test_missing_sources_are_reported_not_raised():
    chart, rs = one_period_game()
    assert T.build_game(5, None, pbp_of(rs))[1]["status"] == "no_shiftchart"
    assert T.build_game(5, {"data": []}, pbp_of(rs))[1]["status"] == "no_shiftchart"
    assert T.build_game(5, chart, None)[1]["status"] == "no_pbp_roster"
    assert T.build_game(5, chart, {"rosterSpots": []})[1]["status"] == "no_pbp_roster"
    assert T.build_game(5, {"data": [shift_row(1, HOME, "H", 1, 5, 5)]}, pbp_of(rs))[1]["status"] == "empty_shifts"


# ---------------------------------------------------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------------------------------------------------
def test_reconcile_reports_exact_and_mismatch_rates():
    sp = pd.DataFrame({"game_id": [1, 1, 1, 1], "player_id": [1, 2, 3, 4], "toi_total": [100, 200, 300, 400],
                       "toi_ev": [90, 190, 295, 390], "toi_pp": [5, 5, 3, 5], "toi_pk": [5, 5, 2, 5]})
    box = pd.DataFrame({"game_id": [1, 1, 1, 1, 1], "player_id": [1, 2, 3, 4, 9], "toi_sec": [100, 200, 304, 460, 800]})
    r = T.reconcile(sp, box)
    assert r["n_rows"] == 4 and r["exact"] == 0.5 and r["within_5s"] == 0.75 and r["over_5s"] == 0.25
    assert r["max_abs_s"] == 60 and r["n_box_rows_without_split"] == 1 and r["n_split_rows_without_box"] == 0
    assert r["mean_abs_s"] == pytest.approx((0 + 0 + 4 + 60) / 4)


def test_compare_official_and_official_frame():
    payload = {"data": [{"gameId": 2024020001, "playerId": 1, "teamAbbrev": "AAA", "gameDate": "2024-10-01", "timeOnIce": 1000,
                         "evTimeOnIce": 800, "ppTimeOnIce": 120, "shTimeOnIce": 80},
                        {"gameId": 2024010001, "playerId": 2, "teamAbbrev": "AAA", "gameDate": "2024-09-25", "timeOnIce": 5,
                         "evTimeOnIce": 5, "ppTimeOnIce": 0, "shTimeOnIce": 0}]}
    off = T.official_frame(payload)
    assert list(off["player_id"]) == [1]                                              # the preseason row is dropped
    sp = pd.DataFrame({"game_id": [2024020001], "player_id": [1], "toi_ev": [803], "toi_pp": [118], "toi_pk": [79]})
    c = T.compare_official(sp, off)
    assert c["n_rows"] == 1 and c["ev"]["mean_signed_s"] == 3 and c["pp"]["mean_abs_s"] == 2 and c["pk"]["within_5s"] == 1.0
    assert c["official_parts_sum_eq_total"] == 1.0
    assert T.official_frame(None).empty


def test_official_as_splits_estimates_team_pp_and_units():
    off = pd.DataFrame({"game_id": [1] * 6, "player_id": range(6), "team": ["A"] * 6, "date": "2024-10-01", "off_total": 1000,
                        "off_ev": [800, 800, 800, 800, 800, 800], "off_pp": [100, 100, 100, 100, 100, 20], "off_pk": 0})
    s = T.official_as_splits(off)
    assert s["team_pp_s"].iloc[0] == pytest.approx(520 / 5)
    assert list(s["pp_unit_guess"]) == [1, 1, 1, 1, 1, 2]


def test_unit_structure_counts_first_unit_sizes():
    rows = []
    for gid, n1 in ((1, 5), (2, 4)):
        for i in range(8):
            rows.append({"game_id": gid, "team": "A", "player_id": gid * 100 + i, "team_pp_s": 200, "pp_unit_guess": 1 if i < n1 else 2})
    u = T.unit_structure(pd.DataFrame(rows))
    assert u["n_team_games"] == 2 and u["share_exactly_5"] == 0.5 and u["share_4_to_6"] == 1.0


# ---------------------------------------------------------------------------------------------------------------------
# game selection, cache, resumable runner, GCS gate
# ---------------------------------------------------------------------------------------------------------------------
def games_frame():
    return pd.DataFrame({"game_id": [2024010001, 2024020001, 2024020002, 2024020003, 2024020004, 2024030001],
                         "season_year": 2024, "game_type": [1, 2, 2, 2, 2, 3], "date": ["2024-09-25", "2024-10-08", "2024-10-08",
                                                                                        "2024-10-08", "2024-10-09", "2025-04-20"],
                         "state": ["FINAL", "OFF", "OFF", "FUT", "OFF", "OFF"]})


def test_select_games_filters_types_state_dates_and_caps():
    g = games_frame()
    assert T.select_games(g) == [2024020001, 2024020002, 2024020004, 2024030001]               # no preseason, no unplayed
    assert T.select_games(g, dates=["2024-10-08"]) == [2024020001, 2024020002]
    assert T.select_games(g, dates=["2024-10-08"], max_per_date=1) == [2024020001]
    assert T.select_games(g, day_from="2024-10-09", day_to="2025-01-01") == [2024020004]
    a = T.select_games(g, limit=2, seed=3)
    assert a == T.select_games(g, limit=2, seed=3) and len(a) == 2
    assert T.select_games(g, limit=2) == [2024020001, 2024020002]


def test_raw_cache_roundtrip_and_negative_cache(tmp_path):
    c = T.RawCache(tmp_path)
    calls = []
    assert c.fetch("shifts", 7, lambda: calls.append(1) or {"a": 1}) == {"a": 1}
    assert c.fetch("shifts", 7, lambda: calls.append(1) or {"a": 2}) == {"a": 1}
    assert c.fetch("pbp", 8, lambda: calls.append(1)) is None and c.fetch("pbp", 8, lambda: calls.append(1) or {"x": 1}) is None
    assert len(calls) == 2
    assert (tmp_path / "shifts" / "7.json.gz").exists()
    assert json.loads(gzip.decompress((tmp_path / "shifts" / "7.json.gz").read_bytes())) == {"a": 1}


class FakeStats:
    def __init__(self, charts):
        self.charts, self.calls = charts, []

    def shiftcharts(self, gid):
        self.calls.append(("shifts", int(gid)))
        return self.charts.get(int(gid))


class FakeWeb:
    def __init__(self, pbps):
        self.pbps, self.calls = pbps, []

    def play_by_play(self, gid):
        self.calls.append(("pbp", int(gid)))
        return self.pbps.get(int(gid))


def test_run_is_resumable_and_records_failures(tmp_path):
    chart, rs = one_period_game()
    charts = {1: chart, 2: chart, 3: None}
    pbps = {1: pbp_of(rs), 2: pbp_of(rs), 3: pbp_of(rs)}
    cl, web = FakeStats(charts), FakeWeb(pbps)
    out = run_to(tmp_path, [1, 2, 3], cl, web)
    assert out["games_built"] == 2 and out["rows"] == 20 and out["status_counts"] == {"ok": 2, "no_shiftchart": 1}
    assert len(cl.calls) == 3 and len(web.calls) == 3
    # a re-run with a new game requests only the new game; nothing already built or already failed is asked again
    charts[4], pbps[4] = chart, pbp_of(rs)
    out2 = run_to(tmp_path, [1, 2, 3, 4], cl, web)
    assert out2["games_built"] == 3 and out2["new_games"] == 1
    assert len(cl.calls) == 4 and cl.calls[-1] == ("shifts", 4) and web.calls[-1] == ("pbp", 4)
    # the cache serves a different output dir without any request
    out3 = T.run([1, 2], cache_dir=tmp_path / "cache", out_dir=tmp_path / "out_b", client=cl, web_client=web)
    assert out3["games_built"] == 2 and len(cl.calls) == 4
    df = pd.read_csv(tmp_path / "out" / "toi_splits.csv")
    assert list(df.columns) == T.OUT_COLS


def run_to(tmp_path, ids, cl, web):
    return T.run(ids, cache_dir=tmp_path / "cache", out_dir=tmp_path / "out", client=cl, web_client=web, flush_every=1)


def test_nothing_is_written_to_gcs_unless_the_flag_is_given(tmp_path, monkeypatch):
    from mlb_core import storage
    writes = []
    monkeypatch.setattr(storage, "write_csv", lambda df, key, **kw: writes.append(key))
    monkeypatch.setattr(storage, "write_bytes", lambda *a, **k: writes.append("bytes"))
    chart, rs = one_period_game()
    T.run([1], cache_dir=tmp_path / "c", out_dir=tmp_path / "o", client=FakeStats({1: chart}), web_client=FakeWeb({1: pbp_of(rs)}))
    assert writes == []
    # main without --write-gcs: stays local too (the selectors are bypassed with --game-ids; the fake clients are monkeypatched in)
    monkeypatch.setattr(T, "StatsClient", lambda delay=1.0: FakeStats({1: chart}))
    monkeypatch.setattr(T, "NhlClient", lambda delay=1.0: FakeWeb({1: pbp_of(rs)}))
    assert T.main(["--game-ids", "1", "--cache-dir", str(tmp_path / "c2"), "--out-dir", str(tmp_path / "o2")]) == 0
    assert writes == []
    assert T.main(["--game-ids", "1", "--cache-dir", str(tmp_path / "c2"), "--out-dir", str(tmp_path / "o2"), "--write-gcs"]) == 0
    assert writes == [T.GCS_KEY]


def test_stats_client_urls_and_defaults():
    class Resp:
        status_code, headers = 200, {}

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": []}

    class Sess:
        headers = {}

        def __init__(self):
            self.urls = []

        def get(self, url, timeout=0):
            self.urls.append(url)
            return Resp()

    s = Sess()
    c = T.StatsClient(delay=0, session=s)
    c.shiftcharts(2024020500)
    c.official_toi("2024-11-20", "2024-11-27")
    assert s.urls[0] == "https://api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId=2024020500"
    assert s.urls[1].startswith("https://api.nhle.com/stats/rest/en/skater/timeonice?isGame=true&limit=-1&cayenneExp=gameDate%3E%3D")
    assert "2024-11-27" in s.urls[1]
    assert T.StatsClient().delay == 1.0


def test_date_windows_and_the_row_cap_guard(tmp_path):
    assert T.date_windows("2024-01-01", "2024-01-20", 7) == [("2024-01-01", "2024-01-07"), ("2024-01-08", "2024-01-14"),
                                                              ("2024-01-15", "2024-01-20")]

    class Capped:
        def official_toi(self, a, b):
            return {"data": [{"gameId": 2024020001, "playerId": i, "teamAbbrev": "A", "gameDate": a, "timeOnIce": 1, "evTimeOnIce": 1,
                              "ppTimeOnIce": 0, "shTimeOnIce": 0} for i in range(10000)]}

    with pytest.raises(RuntimeError, match="cap"):
        T.fetch_official("2024-01-01", "2024-01-14", T.RawCache(tmp_path), Capped())


def test_output_is_ascii_only():
    import pathlib
    for p in ("nhl/data/toi_splits.py", "nhl/props/toi_feat.py"):
        txt = (pathlib.Path(__file__).resolve().parent.parent / p).read_bytes()
        assert all(b < 128 for b in txt), p
