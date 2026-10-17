"""nba.data.nba_injury_report: official NBA injury report PDF parser/fetcher. The parser is tested on synthetic word
geometry (no poppler needed) and on one real report (skipped when `pdftotext` is absent). Offline: no network."""
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from nba.data import nba_injury_report as R

FIX = Path(__file__).parent / "fixtures" / "nba_injury_report_2026-04-01_0500PM.pdf"
ET = ZoneInfo("America/New_York")
X = dict(date=24, time=120, matchup=201, team=265, player=426, status=587, reason=667)


def w(col, y, text, dx=0.0):
    return (X[col] + dx, y - 6, X[col] + dx + 30, y + 6, text)


def header(y=111):
    return [(23.1, y - 6, 50, y + 6, "Game"), (53, y - 6, 75, y + 6, "Date"), (119.6, y - 6, 146, y + 6, "Game"),
            (149, y - 6, 172, y + 6, "Time"), (200, y - 6, 241, y + 6, "Matchup"), (264.2, y - 6, 290, y + 6, "Team"),
            (425, y - 6, 454, y + 6, "Player"), (457, y - 6, 484, y + 6, "Name"), (585.7, y - 6, 621, y + 6, "Current"),
            (624, y - 6, 653, y + 6, "Status"), (666, y - 6, 700, y + 6, "Reason")]


TITLE = [(289, 38, 349, 67, "Injury"), (355, 38, 432, 67, "Report:"), (438, 38, 533, 67, "04/01/26"),
         (539, 38, 596, 67, "05:00"), (602, 38, 637, 67, "PM")]


def page1():
    ws = TITLE + header()
    ws += [w("date", 133, "04/01/2026"), w("time", 133, "07:00"), w("time", 133, "(ET)", 25), w("matchup", 133, "ATL@ORL"),
           w("team", 133, "Atlanta"), w("team", 133, "Hawks", 32)]
    ws += [w("player", 133, "Dennis,"), w("player", 133, "RayJ", 32), w("status", 133, "Questionable"),
           w("reason", 133, "G"), w("reason", 133, "League", 8), w("reason", 133, "-", 40), w("reason", 133, "Two-Way", 46)]
    # Orlando: reason WRAPS above and below the player line (Black, Anthony)
    ws += [w("team", 207, "Orlando"), w("team", 207, "Magic", 36)]
    ws += [w("player", 207, "Black,"), w("player", 207, "Anthony", 35), w("status", 207, "Out"),
           w("reason", 200, "Injury/Illness"), w("reason", 200, "-", 54), w("reason", 200, "Left", 60),
           w("reason", 214, "Abdominal;"), w("reason", 214, "Strain", 46)]
    ws += [w("player", 251, "Wagner,"), w("player", 251, "Franz", 32), w("status", 251, "Available"),
           w("reason", 251, "Injury/Illness"), w("reason", 251, "-", 54), w("reason", 251, "Left", 60)]
    ws += [(380, 570, 460, 585, "Page"), (463, 570, 470, 585, "1"), (472, 570, 482, 585, "of"), (485, 570, 490, 585, "2")]
    return ws


def page2():
    # a group that CONTINUES from page 1 (no team/date/matchup on this page until the next game), then a new game with a
    # time but no date, then a team that has not submitted
    ws = list(TITLE)
    ws += [w("player", 80, "Isaac,"), w("player", 80, "Jonathan", 30), w("status", 80, "Out"),
           w("reason", 80, "Injury/Illness"), w("reason", 80, "-", 54), w("reason", 80, "Knee", 60)]
    ws += [w("time", 140, "07:30"), w("time", 140, "(ET)", 25), w("matchup", 140, "BOS@MIA"), w("team", 140, "Boston"),
           w("team", 140, "Celtics", 36)]
    ws += [w("player", 140, "Harper"), w("player", 140, "Jr.,", 33), w("player", 140, "Ron", 52), w("status", 140, "Probable"),
           w("reason", 140, "Injury/Illness"), w("reason", 140, "-", 54), w("reason", 140, "Ankle", 60)]
    ws += [w("team", 200, "Miami"), w("team", 200, "Heat", 30), w("player", 200, "NOT"), w("player", 200, "YET", 24),
           w("player", 200, "SUBMITTED", 44)]
    return ws


@pytest.fixture(scope="module")
def df():
    return R.parse_words([page1(), page2()])


def test_columns_rows_and_title_timestamp(df):
    assert list(df.columns) == R.COLUMNS and len(df) == 6
    assert (df["report_ts_et"] == "2026-04-01T17:00:00-04:00").all()


def test_names_are_flipped_with_suffix_kept(df):
    assert df["player"].tolist()[:5] == ["RayJ Dennis", "Anthony Black", "Franz Wagner", "Jonathan Isaac", "Ron Harper Jr."]
    assert df.loc[df["player_raw"] == "Harper Jr., Ron", "player"].iloc[0] == "Ron Harper Jr."


def test_wrapped_reason_above_and_below_goes_to_the_nearest_player(df):
    black = df[df["player"] == "Anthony Black"].iloc[0]
    assert black["reason"] == "Injury/Illness - Left Abdominal; Strain" and black["reason_cat"] == "injury_illness"
    assert df.loc[df["player"] == "RayJ Dennis", "reason"].iloc[0] == "G League - Two-Way"
    assert df.loc[df["player"] == "RayJ Dennis", "reason_cat"].iloc[0] == "g_league"
    assert df.loc[df["player"] == "Franz Wagner", "status"].iloc[0] == "Available"


def test_group_columns_carry_down_and_across_pages(df):
    assert df.loc[df["player"] == "Anthony Black", "team"].iloc[0] == "Orlando Magic"
    isaac = df[df["player"] == "Jonathan Isaac"].iloc[0]               # page 2, continues Orlando in ATL@ORL
    assert (isaac["team"], isaac["matchup"], isaac["game_date"], isaac["team_code"]) == \
        ("Orlando Magic", "ATL@ORL", "2026-04-01", "ORL")
    harper = df[df["player"] == "Ron Harper Jr."].iloc[0]              # new game: own time/matchup, date carried from above
    assert (harper["team"], harper["matchup"], harper["game_time_et"], harper["game_date"]) == \
        ("Boston Celtics", "BOS@MIA", "07:30 (ET)", "2026-04-01")


def test_not_yet_submitted_is_a_team_flag_not_a_player(df):
    row = df[df["status"] == R.NOT_SUBMITTED].iloc[0]
    assert row["team"] == "Miami Heat" and row["team_code"] == "MIA" and pd.isna(row["player"])


def test_unknown_status_rows_are_dropped_and_counted():
    pg = page1() + [w("player", 300, "Mystery,"), w("player", 300, "Man", 35), w("status", 300, "Maybe")]
    out = R.parse_words([pg])
    assert "Man Mystery" not in set(out["player"].dropna()) and out.attrs["n_bad_status"] == 1


def test_long_player_name_overrunning_into_the_status_column_is_repaired():
    pg = page1() + [w("player", 300, "Gilgeous-Alexander,"), w("status", 300, "Shai"), w("status", 300, "Questionable", 30),
                    w("reason", 300, "Injury/Illness"), w("reason", 300, "-", 54), w("reason", 300, "Knee", 60)]
    out = R.parse_words([pg])
    r = out[out["player_raw"] == "Gilgeous-Alexander, Shai"].iloc[0]
    assert (r["player"], r["status"], r["reason"]) == ("Shai Gilgeous-Alexander", "Questionable", "Injury/Illness - Knee")
    assert out.attrs["n_bad_status"] == 0


def test_orphan_reason_words_without_any_player_row_raise_cleanly():
    with pytest.raises(R.ReportParseError):
        R.parse_words([TITLE + header() + [w("reason", 140, "Injury/Illness"), w("team", 140, "Orphan")]])


def test_a_report_with_no_player_rows_raises():
    with pytest.raises(R.ReportParseError):
        R.parse_words([TITLE + header()])


@pytest.mark.parametrize("reason,cat", [("G League - Two-Way", "g_league"), ("Injury/Illness - Knee", "injury_illness"),
                                        ("Injury Management", "injury_management"), ("Not With Team", "not_with_team"),
                                        ("Rest", "rest"), ("Personal Reasons", "personal"), ("Suspension", "suspension"),
                                        ("Return to Competition Reconditioning", "return_to_competition"), ("", "none"),
                                        ("Something else", "other")])
def test_reason_category(reason, cat):
    assert R.reason_category(reason) == cat


def test_urls_cover_both_filename_conventions():
    hourly = R.report_urls(datetime(2024, 1, 15, 17, 0))
    assert hourly == [f"{R.BASE}/Injury-Report_2024-01-15_05PM.pdf", f"{R.BASE}/Injury-Report_2024-01-15_05_00PM.pdf"]
    quarter = R.report_urls(datetime(2026, 4, 1, 17, 15, tzinfo=ET))
    assert quarter == [f"{R.BASE}/Injury-Report_2026-04-01_05_15PM.pdf"]       # no hourly form off the hour
    assert R.report_urls(datetime(2026, 4, 1, 11, 0))[0].endswith("_11AM.pdf")


class _Resp:
    def __init__(self, code, body=b""):
        self.status_code, self.content = code, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Sess:
    def __init__(self, table):
        self.table, self.calls = table, []

    def get(self, url, **kw):
        self.calls.append(url)
        return self.table.get(url, _Resp(403, b"<Error>AccessDenied</Error>"))


def test_fetch_tries_each_convention_and_treats_403_as_not_published():
    ts = datetime(2026, 1, 15, 17, 0, tzinfo=ET)
    u_hourly, u_quarter = R.report_urls(ts)
    s = _Sess({u_quarter: _Resp(200, b"%PDF-1.4 x")})
    assert R.fetch_report(ts, s) == (u_quarter, b"%PDF-1.4 x") and s.calls == [u_hourly, u_quarter]
    assert R.fetch_report(ts, _Sess({})) is None


def test_fetch_never_mistakes_a_non_pdf_or_server_error_for_not_published():
    ts = datetime(2026, 1, 15, 17, 0, tzinfo=ET)
    u_hourly = R.report_urls(ts)[0]
    with pytest.raises(R.ReportParseError):
        R.fetch_report(ts, _Sess({u_hourly: _Resp(200, b"<html>slow down</html>")}))
    with pytest.raises(RuntimeError):
        R.fetch_report(ts, _Sess({u_hourly: _Resp(500)}))


def test_missing_pdftotext_is_an_explicit_error():
    with pytest.raises(R.PdfToolMissing):
        R.pdf_words(b"%PDF", pdftotext="definitely-not-installed-pdftotext")


def test_resolve_ids_fails_closed_on_unknown_and_ambiguous(df):
    rows = pd.DataFrame({
        "player_id": [1, 2, 3, 4, 5], "name": ["RayJ Dennis", "Anthony Black", "Franz Wagner", "Ron Harper Jr.", "Anthony Black"],
        "team": ["ATL", "ORL", "ORL", "BOS", "CHI"], "tip_sort": pd.to_datetime(["2026-03-01"] * 5), "game_id": range(5)})
    out = R.resolve_ids(df, rows)
    got = dict(zip(out["player"], out["espn_id"]))
    assert got["RayJ Dennis"] == 1 and got["Franz Wagner"] == 3 and got["Ron Harper Jr."] == 4
    assert got["Anthony Black"] == 2                        # two Anthony Blacks: the report's TEAM (ORL) disambiguates
    assert pd.isna(got["Jonathan Isaac"])                  # not in history -> unresolved, never guessed
    assert out.loc[out["status"] == R.NOT_SUBMITTED, "how"].iloc[0] == "team_flag"


def test_status_sets_shape_for_the_live_feature_path(df):
    rows = pd.DataFrame({"player_id": [2, 4], "name": ["Anthony Black", "Ron Harper Jr."], "team": ["ORL", "BOS"],
                         "tip_sort": pd.to_datetime(["2026-03-01"] * 2), "game_id": [1, 2]})
    s = R.status_sets(R.resolve_ids(df, rows))
    assert s["ORL"] == {2} and s["BOS"] == set() and "ATL" in s
    assert R.status_sets(R.resolve_ids(df, rows), statuses=("Out", "Probable"))["BOS"] == {4}


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler pdftotext not installed")
def test_real_report_parses_completely():
    out = R.parse_pdf(FIX.read_bytes())
    assert len(out) == 115 and out.attrs["n_bad_status"] == 0
    assert out["team"].notna().all() and out["matchup"].notna().all() and out["game_date"].notna().all()
    assert out["status"].value_counts().to_dict() == {"Out": 83, "Questionable": 17, "Available": 9, "Probable": 5, "Doubtful": 1}
    assert out["matchup"].nunique() == 9 and out["team"].nunique() == 18
    sizes = out.groupby("team").size()
    assert sizes["Atlanta Hawks"] == 3 and sizes["Orlando Magic"] == 5 and sizes["Washington Wizards"] == 10
    assert out.loc[out["player"] == "Joel Embiid", "status"].iloc[0] == "Out"
    assert out.loc[out["player"] == "Ron Harper Jr.", "reason_cat"].iloc[0] == "injury_illness"


def test_bank_roundtrip(tmp_path, monkeypatch, df):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    key = R.bank_report(df, datetime(2026, 4, 1, 17, 0, tzinfo=ET), b"%PDF-1.4 raw")
    assert key == "NBA/injuries/official/2026-04-01/report_1700.csv"
    assert (tmp_path / key).exists() and (tmp_path / "NBA/injuries/official/2026-04-01/raw_report_1700.pdf").read_bytes() == b"%PDF-1.4 raw"
    assert len(pd.read_csv(tmp_path / key)) == len(df)


# ------------------------------------------------------------------------------------------------ backfill
def test_backfill_is_resumable_records_misses_and_isolates_failures(tmp_path, monkeypatch, df):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    calls = []

    def fetch(ts):
        calls.append(ts.strftime("%Y-%m-%d %H"))
        if ts.day == 2:
            return None                                      # not published
        if ts.day == 3 and ts.hour == 17:
            return ("u", b"%PDF-broken")
        return ("u", b"%PDF-ok")

    def parse(pdf):
        if pdf == b"%PDF-broken":
            raise R.ReportParseError("boom")
        return df.copy()

    r = R.backfill(["2026-04-01", "2026-04-02", "2026-04-03"], (13, 17), delay=0, fetch=fetch, parse=parse)
    assert (r["banked"], r["not_published"]) == (3, 2) and r["failed"][0]["key"].endswith("2026-04-03/report_1700.csv")
    assert "boom" in r["failed"][0]["error"]
    calls.clear()
    r2 = R.backfill(["2026-04-01", "2026-04-02", "2026-04-03"], (13, 17), delay=0, fetch=fetch, parse=parse)
    assert r2["skipped_present"] == 3 and r2["not_published"] == 2          # misses are remembered: only the failure retries
    assert calls == ["2026-04-03 17"]
