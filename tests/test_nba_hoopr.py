"""Tests for nba.data.hoopr (sportsdataverse ESPN NBA parquet mirror). Offline: parquet bytes are built in-process and a
fake client stands in for GitHub, so nothing touches the network or a bucket."""
import io
import json
from datetime import date

import pandas as pd
import pytest

from nba.data import hoopr


def parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


def player_box_df(n=3, season=2026):
    return pd.DataFrame({
        "game_id": 1, "season": season, "season_type": 2, "game_date": "2026-01-02",
        "game_date_time": pd.Timestamp("2026-01-02 19:30", tz="America/New_York"),
        "athlete_id": [float(1000 + i) for i in range(n)], "team_id": 1, "team_abbreviation": "NY",
        "minutes": 30.0, "points": 10.0, "rebounds": 4.0, "assists": 2.0, "starter": True,
        "did_not_play": False, "reason": None, "home_away": "home", "opponent_team_id": 2})


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.gets = []

    def get_bytes(self, url):
        self.gets.append(url)
        r = self.responses.get(url)
        if isinstance(r, Exception):
            raise r
        return r

    def head_size(self, url):
        r = self.responses.get(url)
        return len(r) if isinstance(r, (bytes, bytearray)) else None


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    return tmp_path


TODAY = date(2026, 3, 1)            # mid-season: current_season() == 2026


def url_for(name, season):
    return hoopr.DATASETS[name].url(season)


def test_urls_match_hoopr_loader_layout():
    assert url_for("player_boxscores", 2026).endswith(
        "/releases/download/espn_nba_player_boxscores/player_box_2026.parquet")
    assert url_for("team_boxscores", 2025).endswith("/espn_nba_team_boxscores/team_box_2025.parquet")
    assert url_for("schedules", 2027).endswith("/espn_nba_schedules/nba_schedule_2027.parquet")
    assert url_for("pbp", 2024).endswith("/espn_nba_pbp/play_by_play_2024.parquet")
    assert hoopr.DATASETS["player_boxscores"].key(2026) == "NBA/hoopr/player_boxscores/player_box_2026.parquet"


def test_current_season_is_the_year_the_season_ends():
    assert hoopr.current_season(date(2026, 6, 13)) == 2026       # Finals
    assert hoopr.current_season(date(2026, 7, 31)) == 2026
    assert hoopr.current_season(date(2026, 8, 1)) == 2027
    assert hoopr.current_season(date(2026, 10, 16)) == 2027      # 2026-27 preseason is hoopR season 2027
    assert hoopr.current_season(date(2027, 1, 5)) == 2027


def test_default_groups_exclude_pbp():
    names = [d.name for d in hoopr.select_datasets()]
    assert names == ["player_boxscores", "team_boxscores", "schedules"]
    assert "pbp" in [d.name for d in hoopr.select_datasets(groups=("core", "extra"))]
    with pytest.raises(ValueError):
        hoopr.select_datasets(["nope"])
    with pytest.raises(ValueError):
        hoopr.select_datasets(groups=("bogus",))


def test_refresh_plan_is_current_season_only_in_season():
    items = hoopr.plan(hoopr.select_datasets(["player_boxscores"]), "refresh", today=TODAY)
    assert [(i.season, i.latest) for i in items] == [(2026, True)]


def test_refresh_plan_also_takes_last_season_in_the_offseason_turnover():
    items = hoopr.plan(hoopr.select_datasets(["player_boxscores"]), "refresh", today=date(2026, 10, 16))
    assert [(i.season, i.latest) for i in items] == [(2027, True), (2026, True)]


def test_backfill_plan_spans_floor_to_current_and_only_newest_is_latest():
    items = hoopr.plan(hoopr.select_datasets(["schedules"]), "backfill", today=TODAY)
    assert [i.season for i in items] == list(range(2026, 2018, -1))
    assert [i.latest for i in items] == [True] + [False] * 7
    clipped = hoopr.plan(hoopr.select_datasets(["schedules"]), "backfill", seasons=[2000, 2001, 2002, 2003],
                         today=TODAY)
    assert sorted(i.season for i in clipped) == [2002, 2003]      # clipped to the dataset floor
    with pytest.raises(ValueError):
        hoopr.plan([], "sideways")


def test_validate_accepts_good_parquet_and_rejects_the_rest():
    ds = hoopr.DATASETS["player_boxscores"]
    assert hoopr.validate(parquet_bytes(player_box_df(3)), ds) == {"rows": 3, "columns": len(player_box_df().columns)}
    with pytest.raises(hoopr.InvalidAsset, match="not a parquet"):
        hoopr.validate(b"<html>rate limited</html>", ds)
    with pytest.raises(hoopr.InvalidAsset, match="missing required"):
        hoopr.validate(parquet_bytes(player_box_df().drop(columns=["did_not_play"])), ds)


def run_one(responses, **kw):
    client = FakeClient(responses)
    s = hoopr.run("refresh", ["player_boxscores"], client=client, today=TODAY, **kw)
    return s, client


def test_first_run_writes_file_manifest_and_sentinel(local_store):
    data = parquet_bytes(player_box_df(3))
    s, _ = run_one({url_for("player_boxscores", 2026): data})
    assert s["status"] == "ok" and s["written"] == ["player_boxscores/2026"]
    assert (local_store / "NBA/hoopr/player_boxscores/player_box_2026.parquet").read_bytes() == data   # byte-for-byte
    man = json.loads((local_store / "NBA/hoopr/_manifest.json").read_text())
    assert man["NBA/hoopr/player_boxscores/player_box_2026.parquet"]["rows"] == 3
    assert json.loads((local_store / "NBA/hoopr/last_refresh.json").read_text())["status"] == "ok"


def test_identical_second_run_is_unchanged_and_changed_run_updates():
    data = parquet_bytes(player_box_df(3))
    run_one({url_for("player_boxscores", 2026): data})
    s, _ = run_one({url_for("player_boxscores", 2026): data})
    assert s["counts"] == {"unchanged": 1} and s["written"] == []
    s, _ = run_one({url_for("player_boxscores", 2026): parquet_bytes(player_box_df(4))})
    assert s["counts"] == {"updated": 1}


def test_shrunk_file_is_refused_and_old_copy_kept(local_store):
    good = parquet_bytes(player_box_df(10))
    run_one({url_for("player_boxscores", 2026): good})
    s, _ = run_one({url_for("player_boxscores", 2026): parquet_bytes(player_box_df(2))})
    assert s["status"] == "partial" or s["status"] == "error"
    assert s["counts"] == {"refused_shrink": 1}
    assert (local_store / "NBA/hoopr/player_boxscores/player_box_2026.parquet").read_bytes() == good
    s, _ = run_one({url_for("player_boxscores", 2026): parquet_bytes(player_box_df(2))}, force=True)
    assert s["counts"] == {"updated": 1}


def test_invalid_download_is_a_failure_and_never_written(local_store):
    s, _ = run_one({url_for("player_boxscores", 2026): b"not parquet at all, rate limited"})
    assert s["status"] == "error" and s["counts"] == {"invalid": 1}
    assert not (local_store / "NBA/hoopr/player_boxscores/player_box_2026.parquet").exists()


def test_404_on_the_newest_season_is_expected_not_a_failure():
    s, _ = run_one({})                                  # nothing published
    assert s["counts"] == {"missing": 1}
    assert s["failures"] == []                          # latest season may legitimately 404 (new season, no games yet)
    assert s["status"] == "ok"


def test_404_on_a_frozen_season_is_a_failure():
    client = FakeClient({})
    s = hoopr.run("backfill", ["schedules"], seasons=[2025], client=client, today=TODAY)
    assert s["counts"] == {"missing": 1} and s["status"] == "error"


def test_frozen_season_already_mirrored_makes_no_request(local_store):
    data = parquet_bytes(pd.DataFrame({"game_id": [1], "season": [2025], "season_type": [2],
                                       "game_date_time": [pd.Timestamp("2025-01-01", tz="America/New_York")],
                                       "home_id": [1], "away_id": [2], "home_abbreviation": ["NY"],
                                       "away_abbreviation": ["BOS"], "status_type_completed": [True]}))
    c1 = FakeClient({url_for("schedules", 2025): data})
    hoopr.run("backfill", ["schedules"], seasons=[2025], client=c1, today=TODAY)
    c2 = FakeClient({url_for("schedules", 2025): data})
    s = hoopr.run("backfill", ["schedules"], seasons=[2025], client=c2, today=TODAY)
    assert s["counts"] == {"skipped_present": 1} and c2.gets == []


def test_one_failing_file_does_not_stop_the_others():
    good = parquet_bytes(player_box_df(3, season=2025))
    client = FakeClient({url_for("player_boxscores", 2026): hoopr.HooprError("boom"),
                         url_for("player_boxscores", 2025): good})
    s = hoopr.run("backfill", ["player_boxscores"], seasons=[2025, 2026], client=client, today=TODAY)
    assert s["counts"] == {"failed": 1, "new": 1} and s["status"] == "partial"
    assert "boom" in s["failures"][0]["error"]


def test_read_dataset_concatenates_mirrored_seasons_and_skips_missing(local_store):
    run_one({url_for("player_boxscores", 2026): parquet_bytes(player_box_df(3))})
    df = hoopr.read_dataset("player_boxscores", [2025, 2026])          # 2025 never mirrored -> skipped, not invented
    assert len(df) == 3 and set(df["season"]) == {2026}
    assert hoopr.read_dataset("player_boxscores", [1999]).empty


def test_cli_dry_run_makes_no_network_calls(capsys):
    assert hoopr.main(["--mode", "backfill", "--dry-run", "--datasets", "schedules", "--seasons", "2025-2026"]) == 0
    out = capsys.readouterr().out
    assert "schedules/2026" in out and "schedules/2025" in out and "2 files" in out


def test_head_size_and_client_contract(monkeypatch):
    class R:
        def __init__(self, code, body=b"", headers=None):
            self.status_code, self.content, self.headers = code, body, headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                import requests
                raise requests.HTTPError(f"HTTP {self.status_code}")

    class S:
        def __init__(self):
            self.headers = {}
            self.calls = 0

        def head(self, url, **kw):
            return R(404) if "missing" in url else R(200, headers={"Content-Length": "123"})

        def get(self, url, **kw):
            self.calls += 1
            return R(404) if "missing" in url else R(200, b"abc")

    c = hoopr.HooprClient(delay=0, session=S())
    assert c.head_size("http://x/ok") == 123 and c.head_size("http://x/missing") is None
    assert c.get_bytes("http://x/missing") is None and c.get_bytes("http://x/ok") == b"abc"
