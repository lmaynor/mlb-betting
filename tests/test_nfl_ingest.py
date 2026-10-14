"""Tests for the nfl package (nflverse mirror). Offline: real parquet bytes are built in-process and a fake
client stands in for GitHub, so nothing here touches the network or a bucket."""
import io
import json
from datetime import date

import pandas as pd
import pytest

from nfl import config
from nfl.data import ingest, keys, read
from nfl.data.nflverse import NflverseClient, NflverseError


def parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


def stats_df(n=3, season=2025):
    return pd.DataFrame({"player_id": [f"00-{i:07d}" for i in range(n)], "season": season,
                         "week": 1, "team": "KC", "passing_yards": 100.0 + pd.Series(range(n))})


class FakeClient:
    """url -> bytes | None (404) | Exception. Records every GET."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.gets = []

    def get_bytes(self, url):
        self.gets.append(url)
        r = self.responses.get(url, None)
        if isinstance(r, Exception):
            raise r
        return r

    def head_size(self, url):
        r = self.responses.get(url)
        return len(r) if isinstance(r, (bytes, bytearray)) else None


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    """Route mlb_core.storage to a temp dir (the repo default BASE_DATA is a Windows path)."""
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    return tmp_path


TODAY = date(2026, 10, 14)          # in-season: current_season() == 2026


def url_for(name, season=None):
    return config.DATASETS[name].url(season)


# --------------------------------------------------------------------------------------------- config / urls

def test_urls_follow_nflreadpy_layout():
    assert url_for("schedules").endswith("/releases/download/schedules/games.parquet")
    assert url_for("player_stats", 2025).endswith("/stats_player/stats_player_week_2025.parquet")
    assert url_for("pbp", 2024).endswith("/pbp/play_by_play_2024.parquet")
    assert url_for("participation", 2023).endswith("/pbp_participation/pbp_participation_2023.parquet")
    assert url_for("rosters_weekly", 2022).endswith("/weekly_rosters/roster_weekly_2022.parquet")
    assert url_for("pfr_rec", 2021).endswith("/pfr_advstats/advstats_week_rec_2021.parquet")
    assert url_for("ngs_passing").endswith("/nextgen_stats/ngs_passing.parquet")


def test_keys_nest_under_nfl_prefix():
    assert config.DATASETS["player_stats"].key(2025) == "NFL/nflverse/player_stats/player_stats_2025.parquet"
    assert config.DATASETS["schedules"].key() == "NFL/nflverse/schedules/schedules.parquet"


@pytest.mark.parametrize("d,expected", [
    (date(2026, 9, 1), 2025),     # before Labor Day (Sep 7, 2026)
    (date(2026, 9, 8), 2025),     # Tuesday after Labor Day
    (date(2026, 9, 9), 2026),     # the Wednesday -> new season
    (date(2027, 1, 20), 2026),    # January playoffs still belong to the season that started the prior fall
    (date(2027, 2, 15), 2026),
])
def test_current_season_rolls_after_labor_day_wednesday(d, expected):
    assert config.current_season(d) == expected


def test_roster_year_rolls_on_march_15():
    assert config.current_season(date(2027, 3, 14), roster=True) == 2026
    assert config.current_season(date(2027, 3, 15), roster=True) == 2027


def test_every_dataset_has_a_valid_group_and_url():
    for ds in config.DATASETS.values():
        assert ds.group in config.GROUPS
        assert ds.url(2024 if ds.per_season else None).startswith("https://")
        assert ("{season}" in ds.stem) == ds.per_season


# --------------------------------------------------------------------------------------------- planning

def test_refresh_plans_current_season_and_every_single_file():
    items = ingest.plan([config.DATASETS[n] for n in ("schedules", "player_stats", "pbp")], "refresh", today=TODAY)
    assert [(i.dataset.name, i.season) for i in items] == [("schedules", None), ("player_stats", 2026), ("pbp", 2026)]
    assert all(i.latest for i in items)


def test_backfill_spans_window_newest_first_and_marks_only_top_latest():
    items = ingest.plan([config.DATASETS["player_stats"]], "backfill", today=TODAY)
    assert [i.season for i in items] == list(range(2026, 2017, -1))
    assert [i.latest for i in items] == [True] + [False] * 8


def test_backfill_clips_to_dataset_first_season():
    items = ingest.plan([config.DATASETS["ftn_charting"]], "backfill", seasons=range(2018, 2027), today=TODAY)
    assert [i.season for i in items] == [2026, 2025, 2024, 2023, 2022]     # ftn starts 2022


def test_explicit_seasons_never_exceed_current():
    items = ingest.plan([config.DATASETS["injuries"]], "backfill", seasons=[2024, 2030], today=TODAY)
    assert [i.season for i in items] == [2024]


def test_select_datasets_defaults_exclude_extra_and_validate_names():
    names = {d.name for d in ingest.select_datasets()}
    assert "pbp" in names and "schedules" in names and "contracts" not in names
    assert {d.name for d in ingest.select_datasets(groups=["extra"])} >= {"contracts", "combine"}
    with pytest.raises(ValueError):
        ingest.select_datasets(["not_a_dataset"])
    with pytest.raises(ValueError):
        ingest.select_datasets(groups=["bogus"])


def test_parse_seasons():
    assert ingest._parse_seasons("2024") == [2024]
    assert ingest._parse_seasons("2019,2022-2024") == [2019, 2022, 2023, 2024]
    assert ingest._parse_seasons(None) is None


# --------------------------------------------------------------------------------------------- ingest

def test_ingest_mirrors_bytes_verbatim_and_writes_manifest_and_sentinel(local_store):
    raw = parquet_bytes(stats_df(3, 2026))
    client = FakeClient({url_for("player_stats", 2026): raw})
    out = ingest.run("refresh", datasets=["player_stats"], client=client, today=TODAY)

    key = config.DATASETS["player_stats"].key(2026)
    assert (local_store / key).read_bytes() == raw                     # byte-for-byte, no re-encode
    manifest = json.loads((local_store / config.MANIFEST_KEY).read_text())
    assert manifest[key]["rows"] == 3 and manifest[key]["bytes"] == len(raw)
    sentinel = json.loads((local_store / config.LAST_REFRESH).read_text())
    assert sentinel["status"] == "ok" and out["written"] == ["player_stats/2026"]


def test_unchanged_content_is_not_rewritten(local_store, monkeypatch):
    raw = parquet_bytes(stats_df(3, 2026))
    client = FakeClient({url_for("player_stats", 2026): raw})
    ingest.run("refresh", datasets=["player_stats"], client=client, today=TODAY)

    writes = []
    real = ingest.storage.write_bytes
    monkeypatch.setattr(ingest.storage, "write_bytes", lambda d, k: (writes.append(k), real(d, k))[1])
    out = ingest.run("refresh", datasets=["player_stats"], client=client, today=TODAY)
    assert out["counts"] == {ingest.UNCHANGED: 1}
    assert config.DATASETS["player_stats"].key(2026) not in writes      # only manifest + sentinel written


def test_changed_content_updates(local_store):
    url = url_for("player_stats", 2026)
    ingest.run("refresh", datasets=["player_stats"], client=FakeClient({url: parquet_bytes(stats_df(3, 2026))}),
               today=TODAY)
    bigger = parquet_bytes(stats_df(5, 2026))
    out = ingest.run("refresh", datasets=["player_stats"], client=FakeClient({url: bigger}), today=TODAY)
    assert out["counts"] == {ingest.UPDATED: 1}
    assert (local_store / config.DATASETS["player_stats"].key(2026)).read_bytes() == bigger


def test_frozen_historical_season_is_skipped_without_a_request_unless_forced(local_store):
    u24, u25 = url_for("player_stats", 2024), url_for("player_stats", 2025)
    raw24 = parquet_bytes(stats_df(2, 2024))
    first = FakeClient({u24: raw24, u25: parquet_bytes(stats_df(2, 2025))})
    ingest.run("backfill", datasets=["player_stats"], seasons=[2024, 2025], client=first, today=TODAY)
    assert set(first.gets) == {u24, u25}

    again = FakeClient({u24: raw24, u25: parquet_bytes(stats_df(2, 2025))})
    out = ingest.run("backfill", datasets=["player_stats"], seasons=[2024, 2025], client=again, today=TODAY)
    assert out["counts"] == {ingest.SKIPPED: 2}
    assert again.gets == []                                  # no network for frozen seasons

    forced = FakeClient({u24: raw24, u25: parquet_bytes(stats_df(2, 2025))})
    ingest.run("backfill", datasets=["player_stats"], seasons=[2024, 2025], client=forced, force=True, today=TODAY)
    assert set(forced.gets) == {u24, u25}


def test_manifest_loss_does_not_refetch_frozen_seasons(local_store):
    u = url_for("player_stats", 2024)
    ingest.run("backfill", datasets=["player_stats"], seasons=[2024], client=FakeClient({u: parquet_bytes(stats_df())}),
               today=TODAY)
    (local_store / config.MANIFEST_KEY).unlink()
    c = FakeClient({u: parquet_bytes(stats_df())})
    out = ingest.run("backfill", datasets=["player_stats"], seasons=[2024], client=c, today=TODAY)
    assert out["counts"] == {ingest.SKIPPED: 1} and c.gets == []      # falls back to the object's existence


def test_newest_season_404_is_expected_not_a_failure(local_store):
    # e.g. participation for the in-progress season, or a Week-1 file not cut yet
    out = ingest.run("refresh", datasets=["participation"], client=FakeClient({}), today=TODAY)
    assert out["counts"] == {ingest.MISSING: 1} and out["status"] == "ok"


def test_historical_or_single_file_404_is_a_failure(local_store):
    # a 404 on a frozen season or on a single-file dataset means our URL assumption is wrong -> must be loud
    out = ingest.run("backfill", datasets=["player_stats"], seasons=[2024], client=FakeClient({}), today=TODAY)
    assert out["status"] == "error" and out["failures"][0]["item"] == "player_stats/2024"
    out = ingest.run("refresh", datasets=["schedules"], client=FakeClient({}), today=TODAY)
    assert out["status"] == "error"


def test_html_error_page_is_rejected_and_old_copy_survives(local_store):
    url = url_for("player_stats", 2026)
    good = parquet_bytes(stats_df(3, 2026))
    ingest.run("refresh", datasets=["player_stats"], client=FakeClient({url: good}), today=TODAY)
    out = ingest.run("refresh", datasets=["player_stats"], client=FakeClient({url: b"<html>rate limited</html>"}),
                     today=TODAY)
    assert out["counts"] == {ingest.INVALID: 1} and out["status"] == "error"
    assert (local_store / config.DATASETS["player_stats"].key(2026)).read_bytes() == good


def test_missing_required_columns_is_invalid(local_store):
    bad = parquet_bytes(pd.DataFrame({"foo": [1, 2]}))
    out = ingest.run("refresh", datasets=["player_stats"], client=FakeClient({url_for("player_stats", 2026): bad}),
                     today=TODAY)
    assert out["counts"] == {ingest.INVALID: 1}
    assert "missing required columns" in out["failures"][0]["error"]
    assert not (local_store / config.DATASETS["player_stats"].key(2026)).exists()


def test_empty_file_is_not_written_and_not_a_failure(local_store):
    empty = parquet_bytes(stats_df(3, 2026).iloc[0:0])
    out = ingest.run("refresh", datasets=["player_stats"], client=FakeClient({url_for("player_stats", 2026): empty}),
                     today=TODAY)
    assert out["counts"] == {ingest.EMPTY: 1} and out["status"] == "ok"
    assert not (local_store / config.DATASETS["player_stats"].key(2026)).exists()


def test_shrink_guard_refuses_truncated_publish_unless_forced(local_store):
    url = url_for("player_stats", 2026)
    big = parquet_bytes(stats_df(100, 2026))
    ingest.run("refresh", datasets=["player_stats"], client=FakeClient({url: big}), today=TODAY)
    small = parquet_bytes(stats_df(10, 2026))
    out = ingest.run("refresh", datasets=["player_stats"], client=FakeClient({url: small}), today=TODAY)
    assert out["counts"] == {ingest.SHRUNK: 1}
    assert (local_store / config.DATASETS["player_stats"].key(2026)).read_bytes() == big
    out = ingest.run("refresh", datasets=["player_stats"], client=FakeClient({url: small}), force=True, today=TODAY)
    assert out["counts"] == {ingest.UPDATED: 1}


def test_one_failing_file_does_not_stop_the_rest_and_exit_is_nonzero(local_store):
    ok_url = url_for("schedules")
    sched = parquet_bytes(pd.DataFrame({"game_id": ["g"], "season": [2026], "week": [1],
                                        "home_team": ["KC"], "away_team": ["BUF"]}))
    client = FakeClient({url_for("player_stats", 2026): NflverseError("boom"), ok_url: sched})
    out = ingest.run("refresh", datasets=["player_stats", "schedules"], client=client, today=TODAY)
    assert out["counts"] == {ingest.FAILED: 1, ingest.NEW: 1}
    assert out["status"] == "partial"
    assert (local_store / config.DATASETS["schedules"].key()).exists()
    assert ingest.is_failure({"status": ingest.FAILED})


# --------------------------------------------------------------------------------------------- http client

class FakeResp:
    def __init__(self, status, content=b""):
        self.status_code, self.content, self.headers = status, content, {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSession:
    def __init__(self, seq):
        self.seq, self.headers, self.calls = list(seq), {}, 0

    def get(self, url, timeout=None):
        self.calls += 1
        return self.seq.pop(0)


def test_client_404_returns_none_and_5xx_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("nfl.data.nflverse.time.sleep", lambda s: None)
    assert NflverseClient(delay=0, session=FakeSession([FakeResp(404)])).get_bytes("u") is None
    s = FakeSession([FakeResp(503), FakeResp(429), FakeResp(200, b"PAR1xxxxPAR1")])
    assert NflverseClient(delay=0, session=s).get_bytes("u") == b"PAR1xxxxPAR1"
    assert s.calls == 3


def test_client_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr("nfl.data.nflverse.time.sleep", lambda s: None)
    s = FakeSession([FakeResp(500)] * 4)
    with pytest.raises(NflverseError):
        NflverseClient(delay=0, session=s).get_bytes("u")
    assert s.calls == 4


def test_client_enforces_size_cap(monkeypatch):
    monkeypatch.setattr("nfl.data.nflverse.time.sleep", lambda s: None)
    with pytest.raises(NflverseError):
        NflverseClient(delay=0, session=FakeSession([FakeResp(200, b"x" * 50)]), max_bytes=10).get_bytes("u")


# --------------------------------------------------------------------------------------------- reader

def test_load_concats_seasons_and_strict_missing_raises(local_store):
    for s in (2024, 2025):
        ingest.run("backfill", datasets=["player_stats"], seasons=[s],
                   client=FakeClient({url_for("player_stats", s): parquet_bytes(stats_df(2, s))}), today=TODAY)
    assert read.mirrored_seasons("player_stats") == [2024, 2025]
    df = read.load("player_stats")
    assert sorted(df["season"].unique()) == [2024, 2025] and len(df) == 4
    assert list(read.load("player_stats", seasons=[2025], columns=["player_id", "season"]).columns) == \
        ["player_id", "season"]
    with pytest.raises(FileNotFoundError, match="2023"):
        read.load("player_stats", seasons=[2023, 2024])
    assert len(read.load("player_stats", seasons=[2023, 2024], strict=False)) == 2


def test_load_single_file_and_unknown_dataset(local_store):
    sched = pd.DataFrame({"game_id": ["g"], "season": [2026], "week": [1], "home_team": ["KC"], "away_team": ["BUF"]})
    ingest.run("refresh", datasets=["schedules"], client=FakeClient({url_for("schedules"): parquet_bytes(sched)}),
               today=TODAY)
    assert len(read.load("schedules")) == 1
    with pytest.raises(ValueError):
        read.load("nope")


# --------------------------------------------------------------------------------------------- keys

def test_ev_code_crosswalk():
    assert keys.ev_code("JAX") == "JAC" and keys.ev_code("LA") == "LAR"
    assert keys.ev_code("OAK") == "LV" and keys.ev_code("SD") == "LAC" and keys.ev_code("STL") == "LAR"
    assert keys.ev_code("KC") == "KC" and keys.ev_code("WAS") == "WAS"
    assert keys.ev_code("AFC") is None and keys.ev_code(None) is None      # fail closed, never guess


def test_game_key_matches_ev_owned_format_and_codes():
    from ev.kalshi_map import game_key as ev_game_key
    from ev.sports.nfl import CODES
    assert keys.make_game_key("2026-10-12", "BUF", "KC") == ev_game_key("nfl", "2026-10-12", "BUF", "KC")
    assert keys.make_game_key("2026-10-12", "JAX", "LA") == ev_game_key("nfl", "2026-10-12", "JAC", "LAR")
    assert keys._EV_CODES == CODES                                          # the two code universes cannot drift
    assert keys.make_game_key("2026-10-12", "XXX", "KC") is None


def test_add_game_keys():
    s = pd.DataFrame({"gameday": ["2026-10-12", "2026-10-13"], "away_team": ["JAX", "OAK"], "home_team": ["LA", "KC"]})
    out = keys.add_game_keys(s)
    assert list(out["game_key"]) == ["nfl:2026-10-12:JAC@LAR", "nfl:2026-10-13:LV@KC"]
    assert "game_key" not in s.columns                                      # input not mutated
