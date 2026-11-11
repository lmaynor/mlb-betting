"""Designation banking lane: scripts/bank_nba_designations.py (guarded runner) + nba/data/designation_sources.py
(SportsDataverse interface, ESPN snapshot adapter) + nba_injury_report versioning invariants.

Offline and synthetic only: word geometry is built in code (the pattern tests/test_nba_injury_report.py uses),
no real PDF is downloaded, and the runner tests tripwire backfill/fetch/storage so a disabled run PROVES it
touched nothing. The existing parser suite (test_nba_injury_report.py) is not duplicated; this file covers the
new surfaces plus the two-player / wrapped-reason / NOT YET SUBMITTED geometry the deliverable calls for.
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import mlb_core.storage as storage
from nba.data import designation_sources as S
from nba.data import espn_injuries as EI
from nba.data import nba_injury_report as R
import scripts.bank_nba_designations as runner

ET = ZoneInfo("America/New_York")
X = dict(date=24, time=120, matchup=201, team=265, player=426, status=587, reason=667)


def w(col, y, text, dx=0.0):
    return (X[col] + dx, y - 6, X[col] + dx + 30, y + 6, text)


def header(y=111):
    return [(23.1, y - 6, 50, y + 6, "Game"), (53, y - 6, 75, y + 6, "Date"), (119.6, y - 6, 146, y + 6, "Game"),
            (149, y - 6, 172, y + 6, "Time"), (200, y - 6, 241, y + 6, "Matchup"), (264.2, y - 6, 290, y + 6, "Team"),
            (425, y - 6, 454, y + 6, "Player"), (457, y - 6, 484, y + 6, "Name"), (585.7, y - 6, 621, y + 6, "Current"),
            (624, y - 6, 653, y + 6, "Status"), (666, y - 6, 700, y + 6, "Reason")]


def title(hhmm="01:30", ap="PM", day="04/02/26"):
    return [(289, 38, 349, 67, "Injury"), (355, 38, 432, 67, "Report:"), (438, 38, 533, 67, day),
            (539, 38, 596, 67, hhmm), (602, 38, 637, 67, ap)]


def small_report(hhmm="01:30", ap="PM"):
    """One page, two players + one NOT YET SUBMITTED team. Player 1's reason WRAPS above and below his line."""
    ws = title(hhmm, ap) + header()
    ws += [w("date", 133, "04/02/2026"), w("time", 133, "07:00"), w("time", 133, "(ET)", 25),
           w("matchup", 133, "ATL@ORL"), w("team", 133, "Atlanta"), w("team", 133, "Hawks", 32)]
    ws += [w("player", 133, "Young,"), w("player", 133, "Trae", 30), w("status", 133, "Out"),
           w("reason", 126, "Injury/Illness"), w("reason", 126, "-", 54), w("reason", 126, "Right", 60),
           w("reason", 140, "Achilles;"), w("reason", 140, "Tendinitis", 46)]
    ws += [w("team", 220, "Orlando"), w("team", 220, "Magic", 36),
           w("player", 220, "Wagner,"), w("player", 220, "Franz", 32), w("status", 220, "Probable"),
           w("reason", 220, "Rest")]
    ws += [w("team", 280, "Miami"), w("team", 280, "Heat", 30),
           w("player", 280, "NOT"), w("player", 280, "YET", 24), w("player", 280, "SUBMITTED", 44)]
    return [ws]


# ----------------------------------------------------------------------------------------------- (a) parse_words
def test_parse_words_two_players_wrapped_reason_and_not_submitted():
    df = R.parse_words(small_report())
    assert list(df.columns) == R.COLUMNS and len(df) == 3
    assert (df["report_ts_et"] == "2026-04-02T13:30:00-04:00").all()
    young = df[df["player"] == "Trae Young"].iloc[0]
    assert young["status"] == "Out" and young["team"] == "Atlanta Hawks" and young["team_code"] == "ATL"
    assert young["reason"] == "Injury/Illness - Right Achilles; Tendinitis"      # wrapped fragments, nearest line
    assert young["reason_cat"] == "injury_illness"
    assert (young["game_date"], young["game_time_et"], young["matchup"]) == ("2026-04-02", "07:00 (ET)", "ATL@ORL")
    wagner = df[df["player"] == "Franz Wagner"].iloc[0]
    assert (wagner["status"], wagner["reason"], wagner["team_code"]) == ("Probable", "Rest", "ORL")
    nys = df[df["status"] == R.NOT_SUBMITTED].iloc[0]
    assert nys["team"] == "Miami Heat" and nys["team_code"] == "MIA" and pd.isna(nys["player"])


# ----------------------------------------------------------------------------------------------- (b) versioning
def test_bank_key_versions_are_distinct_and_report_ts_is_preserved(tmp_path, monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    ts13 = datetime(2026, 4, 2, 13, 0, tzinfo=ET)
    ts17 = datetime(2026, 4, 2, 17, 30, tzinfo=ET)
    assert R.bank_key(ts13) == "NBA/injuries/official/2026-04-02/report_1300.csv"
    assert R.bank_key(ts17) == "NBA/injuries/official/2026-04-02/report_1730.csv"
    assert R.bank_key(ts13) != R.bank_key(ts17)
    early = R.parse_words(small_report("01:30", "PM"))
    late = R.parse_words(small_report("05:30", "PM"))
    assert early["report_ts_et"].iloc[0] == "2026-04-02T13:30:00-04:00"
    assert late["report_ts_et"].iloc[0] == "2026-04-02T17:30:00-04:00"
    k1 = R.bank_report(early, ts13)
    k2 = R.bank_report(late, ts17)
    assert k1 != k2 and (tmp_path / k1).exists() and (tmp_path / k2).exists()   # both versions kept, never collapsed
    assert pd.read_csv(tmp_path / k1)["report_ts_et"].iloc[0] == "2026-04-02T13:30:00-04:00"
    assert pd.read_csv(tmp_path / k2)["report_ts_et"].iloc[0] == "2026-04-02T17:30:00-04:00"


# ----------------------------------------------------------------------------------------------- runner tripwires
@pytest.fixture
def tripwires(monkeypatch):
    calls = {"backfill": [], "fetch": [], "writes": []}

    def fake_backfill(days, hours, delay=0.25, **kw):
        calls["backfill"].append((list(days), tuple(hours)))
        return {"banked": 0, "skipped_present": 0, "not_published": 0, "failed": []}

    monkeypatch.setattr(R, "backfill", fake_backfill)
    monkeypatch.setattr(R, "fetch_report", lambda *a, **k: calls["fetch"].append(a))
    monkeypatch.setattr(storage, "write_bytes", lambda data, key: calls["writes"].append(key))
    return calls


# ----------------------------------------------------------------------------------------------- (c) disabled by default
@pytest.mark.parametrize("argv,env", [
    (["--days", "2026-04-01"], {}),                                            # neither gate
    (["--days", "2026-04-01", "--execute"], {}),                               # flag only, env missing
    (["--days", "2026-04-01", "--execute"], {"MINUTES_RUN_ENABLED": "0"}),     # flag only, env wrong value
    (["--days", "2026-04-01"], {"MINUTES_RUN_ENABLED": "1"}),                  # env only, flag missing
])
def test_runner_without_both_gates_does_nothing(tripwires, argv, env):
    assert runner.main(argv, environ=env) == 0
    assert tripwires == {"backfill": [], "fetch": [], "writes": []}


def test_runner_with_both_gates_calls_backfill_once(tripwires):
    rc = runner.main(["--days", "2026-04-01,2026-04-02", "--execute"], environ={"MINUTES_RUN_ENABLED": "1"})
    assert rc == 0
    assert tripwires["backfill"] == [(["2026-04-01", "2026-04-02"], (13, 17))]  # default stamp hours
    assert tripwires["fetch"] == [] and tripwires["writes"] == []               # backfill itself was the fake


# ----------------------------------------------------------------------------------------------- (d) dry-run plan
def test_dry_run_plan_counts_for_a_fixed_day_set(tripwires, capsys):
    assert runner.main(["--start", "2026-04-01", "--end", "2026-04-03"], environ={}) == 0
    out = capsys.readouterr().out
    assert "3 days x 2 stamps = 6 reports" in out
    assert "target prefix: NBA/injuries/official" in out and "PLAN ONLY" in out
    assert tripwires == {"backfill": [], "fetch": [], "writes": []}


def test_dry_run_plan_all_hours_and_explicit_hours(capsys):
    runner.main(["--days", "2026-04-01,2026-04-02", "--all-hours"], environ={})
    assert "2 days x 24 stamps = 48 reports" in capsys.readouterr().out
    runner.main(["--days", "2026-04-01", "--hours", "13", "17", "20"], environ={})
    assert "1 days x 3 stamps = 3 reports" in capsys.readouterr().out


def test_plan_arithmetic_is_pure():
    plan = runner.build_plan(["2026-04-01", "2026-04-02"], (13, 17))
    assert plan["n_reports"] == 4 and plan["target_prefix"] == "NBA/injuries/official"
    assert runner.execution_allowed(False, {"MINUTES_RUN_ENABLED": "1"})[0] is False
    assert runner.execution_allowed(True, {})[0] is False
    assert runner.execution_allowed(True, {"MINUTES_RUN_ENABLED": "1"})[0] is True


# ----------------------------------------------------------------------------------------------- (e) source adapters
def test_sportsdataverse_adapter_is_an_honest_stub():
    assert "report_datetime" in S.SDV_DOCUMENTED_COLUMNS and "current_status" in S.SDV_DOCUMENTED_COLUMNS
    with pytest.raises(NotImplementedError):
        S.sportsdataverse_to_designations(pd.DataFrame())
    with pytest.raises(NotImplementedError):
        S.fetch_sportsdataverse([2026])


def test_runner_sportsdataverse_plans_but_execute_fails_closed(tripwires, capsys):
    assert runner.main(["--days", "2026-04-01", "--source", "sportsdataverse"], environ={}) == 0
    assert "source: sportsdataverse" in capsys.readouterr().out
    rc = runner.main(["--days", "2026-04-01", "--source", "sportsdataverse", "--execute"],
                     environ={"MINUTES_RUN_ENABLED": "1"})
    assert rc == 1 and tripwires["backfill"] == []                              # never falls back to the PDF path


def _espn_snapshot(extra_rows=()):
    rows = [["ATL", 101, "Trae Young", "Out", "Right Achilles; Tendinitis", "2026-10-16T11:00Z", "Out"],
            ["ORL", 202, "Franz Wagner", "Day-To-Day", "Left Ankle", "2026-10-16T11:05Z", "Day-To-Day"]]
    df = pd.DataFrame(rows + list(extra_rows), columns=EI.COLUMNS)
    df["fetched_at_utc"] = "2026-10-16T15:30:00+00:00"
    return df


def test_espn_snapshot_aligns_to_designation_columns():
    out = S.espn_snapshot_to_designations(_espn_snapshot())
    assert list(out.columns) == R.COLUMNS + ["espn_id"] and len(out) == 2
    young = out.iloc[0]
    assert young["report_ts_et"] == "2026-10-16T11:30:00-04:00"                 # poll time, in ET
    assert (young["game_date"], young["team"], young["team_code"]) == ("2026-10-16", "Atlanta Hawks", "ATL")
    assert (young["player"], young["player_raw"], young["status"]) == ("Trae Young", "Trae Young", "Out")
    assert young["reason"] == "Right Achilles; Tendinitis" and young["espn_id"] == 101
    assert pd.isna(young["matchup"]) and pd.isna(young["game_time_et"])         # ESPN does not know: None, not invented
    assert out.iloc[1]["status"] == "Day-To-Day"                                # ESPN-only status passes through
    assert out.attrs["n_dropped_unknown_team"] == 0


def test_espn_snapshot_drops_unknown_teams_and_requires_a_fetch_time():
    out = S.espn_snapshot_to_designations(_espn_snapshot(
        extra_rows=[["XXX", 303, "Ghost Player", "Out", "Knee", "2026-10-16T11:00Z", "Out"]]))
    assert len(out) == 2 and out.attrs["n_dropped_unknown_team"] == 1
    with pytest.raises(ValueError):
        S.espn_snapshot_to_designations(_espn_snapshot().drop(columns=["fetched_at_utc"]))
    empty = S.espn_snapshot_to_designations(_espn_snapshot().iloc[0:0])
    assert len(empty) == 0 and list(empty.columns) == R.COLUMNS + ["espn_id"]
