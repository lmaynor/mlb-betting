"""MIN-08: nba.data.official_report_capture -- the as-published T-60 decision-time feed.

Pinned here: capture banks every DUE, published, not-yet-banked version of a day's official
report (parsed + raw + provenance) and is idempotent; a 403 is "not yet published", never
recorded missing (capture never touches _missing.json); the per-day capture ledger is the
feed's provenance; a captured day VERIFIES (banked rows == re-parse of the banked raw PDFs);
and the banked frame feeds cases.select_designation, so a tip-60 designation comes from
banked data alone. The report itself is the repo's real fixture PDF (2026-04-01 5:00 PM,
127 rows: 115 player + 12 team-flag); fetch is faked, storage is a tmp local store. Offline.
"""
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from nba.data import nba_injury_report as R
from nba.data import official_report_capture as C
from nba.minutes_restriction import cases

ET = ZoneInfo("America/New_York")
FIX = Path(__file__).parent / "fixtures" / "nba_injury_report_2026-04-01_0500PM.pdf"
DAY = date(2026, 4, 1)
NOW = datetime(2026, 4, 1, 18, 0, tzinfo=ET)          # due: grid stamps 11:00..18:00 = 29


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    return tmp_path


def fetch_only_1700(ts):
    """The fixture report exists (in this fake world) only at the 17:00 stamp."""
    if (ts.hour, ts.minute) == (17, 0):
        return ("https://ak-static.test/Injury-Report_2026-04-01_05PM.pdf", FIX.read_bytes())
    return None


# --------------------------------------------------------------------------------------
# the grid
def test_stamp_grid_is_the_quarter_hour_publication_window():
    grid = C.stamp_grid(DAY)
    assert len(grid) == 52 and grid[0].strftime("%H%M") == "1100" and grid[-1].strftime("%H%M") == "2345"


def test_due_stamps_respect_now_and_day():
    assert len(C.due_stamps(DAY, NOW)) == 29
    assert C.due_stamps(DAY, datetime(2026, 4, 1, 10, 59, tzinfo=ET)) == []
    assert len(C.due_stamps(DAY, datetime(2026, 4, 2, 9, 0, tzinfo=ET))) == 52   # a past day: all due
    assert C.due_stamps(date(2026, 4, 3), NOW) == []                              # a future day: none


# --------------------------------------------------------------------------------------
# capture
def test_capture_banks_the_published_version_with_provenance(local_store):
    res = C.capture_day(DAY, now=NOW, fetch=fetch_only_1700)
    assert res["captured"] == ["1700"] and res["not_yet_published"] == 28 and not res["failed"]
    base = local_store / "NBA/injuries/official/2026-04-01"
    banked = pd.read_csv(base / "report_1700.csv")
    assert len(banked) == 127
    assert banked["source_url"].str.contains("ak-static.test").all()
    assert banked["source_pdf_sha256"].eq(R.pdf_sha256(FIX.read_bytes())).all()
    assert (base / "raw_report_1700.pdf").read_bytes() == FIX.read_bytes()
    ledger = json.loads((local_store / C.ledger_key(DAY)).read_text())
    assert ledger["runs"] == 1 and ledger["captured"]["1700"]["rows"] == 127
    # "not yet published" is not "missing": capture never writes the backfill's record
    assert not (local_store / "NBA/injuries/official/_missing.json").exists()


def test_capture_is_idempotent(local_store):
    C.capture_day(DAY, now=NOW, fetch=fetch_only_1700)
    res = C.capture_day(DAY, now=NOW, fetch=fetch_only_1700)
    assert res["captured"] == [] and res["already_banked"] == 1 and res["not_yet_published"] == 28
    ledger = json.loads((local_store / C.ledger_key(DAY)).read_text())
    assert ledger["runs"] == 2


def test_capture_isolates_a_failing_stamp(local_store):
    def fetch(ts):
        if (ts.hour, ts.minute) == (17, 0):
            raise RuntimeError("boom")
        return fetch_only_1700(ts) if False else None

    res = C.capture_day(DAY, now=NOW, fetch=fetch)
    assert res["failed"] and res["failed"][0]["stamp"] == "1700"
    # and it is retried, not skipped, on the next pass
    res2 = C.capture_day(DAY, now=NOW, fetch=fetch_only_1700)
    assert res2["captured"] == ["1700"]


# --------------------------------------------------------------------------------------
# reproducibility: verify + the select_designation chain
def test_captured_day_verifies_against_its_raw_pdfs(local_store):
    C.capture_day(DAY, now=NOW, fetch=fetch_only_1700)
    v = C.verify_day(DAY)
    assert v["equal"] and v["versions"] == 1 and v["n_banked"] == v["n_reparsed"] == 127


def test_verify_detects_a_tampered_banked_row(local_store):
    C.capture_day(DAY, now=NOW, fetch=fetch_only_1700)
    path = local_store / "NBA/injuries/official/2026-04-01/report_1700.csv"
    df = pd.read_csv(path)
    df.loc[df["player"] == "Joel Embiid", "status"] = "Available"
    df.to_csv(path, index=False)
    v = C.verify_day(DAY)
    assert not v["equal"] and v["only_in_banked"] and v["only_in_banked"][0]["player"] == "Joel Embiid"


def test_banked_frame_feeds_select_designation(local_store):
    C.capture_day(DAY, now=NOW, fetch=fetch_only_1700)
    frame = C.load_banked_reports([DAY])
    assert len(frame) == 127
    norm = cases._reports_normalized(frame)
    embiid_team = frame.loc[frame["player"] == "Joel Embiid", "team_code"].iloc[0]
    found = cases.select_designation(
        norm, player="Joel Embiid", team=embiid_team, game_date=DAY,
        tip_ts=datetime(2026, 4, 1, 19, 30, tzinfo=ET))
    assert found["designation"] == "Out"
    assert found["decision_report_ts"] == pd.Timestamp("2026-04-01 17:00", tz=ET)


# --------------------------------------------------------------------------------------
# the entrypoint's gates (same convention as bank_nba_designations)
def test_entrypoint_without_both_gates_does_nothing(monkeypatch, capsys):
    from scripts import capture_official_reports as runner
    calls = []
    monkeypatch.setattr(C, "capture_day", lambda *a, **k: calls.append(a))
    assert runner.main(["--date", "2026-04-01"], environ={}) == 0
    assert runner.main(["--date", "2026-04-01", "--execute"], environ={}) == 0
    assert runner.main(["--date", "2026-04-01"], environ={"MINUTES_RUN_ENABLED": "1"}) == 0
    assert calls == [] and "PLAN ONLY" in capsys.readouterr().out
