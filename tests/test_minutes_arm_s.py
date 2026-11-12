"""Tests for nba.minutes_restriction (Arm S harness) + scripts/minutes_arm_s_backtest.py.

SYNTHETIC FIXTURES ONLY: every box score and injury report below is hand-built in this file.
Nothing here touches GCS, the network, or a real backtest run; the CLI is exercised with its
loaders monkeypatched, and the no---execute path must not call them at all.
"""
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from nba.minutes_restriction import arm_s, cases, grade
from nba.minutes_restriction.cases import build_cases

REPORT_COLUMNS = ["report_ts_et", "game_date", "game_time_et", "matchup", "team", "team_code",
                  "player_raw", "player", "status", "reason", "reason_cat"]


# -----------------------------------------------------------------------------------------------
# Fixture builders

def _row(game_id, season_end, day, player_id, name, team, minutes, starter=False, dnp=False,
         reason=None):
    return {"game_id": game_id, "season": season_end, "season_type": 2,
            "game_date": day.isoformat(), "game_date_time": f"{day.isoformat()}T19:30:00-05:00",
            "athlete_id": float(player_id), "athlete_display_name": name,
            "team_abbreviation": team, "minutes": float(minutes), "starter": bool(starter),
            "did_not_play": bool(dnp), "reason": reason, "home_away": "home",
            "opponent_team_id": 2}


def _slate(start: date, n: int, first_id: int, step_days: int = 2):
    return [(first_id + i, start + timedelta(days=step_days * i)) for i in range(n)]


def _player_rows(slate, season_end, player_id, name, team, spec):
    """spec per game: ("play", minutes[, starter]) | ("dnp", reason) | ("out",) = no row at all."""
    rows = []
    for (gid, day), entry in zip(slate, spec):
        if entry[0] == "out":
            continue
        if entry[0] == "play":
            rows.append(_row(gid, season_end, day, player_id, name, team, entry[1],
                             starter=entry[2] if len(entry) > 2 else False))
        else:
            rows.append(_row(gid, season_end, day, player_id, name, team, 0.0,
                             dnp=True, reason=entry[1]))
    return rows


def _plays(n, minutes, starter_first=False):
    return [("play", float(minutes), bool(starter_first and i == 0)) for i in range(n)]


LAL_SLATE = _slate(date(2022, 1, 3), 14, 1001)          # Jan 3 .. Jan 29, 2022 (games 1001-1014)
BOS_SLATE = _slate(date(2022, 2, 2), 12, 2001)          # Feb 2 .. Feb 24, 2022 (games 2001-2012)
INJ_ANKLE = "LEFT ANKLE SPRAIN"
INJ_KNEE = "RIGHT KNEE SORENESS"


def season_box() -> pd.DataFrame:
    """One 2021-22 slice: returners, a coach-decision scratch, a short-history player, a trade."""
    rows = []
    # Ironmen: establish both teams' calendars (every game exists even when a player is unlisted).
    rows += _player_rows(LAL_SLATE, 2022, 900, "Iron Man", "LAL", _plays(14, 32))
    rows += _player_rows(BOS_SLATE, 2022, 900, "Iron Man", "LAL", _plays(12, 32))
    rows += _player_rows(BOS_SLATE, 2022, 901, "Boston Iron", "BOS", _plays(12, 30))
    rows += _player_rows(LAL_SLATE, 2022, 901, "Boston Iron", "BOS", _plays(14, 30))
    # Alpha (1): 8x30, 3 missed (ankle), returns 20 (starter), game-2 26, then 30.
    rows += _player_rows(LAL_SLATE, 2022, 1, "Alpha Returner", "LAL",
                         _plays(8, 30) + [("dnp", INJ_ANKLE)] * 3
                         + [("play", 20.0, True), ("play", 26.0), ("play", 30.0)])
    # Echo (5): rising minutes 10..28, 2 missed (knee), returns 21, game-2 22.
    rows += _player_rows(LAL_SLATE, 2022, 5, "Echo Baseline", "LAL",
                         [("play", float(m)) for m in (10, 12, 14, 16, 18, 20, 22, 24, 26, 28)]
                         + [("dnp", INJ_KNEE)] * 2 + [("play", 21.0), ("play", 22.0)])
    # Bravo (2): 8x28, 4 COACH'S DECISION, back at 10/12 -- role loss, never a case.
    rows += _player_rows(LAL_SLATE, 2022, 2, "Bravo Coach", "LAL",
                         _plays(8, 28) + [("dnp", "COACH'S DECISION")] * 4
                         + [("play", 10.0), ("play", 12.0)])
    # Delta (4): only 4 played games before his spell -- baseline history too short, never a case.
    rows += _player_rows(LAL_SLATE, 2022, 4, "Delta Short", "LAL",
                         _plays(4, 30) + [("dnp", INJ_ANKLE)] * 2 + _plays(8, 30))
    # Golf (7): exactly 5 played games, 2 missed (injury), returns once, then unlisted for good.
    rows += _player_rows(LAL_SLATE, 2022, 7, "Golf Five", "LAL",
                         _plays(5, 20) + [("dnp", INJ_KNEE)] * 2 + [("play", 18.0)]
                         + [("out",)] * 6)
    # Foxtrot (6): plays every game at 25 -- designation-class material only.
    rows += _player_rows(LAL_SLATE, 2022, 6, "Foxtrot Flag", "LAL", _plays(14, 25))
    # Hotel (8): 8x22, 1 missed (foot), plays on -- no return case (missed < 2).
    rows += _player_rows(LAL_SLATE, 2022, 8, "Hotel Scratch", "LAL",
                         _plays(8, 22) + [("dnp", "LEFT FOOT SORENESS")] + _plays(5, 22))
    # Charlie (3): 6 games for LAL, traded; 7 games for BOS, 2 missed (knee), returns at 15.
    rows += _player_rows(LAL_SLATE, 2022, 3, "Charlie Trade", "LAL",
                         _plays(6, 24) + [("out",)] * 8)
    rows += _player_rows(BOS_SLATE, 2022, 3, "Charlie Trade", "BOS",
                         _plays(7, 20) + [("dnp", INJ_KNEE)] * 2
                         + [("play", 15.0), ("play", 18.0), ("play", 20.0)])
    return pd.DataFrame(rows)


def _report(day, hhmm, player, status, reason="Injury/Illness - Left Ankle; Sprain",
            reason_cat="injury_illness", team_code="LAL", team="Los Angeles Lakers"):
    return {"report_ts_et": f"{day.isoformat()}T{hhmm}:00-05:00", "game_date": day.isoformat(),
            "game_time_et": "07:30 PM", "matchup": "BOS@LAL", "team": team,
            "team_code": team_code, "player_raw": player.split()[-1] + ", " + player.split()[0],
            "player": player, "status": status, "reason": reason, "reason_cat": reason_cat}


def reports_frame() -> pd.DataFrame:
    rows = [
        # Alpha, return game Jan 25: eligible Questionable at noon; a post-cutoff upgrade to
        # Available at 19:00 (30 min pre-tip) must NEVER reach the features.
        _report(date(2022, 1, 25), "12:00", "Alpha Returner", "Questionable"),
        _report(date(2022, 1, 25), "19:00", "Alpha Returner", "Available"),
        # Echo, return game Jan 27: Questionable at noon, Probable at 17:30 (last eligible),
        # Available at 19:00 (post-cutoff, invisible).
        _report(date(2022, 1, 27), "12:00", "Echo Baseline", "Questionable",
                reason="Injury/Illness - Right Knee; Soreness"),
        _report(date(2022, 1, 27), "17:30", "Echo Baseline", "Probable",
                reason="Injury/Illness - Right Knee; Soreness"),
        _report(date(2022, 1, 27), "19:00", "Echo Baseline", "Available",
                reason="Injury/Illness - Right Knee; Soreness"),
        # Foxtrot, ordinary game Jan 13: Probable at decision time -> designation case.
        _report(date(2022, 1, 13), "17:00", "Foxtrot Flag", "Probable",
                reason="Injury/Illness - Right Hip; Soreness"),
        # Bravo, Jan 27 return from a COACH spell: Probable, but the spell exclusion wins.
        _report(date(2022, 1, 27), "17:00", "Bravo Coach", "Probable", reason="Rest",
                reason_cat="rest"),
        # Hotel, Jan 19: Questionable -- but he does not dress that night, so no case.
        _report(date(2022, 1, 19), "17:00", "Hotel Scratch", "Questionable",
                reason="Injury/Illness - Left Foot; Soreness"),
        # Golf, Jan 17 return game: Questionable -- attaches to his RETURN case (no second case).
        _report(date(2022, 1, 17), "17:00", "Golf Five", "Questionable",
                reason="Injury/Illness - Right Knee; Soreness"),
    ]
    return pd.DataFrame(rows, columns=REPORT_COLUMNS)


def _by_player(df, name):
    return df[df["player"] == name]


# -----------------------------------------------------------------------------------------------
# (a) absence spells, boundedness, team change

def test_spell_detection_return_and_game_two():
    out = build_cases(season_box())
    alpha = _by_player(out, "Alpha Returner")
    assert sorted(alpha["case_class"]) == ["game_two", "return"]
    ret = alpha[alpha["case_class"] == "return"].iloc[0]
    assert ret["game_id"] == 1012 and ret["games_missed"] == 3 and ret["missed_band"] == "2-3"
    assert ret["team"] == "LAL" and ret["opponent"] == "BOS"
    assert ret["absence_reason_class"] == "INJURY" and ret["is_injury_return"]
    assert ret["season"] == 2022 and ret["season_start"] == 2021 and ret["season_label"] == "2021-22"
    assert not ret["is_game_two"] and ret["started"]


def test_spell_must_be_bounded_and_stints_split_on_team_change():
    out = build_cases(season_box())
    charlie = _by_player(out, "Charlie Trade")
    # Exactly one case: the BOS return. The trailing LAL absence after the trade is unbounded
    # (no later LAL played game), so it is not a spell and yields nothing.
    assert len(charlie) == 2  # BOS return + its game-2
    assert set(charlie["team"]) == {"BOS"}
    ret = charlie[charlie["case_class"] == "return"].iloc[0]
    assert ret["games_missed"] == 2 and ret["game_id"] == 2010


def test_unlisted_and_short_spells():
    out = build_cases(season_box())
    # Hotel missed exactly 1 game: no return case, and without reports no designation case.
    assert not len(_by_player(out, "Hotel Scratch"))
    # Ironmen never miss: no cases.
    assert not len(_by_player(out, "Iron Man"))


# -----------------------------------------------------------------------------------------------
# (b) baseline math

def test_baseline_is_prior_eight_mean_within_stint():
    out = build_cases(season_box())
    echo = _by_player(out, "Echo Baseline")
    ret = echo[echo["case_class"] == "return"].iloc[0]
    # Last 8 played before the spell: 14,16,18,20,22,24,26,28 -> 21.0 exactly.
    assert ret["baseline_minutes"] == pytest.approx(21.0)
    assert ret["realized_minutes"] == 21.0 and ret["realized_ratio"] == pytest.approx(1.0)
    alpha = _by_player(out, "Alpha Returner")
    assert alpha["baseline_minutes"].tolist() == [30.0, 30.0]  # game-2 keeps the spell baseline
    charlie = _by_player(out, "Charlie Trade")
    ret_c = charlie[charlie["case_class"] == "return"].iloc[0]
    assert ret_c["baseline_minutes"] == pytest.approx(20.0)  # BOS stint only, not his 24 at LAL


def test_baseline_requires_five_played_games():
    out = build_cases(season_box())
    assert not len(_by_player(out, "Delta Short"))       # 4 played before the spell: no case
    golf = _by_player(out, "Golf Five")                    # exactly 5: a case exists
    assert len(golf) == 1 and golf.iloc[0]["case_class"] == "return"
    assert golf.iloc[0]["baseline_minutes"] == pytest.approx(20.0)


# -----------------------------------------------------------------------------------------------
# (c) decision-time designation selection -- the honesty core

def test_designation_frozen_at_last_report_at_least_60_min_pre_tip():
    out = build_cases(season_box(), reports_frame())
    alpha = _by_player(out, "Alpha Returner")
    ret = alpha[alpha["case_class"] == "return"].iloc[0]
    # The 19:00 upgrade to Available sits inside the 60-minute cutoff and must not appear.
    assert ret["designation"] == "Questionable"
    assert ret["decision_report_ts"] == pd.Timestamp("2022-01-25 12:00", tz="America/New_York")
    assert ret["reason_cat"] == "injury_illness"
    assert ret["injury_location"] == "ankle" and ret["injury_side"] == "left"

    echo = _by_player(out, "Echo Baseline")
    ret_e = echo[echo["case_class"] == "return"].iloc[0]
    # Last ELIGIBLE report wins: Probable at 17:30, not the noon Questionable, never the
    # post-cutoff 19:00 Available.
    assert ret_e["designation"] == "Probable"
    assert ret_e["decision_report_ts"] == pd.Timestamp("2022-01-27 17:30", tz="America/New_York")
    assert ret_e["injury_location"] == "knee" and ret_e["injury_side"] == "right"


def test_designation_class_requires_dressing_with_q_or_probable():
    out = build_cases(season_box(), reports_frame())
    fox = _by_player(out, "Foxtrot Flag")
    assert len(fox) == 1
    case = fox.iloc[0]
    assert case["case_class"] == "designation" and case["designation"] == "Probable"
    assert case["games_missed"] == 0 and case["missed_band"] == "0"
    assert case["baseline_minutes"] == pytest.approx(25.0)
    # Hotel was Questionable for Jan 19 but did not dress: no case for that game (or any game).
    assert not len(_by_player(out, "Hotel Scratch"))
    # Golf's Questionable attaches to his return case; it does not mint a second designation case.
    golf = _by_player(out, "Golf Five")
    assert len(golf) == 1 and golf.iloc[0]["case_class"] == "return"
    assert golf.iloc[0]["designation"] == "Questionable"


def test_no_reports_means_no_designation_anywhere():
    out = build_cases(season_box())
    assert out["designation"].isna().all() and out["decision_report_ts"].isna().all()


# -----------------------------------------------------------------------------------------------
# (d) COACH_DECISION exclusion

def test_coach_decision_spells_are_excluded_everywhere():
    plain = build_cases(season_box())
    assert not len(_by_player(plain, "Bravo Coach"))
    # Even a Probable designation on the return game cannot resurrect a role-loss spell.
    with_reports = build_cases(season_box(), reports_frame())
    assert not len(_by_player(with_reports, "Bravo Coach"))
    assert cases.classify_absence_reason("COACH'S DECISION") == "COACH"


def test_reason_classifier_parity_with_espn_data():
    from nba.props.espn_data import classify_reason
    battery = ["COACH'S DECISION", "LEFT ANKLE SPRAIN", "LEFT KNEE INJURY MANAGEMENT", "REST",
               "LOAD MANAGEMENT", "ILLNESS", "SUSPENDED", "NOT WITH TEAM", "PERSONAL REASONS",
               "DID NOT DRESS", "G LEAGUE - TWO-WAY", None, ""]
    for text in battery:
        assert cases.classify_absence_reason(text) == classify_reason(text), text


# -----------------------------------------------------------------------------------------------
# (e) game-2 cohort

def test_game_two_emission_and_flags():
    out = build_cases(season_box())
    alpha = _by_player(out, "Alpha Returner")
    g2 = alpha[alpha["case_class"] == "game_two"].iloc[0]
    assert g2["is_game_two"] and g2["game_id"] == 1013
    assert g2["games_missed"] == 3 and g2["baseline_minutes"] == pytest.approx(30.0)
    assert g2["realized_minutes"] == 26.0 and g2["is_injury_return"]
    # The third game back is an ordinary game: not a case.
    assert 1014 not in set(alpha["game_id"])
    # Golf never plays again after his return: no game-2.
    golf = _by_player(out, "Golf Five")
    assert not golf["is_game_two"].any()


# -----------------------------------------------------------------------------------------------
# Schema

def test_case_schema_is_the_lane4_contract():
    required = ["case_id", "player", "team", "game_date", "tip_ts", "decision_report_ts",
                "designation", "games_missed", "missed_band", "baseline_minutes",
                "realized_minutes", "realized_ratio", "case_class", "is_game_two"]
    assert set(required) <= set(cases.CASE_COLUMNS)
    out = build_cases(season_box(), reports_frame())
    assert list(out.columns) == cases.CASE_COLUMNS
    assert out["case_id"].is_unique


# -----------------------------------------------------------------------------------------------
# (f) naive vs model MAE math on known values

def _pred_frame(rows):
    base = {"season_start": 2024, "season_label": "2024-25", "case_class": "return",
            "is_game_two": False, "is_injury_return": True, "missed_band": "2-3"}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_cohort_mae_math():
    preds = _pred_frame([
        {"games_missed": 2, "realized_minutes": 20.0, "naive_minutes": 30.0,
         "pred_minutes": 22.0, "pred_ratio": 22 / 30, "realized_ratio": 20 / 30},
        {"games_missed": 3, "realized_minutes": 20.0, "naive_minutes": 30.0,
         "pred_minutes": 22.0, "pred_ratio": 22 / 30, "realized_ratio": 20 / 30},
    ])
    stats = grade.cohort_stats(preds)
    assert stats["n"] == 2
    assert stats["naive_mae"] == pytest.approx(10.0)
    assert stats["arm_s_mae"] == pytest.approx(2.0)
    assert stats["rel_improvement"] == pytest.approx(0.8)


def test_model_fallback_is_dev_mean_ratio():
    train = pd.DataFrame({
        "case_id": ["a", "b", "c"], "player": ["P"] * 3, "team": ["LAL"] * 3,
        "game_date": [date(2022, 1, 3)] * 3, "season_start": [2021] * 3,
        "season_label": ["2021-22"] * 3, "case_class": ["return"] * 3,
        "games_missed": [4, 5, 6], "missed_band": ["4-9"] * 3,
        "is_injury_return": [True] * 3, "is_game_two": [False] * 3,
        "baseline_minutes": [30.0] * 3, "realized_minutes": [24.0, 27.0, 30.0],
        "realized_ratio": [0.8, 0.9, 1.0], "designation": [None] * 3,
        "injury_class": ["INJURY"] * 3, "is_back_to_back": [False] * 3,
    })
    model = arm_s.ArmSModel().fit(train)
    assert model.used_fallback_ and model.n_train_ == 3
    assert model.predict_ratio(train) == pytest.approx([0.9, 0.9, 0.9])


# -----------------------------------------------------------------------------------------------
# (g) bar verdict boundaries

def _boundary_preds(pred_minutes):
    # One missed>=4 injury case: baseline 20, realized 10 -> naive MAE exactly 10.0.
    return _pred_frame([{"games_missed": 5, "missed_band": "4-9", "realized_minutes": 10.0,
                         "naive_minutes": 20.0, "pred_minutes": pred_minutes,
                         "pred_ratio": pred_minutes / 20.0, "realized_ratio": 0.5}])


def test_bar_boundary_exactly_15_percent_passes():
    report = grade.grade(_boundary_preds(18.5))   # Arm MAE 8.5 -> improvement exactly 0.15
    bar = report["bars"]["missed_ge4_injury_mae_cut"]
    assert bar["observed"] == pytest.approx(0.15)
    assert bar["pass"] is True


def test_bar_boundary_14_9_percent_fails():
    report = grade.grade(_boundary_preds(18.51))  # Arm MAE 8.51 -> improvement 0.149
    bar = report["bars"]["missed_ge4_injury_mae_cut"]
    assert bar["observed"] == pytest.approx(0.149)
    assert bar["pass"] is False
    assert report["verdict"] == "FAIL"


def test_grade_fails_closed_on_empty_predictions():
    report = grade.grade(pd.DataFrame(columns=["season_start", "season_label", "is_game_two",
                                               "games_missed", "is_injury_return", "missed_band",
                                               "realized_minutes", "naive_minutes",
                                               "pred_minutes", "pred_ratio", "realized_ratio"]))
    assert report["verdict"] == "FAIL"
    assert report["bars"]["full_population_mae_cut"]["observed"] is None
    assert "Verdict: FAIL" in grade.render_markdown(report)


# -----------------------------------------------------------------------------------------------
# (h) walk-forward split: no test-season case in any fit set

def _case_row(case_id, season_start, games_missed, ratio, baseline=30.0):
    return {"case_id": case_id, "player": "P", "team": "LAL", "game_date": date(season_start, 11, 1),
            "season_start": season_start, "season_label": cases.season_label(season_start),
            "case_class": "return", "games_missed": games_missed,
            "missed_band": cases.missed_band(games_missed), "is_injury_return": True,
            "is_game_two": False, "baseline_minutes": baseline,
            "realized_minutes": baseline * ratio, "realized_ratio": ratio,
            "designation": "Questionable", "injury_class": "injury_illness",
            "is_back_to_back": False}


def test_walk_forward_fits_dev_only_and_grades_test_only():
    rows = []
    for season in arm_s.DEV_SEASONS:
        for i, missed in enumerate((0, 2, 4, 6, 8, 10)):
            rows.append(_case_row(f"dev-{season}-{i}", season, missed, 1.0 - 0.03 * missed))
    for season in arm_s.TEST_SEASONS:
        for i, missed in enumerate((2, 5, 9, 12)):
            rows.append(_case_row(f"test-{season}-{i}", season, missed, 1.0 - 0.03 * missed))
    all_cases = pd.DataFrame(rows)

    fit = arm_s.dev_fit_frame(all_cases)
    assert set(fit["season_start"]) == set(arm_s.DEV_SEASONS)
    assert not (set(fit["case_id"]) & set(all_cases[all_cases["season_start"].isin(
        arm_s.TEST_SEASONS)]["case_id"]))

    preds = arm_s.walk_forward(all_cases)
    assert set(preds["season_start"]) == set(arm_s.TEST_SEASONS)
    assert preds.attrs["fit_seasons"] == sorted(arm_s.DEV_SEASONS)
    assert set(preds["case_id"]) <= {f"test-{s}-{i}" for s in arm_s.TEST_SEASONS for i in range(4)}
    # The naive arm is the baseline itself, and predictions stay inside the clip.
    assert (preds["naive_minutes"] == preds["baseline_minutes"]).all()
    assert preds["pred_ratio"].between(*arm_s.RATIO_CLIP).all()
    # Deterministic: a second fit produces identical predictions.
    preds2 = arm_s.walk_forward(all_cases)
    pd.testing.assert_frame_equal(preds, preds2)


# -----------------------------------------------------------------------------------------------
# (i) CLI guard: no --execute, no data access

def _load_cli():
    path = Path(__file__).parent.parent / "scripts" / "minutes_arm_s_backtest.py"
    spec = importlib.util.spec_from_file_location("minutes_arm_s_backtest", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_refuses_without_execute(monkeypatch, capsys):
    cli = _load_cli()

    def forbidden(path):
        raise AssertionError(f"loader called without --execute: {path}")

    monkeypatch.setattr(cli, "load_box_scores", forbidden)
    monkeypatch.setattr(cli, "load_designations", forbidden)
    assert cli.main([]) == 0
    assert cli.main(["--box-path", "somewhere", "--reports-path", "elsewhere"]) == 0
    out = capsys.readouterr().out
    assert "EXECUTION DISABLED" in out and "--execute" in out


def _multi_season_box() -> pd.DataFrame:
    """Tiny synthetic multi-season box for the CLI execute path (loaders are mocked to return it)."""
    rows = []
    for season_start in (2021, 2022, 2023, 2024, 2025):
        slate = _slate(date(season_start, 10, 20), 16, season_start * 1000)
        rows += _player_rows(slate, season_start + 1, 900, "Iron Man", "LAL", _plays(16, 32))
        rows += _player_rows(slate, season_start + 1, 901, "Boston Iron", "BOS", _plays(16, 30))
        rows += _player_rows(slate, season_start + 1, 1, "Alpha Returner", "LAL",
                             _plays(8, 30) + [("dnp", INJ_ANKLE)] * 4
                             + [("play", 20.0), ("play", 24.0), ("play", 28.0), ("play", 30.0)])
        rows += _player_rows(slate, season_start + 1, 5, "Echo Baseline", "LAL",
                             _plays(8, 26) + [("dnp", INJ_KNEE)] * 2
                             + [("play", 22.0), ("play", 24.0)] + _plays(4, 26))
    return pd.DataFrame(rows)


def test_cli_execute_with_mocked_loaders_writes_reports(monkeypatch, tmp_path, capsys):
    cli = _load_cli()
    box = _multi_season_box()
    monkeypatch.setattr(cli, "load_box_scores", lambda path: box)
    monkeypatch.setattr(cli, "load_designations",
                        lambda path: (_ for _ in ()).throw(AssertionError("no reports path given")))
    out_json, out_md = tmp_path / "report.json", tmp_path / "report.md"
    rc = cli.main(["--execute", "--box-path", "mocked", "--out-json", str(out_json),
                   "--out-md", str(out_md)])
    assert rc == 0
    report = json.loads(out_json.read_text())
    assert report["verdict"] in ("PASS", "FAIL")
    assert report["n_cases"] > 0 and report["n_predictions"] > 0
    assert set(report["seasons"]) == {"2024-25", "2025-26"}
    assert "Verdict:" in out_md.read_text()
    assert "Verdict:" in capsys.readouterr().out


# -----------------------------------------------------------------------------------------------
# (g) designation index: the grouped lookup must be indistinguishable from the full-frame scan

def _assert_same_selection(scan, indexed):
    assert (scan is None) == (indexed is None)
    if scan is None:
        return
    assert scan["designation"] == indexed["designation"]
    assert scan["decision_report_ts"] == indexed["decision_report_ts"]
    assert scan["report_reason"] == indexed["report_reason"]
    assert scan["reason_cat"] == indexed["reason_cat"]


def test_designation_index_matches_scan():
    norm = cases._reports_normalized(reports_frame())
    index = cases.build_designation_index(norm)
    tip = pd.Timestamp("2022-01-25 19:30", tz="America/New_York")
    queries = [
        # Eligible pre-cutoff report; the post-cutoff upgrade is invisible to both paths.
        ("Alpha Returner", "LAL", date(2022, 1, 25), tip),
        # Last eligible wins (17:30 Probable over noon Questionable) in both paths.
        ("Echo Baseline", "LAL", date(2022, 1, 27),
         pd.Timestamp("2022-01-27 19:30", tz="America/New_York")),
        ("Foxtrot Flag", "LAL", date(2022, 1, 13),
         pd.Timestamp("2022-01-13 19:30", tz="America/New_York")),
        # Selection answers even where no case is built (Hotel did not dress that night).
        ("Hotel Scratch", "LAL", date(2022, 1, 19),
         pd.Timestamp("2022-01-19 19:30", tz="America/New_York")),
        # Misses: unknown player, wrong team, wrong date.
        ("Nobody Here", "LAL", date(2022, 1, 13), tip),
        ("Foxtrot Flag", "BOS", date(2022, 1, 13), tip),
        ("Foxtrot Flag", "LAL", date(2022, 1, 14), tip),
        # Tip 30 min after the noon report: the cutoff precedes it, so both paths return None.
        ("Alpha Returner", "LAL", date(2022, 1, 25),
         pd.Timestamp("2022-01-25 12:30", tz="America/New_York")),
        # Suffix / punctuation folding resolves identically ("Jr." stripped by _norm_name).
        ("Alpha Returner Jr.", "LAL", date(2022, 1, 25), tip),
    ]
    for player, team, day, query_tip in queries:
        scan = cases.select_designation(norm, player=player, team=team, game_date=day,
                                        tip_ts=query_tip)
        indexed = cases.select_designation(norm, player=player, team=team, game_date=day,
                                           tip_ts=query_tip, index=index)
        _assert_same_selection(scan, indexed)


def test_designation_index_matches_scan_on_ties_and_cutoff_edge():
    rows = [
        # Two reports at the SAME timestamp: the later row in frame order wins in both paths.
        _report(date(2022, 3, 1), "17:00", "Tie Player", "Questionable"),
        _report(date(2022, 3, 1), "17:00", "Tie Player", "Probable"),
        # A report stamped exactly at tip - 60 min is eligible (<= cutoff) in both paths.
        _report(date(2022, 3, 1), "18:30", "Edge Player", "Questionable"),
    ]
    norm = cases._reports_normalized(pd.DataFrame(rows, columns=REPORT_COLUMNS))
    index = cases.build_designation_index(norm)
    tip = pd.Timestamp("2022-03-01 19:30", tz="America/New_York")
    for player in ("Tie Player", "Edge Player"):
        scan = cases.select_designation(norm, player=player, team="LAL",
                                        game_date=date(2022, 3, 1), tip_ts=tip)
        indexed = cases.select_designation(norm, player=player, team="LAL",
                                           game_date=date(2022, 3, 1), tip_ts=tip, index=index)
        _assert_same_selection(scan, indexed)
    tie = cases.select_designation(norm, player="Tie Player", team="LAL",
                                   game_date=date(2022, 3, 1), tip_ts=tip, index=index)
    assert tie["designation"] == "Probable"      # last in frame order, not first
    edge = cases.select_designation(norm, player="Edge Player", team="LAL",
                                    game_date=date(2022, 3, 1), tip_ts=tip, index=index)
    assert edge["designation"] == "Questionable"  # exactly at the cutoff: eligible


def test_build_cases_with_index_matches_scan_build(monkeypatch):
    indexed = build_cases(season_box(), reports_frame())
    # Force the pre-index path: build_designation_index returning None routes every
    # select_designation call inside build_cases through the full-frame scan.
    monkeypatch.setattr(cases, "build_designation_index", lambda reports_norm: None)
    scanned = build_cases(season_box(), reports_frame())
    pd.testing.assert_frame_equal(indexed, scanned)


def test_cli_export_predictions_writes_frame(monkeypatch, tmp_path):
    cli = _load_cli()
    box = _multi_season_box()
    monkeypatch.setattr(cli, "load_box_scores", lambda path: box)
    out_json, export_csv = tmp_path / "report.json", tmp_path / "predictions.csv"
    rc = cli.main(["--execute", "--box-path", "mocked", "--out-json", str(out_json),
                   "--export-predictions", str(export_csv)])
    assert rc == 0
    report = json.loads(out_json.read_text())
    export = pd.read_csv(export_csv)
    assert len(export) == report["n_predictions"]
    for column in ("case_id", "player", "game_date", "games_missed", "is_injury_return",
                   "designation", "decision_report_ts", "absence_reason_class",
                   "baseline_minutes", "naive_minutes", "pred_minutes", "realized_minutes"):
        assert column in export.columns, column
