"""MIN-07: provenance + publication-grid banking for nba.data.nba_injury_report.

The parse contract (COLUMNS) is pinned by tests/test_nba_injury_report.py and does not move.
What MIN-07 adds, pinned here: every banked row carries where it came from (source_url), what
it came from (source_pdf_sha256 of the raw PDF), and when it was banked (banked_at_utc); the
backfill walks the quarter-hour publication grid (hours x stamp_minutes); and the fetcher sends
the generic Chrome UA that ak-static actually answers (verified 2026-11-13: non-browser UAs
stall at this host -- CONNECT/TLS complete, no HTTP response ever arrives).
Offline: storage is a tmp dir, fetch/parse are fakes except one real-fixture parse.
"""
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from nba.data import nba_injury_report as R

ET = ZoneInfo("America/New_York")
FIX = Path(__file__).parent / "fixtures" / "nba_injury_report_2026-04-01_0500PM.pdf"


@pytest.fixture()
def df():
    return pd.DataFrame(
        [{"report_ts_et": "2026-04-01T17:00:00-04:00", "game_date": "2026-04-01",
          "game_time_et": "07:00 (ET)", "matchup": "ATL@ORL", "team": "Atlanta Hawks",
          "team_code": "ATL", "player_raw": "Dennis, RayJ", "player": "RayJ Dennis",
          "status": "Questionable", "reason": "G League - Two-Way", "reason_cat": "g_league"}],
        columns=R.COLUMNS)


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------------------
# provenance stamping
def test_pdf_sha256_is_the_document_identity():
    assert R.pdf_sha256(b"%PDF-x") == hashlib.sha256(b"%PDF-x").hexdigest()
    assert R.pdf_sha256(b"%PDF-x") != R.pdf_sha256(b"%PDF-y")


def test_with_provenance_appends_without_touching_the_parse(df):
    out = R.with_provenance(df, source_url="https://example.test/r.pdf", pdf=b"%PDF-raw",
                            banked_at=datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc))
    assert list(out.columns) == R.COLUMNS + R.PROVENANCE_COLUMNS
    assert out["source_url"].iloc[0] == "https://example.test/r.pdf"
    assert out["source_pdf_sha256"].iloc[0] == hashlib.sha256(b"%PDF-raw").hexdigest()
    assert out["banked_at_utc"].iloc[0] == "2026-11-13T12:00:00+00:00"
    assert list(df.columns) == R.COLUMNS                      # the input frame is untouched


def test_bank_report_stamps_provenance_when_it_knows_the_source(df):
    ts = datetime(2026, 4, 1, 17, 30, tzinfo=ET)
    key = R.bank_report(df, ts, b"%PDF-raw", source_url="https://example.test/r.pdf")
    assert key == "NBA/injuries/official/2026-04-01/report_1730.csv"
    banked = pd.read_csv(Path(__import__("os").environ["MLB_BASE_DATA"]) / key)
    assert list(banked.columns) == R.COLUMNS + R.PROVENANCE_COLUMNS
    assert banked["source_url"].eq("https://example.test/r.pdf").all()
    assert banked["source_pdf_sha256"].eq(hashlib.sha256(b"%PDF-raw").hexdigest()).all()


def test_bank_report_without_a_source_keeps_the_historical_shape(df):
    key = R.bank_report(df, datetime(2026, 4, 1, 17, 0, tzinfo=ET))
    banked = pd.read_csv(Path(__import__("os").environ["MLB_BASE_DATA"]) / key)
    assert list(banked.columns) == R.COLUMNS


# --------------------------------------------------------------------------------------
# the grid backfill: hours x minutes, provenance on every row, resumable
def test_backfill_walks_the_quarter_hour_grid_and_stamps_provenance(df):
    calls = []

    def fetch(ts):
        calls.append(ts.strftime("%H:%M"))
        return ("https://example.test/x.pdf", b"%PDF-ok")

    r = R.backfill(["2026-04-01"], (13, 17), delay=0, fetch=fetch,
                   parse=lambda pdf: df.copy(), stamp_minutes=(0, 30))
    assert r["banked"] == 4 and not r["failed"]
    assert calls == ["13:00", "13:30", "17:00", "17:30"]
    base = Path(__import__("os").environ["MLB_BASE_DATA"]) / "NBA/injuries/official/2026-04-01"
    assert sorted(p.name for p in base.glob("report_*.csv")) == \
        ["report_1300.csv", "report_1330.csv", "report_1700.csv", "report_1730.csv"]
    banked = pd.read_csv(base / "report_1730.csv")
    assert banked["source_pdf_sha256"].iloc[0] == hashlib.sha256(b"%PDF-ok").hexdigest()
    # resumable: a second pass over the same grid asks for nothing
    calls.clear()
    r2 = R.backfill(["2026-04-01"], (13, 17), delay=0, fetch=fetch,
                    parse=lambda pdf: df.copy(), stamp_minutes=(0, 30))
    assert r2["skipped_present"] == 4 and calls == []


def test_publication_grid_constants_match_the_probed_bank():
    assert R.GRID_STAMP_HOURS == tuple(range(11, 24))
    assert R.GRID_STAMP_MINUTES == (0, 15, 30, 45)
    # every grid stamp has a well-formed bank key
    for h in (11, 23):
        for m in R.GRID_STAMP_MINUTES:
            assert R.bank_key(datetime(2026, 11, 12, h, m, tzinfo=ET)).endswith(
                f"report_{h:02d}{m:02d}.csv")


# --------------------------------------------------------------------------------------
# the fetcher's UA is the one the host answers
class _Resp:
    def __init__(self, status_code, content=b""):
        self.status_code, self.content = status_code, content

    def raise_for_status(self):
        pass


def test_fetch_report_sends_a_browser_user_agent():
    seen = {}

    class FakeSession:
        def get(self, url, timeout=None, headers=None):
            seen.update(headers or {})
            return _Resp(200, b"%PDF-real")

    url, pdf = R.fetch_report(datetime(2026, 11, 12, 17, 30, tzinfo=ET), session=FakeSession())
    assert pdf == b"%PDF-real" and url.endswith("Injury-Report_2026-11-12_05_30PM.pdf")
    assert "Chrome" in seen["User-Agent"] and "beezy" not in seen["User-Agent"]


@pytest.mark.skipif(not FIX.exists(), reason="fixture PDF missing")
def test_real_fixture_still_parses_to_the_pinned_columns():
    out = R.parse_pdf(FIX.read_bytes())
    # 115 player rows + 12 reason-column NOT YET SUBMITTED team flags (see MIN-07 flag tests)
    assert list(out.columns) == R.COLUMNS and len(out) == 127


# --------------------------------------------------------------------------------------
# the 2026-27 layout era: NOT YET SUBMITTED lives in the REASON column (synthetic word
# geometry replicating the real 2026-10-19 11:00 AM report, whose six teams were all
# unsubmitted and which the old parser rejected as "no player rows")
_X = dict(date=24, time=120, matchup=201, team=265, player=426, status=587, reason=667)


def _w(col, y, text, dx=0.0):
    return (_X[col] + dx, y - 6, _X[col] + dx + 30, y + 6, text)


def _header(y=111):
    return [(23.1, y - 6, 50, y + 6, "Game"), (53, y - 6, 75, y + 6, "Date"),
            (119.6, y - 6, 146, y + 6, "Game"), (149, y - 6, 172, y + 6, "Time"),
            (200, y - 6, 241, y + 6, "Matchup"), (264.2, y - 6, 290, y + 6, "Team"),
            (425, y - 6, 454, y + 6, "Player"), (457, y - 6, 484, y + 6, "Name"),
            (585.7, y - 6, 621, y + 6, "Current"), (624, y - 6, 653, y + 6, "Status"),
            (666, y - 6, 700, y + 6, "Reason")]


_TITLE26 = [(289, 38, 349, 67, "Injury"), (355, 38, 432, 67, "Report:"),
            (438, 38, 533, 67, "10/19/26"), (539, 38, 596, 67, "11:00"), (602, 38, 637, 67, "AM")]


def _flag_words(y, team_words):
    ws = [_w("team", y, t, 32 * i) for i, t in enumerate(team_words)]
    ws += [_w("reason", y, "NOT"), _w("reason", y, "YET", 30), _w("reason", y, "SUBMITTED", 55)]
    return ws


def test_all_flag_report_is_a_valid_version_not_a_parse_failure():
    ws = list(_TITLE26) + _header()
    ws += [_w("date", 133, "10/20/2026"), _w("time", 133, "03:00"), _w("time", 133, "(ET)", 25),
           _w("matchup", 133, "BOS@DET")]
    ws += _flag_words(133, ["Boston", "Celtics"])
    ws += _flag_words(155, ["Detroit", "Pistons"])
    out = R.parse_words([ws])
    assert len(out) == 2
    assert out["status"].eq(R.NOT_SUBMITTED).all() and out["player"].isna().all()
    assert out["team"].tolist() == ["Boston Celtics", "Detroit Pistons"]
    assert out["game_date"].eq("2026-10-20").all()
    assert out["report_ts_et"].iloc[0] == "2026-10-19T11:00:00-04:00"


def test_flag_reason_never_leaks_onto_a_players_reason_in_a_mixed_report():
    ws = list(_TITLE26) + _header()
    ws += [_w("date", 133, "10/20/2026"), _w("time", 133, "07:00"), _w("time", 133, "(ET)", 25),
           _w("matchup", 133, "PHI@NYK"), _w("team", 133, "Philadelphia"), _w("team", 133, "76ers", 62)]
    ws += [_w("player", 133, "Embiid,"), _w("player", 133, "Joel", 40),
           _w("status", 133, "Out"), _w("reason", 133, "Injury/Illness"),
           _w("reason", 133, "-", 54), _w("reason", 133, "Knee", 60)]
    ws += _flag_words(155, ["New", "York", "Knicks"])
    out = R.parse_words([ws])
    assert len(out) == 2
    embiid = out[out["player"] == "Joel Embiid"].iloc[0]
    assert embiid["status"] == "Out" and "NOT YET" not in embiid["reason"]
    flag = out[out["status"] == R.NOT_SUBMITTED].iloc[0]
    assert flag["team"] == "New York Knicks" and flag["matchup"] == "PHI@NYK"
