"""Per-row `engine` labeling and payload-level `engine_id(sport)` tests for ev.daily_projections."""
from datetime import datetime, timezone

import pytest

from ev import daily_projections as dp
from mlb_core.data import cockpit_form as cf
from tests.test_nfl_props_hierarchical import _logs_and_args
from tests.test_nhl_trained_board import AS_OF, PIDS, logs_with_ids

NOW = datetime(2026, 2, 4, 16, 0, tzinfo=timezone.utc)
START = datetime(2026, 2, 4, 23, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(dp._rosters, "load", lambda sport, now=None, **k: None)
    monkeypatch.setattr(dp, "write_archive", lambda payload, now: [])


def _nba_logs():
    rows = [{"name": "Jo Doe", "team_abbr": "LAL", "_date": f"2026-01-{i + 1:02d}", "_sort": f"2026-01-{i + 1:02d}",
             "_opp": "BOS", "season_year": 2025, "points": 22.0 + (i % 3), "rebounds": 6.0, "assists": 4.0,
             "three_points_made": 2.0, "three_points_attempted": 5.0, "minutes": 32.0} for i in range(15)]
    return dp.GameLogs("nba", {"all": {cf.norm_name("Jo Doe"): rows}})


def test_nhl_trained_and_hierarchical_row_engine_labels():
    served = {(PIDS["Ann Forward"], AS_OF, "goals"): (0.41, 0.35, "v7")}
    logs = logs_with_ids(served)

    trained_row = dp.make_row(
        "nhl", "goals", "Ann Forward", cf.norm_name("Ann Forward"),
        logs.idx["skater"][cf.norm_name("Ann Forward")], cf.NHL_SKATER_STATS["goals"],
        logs, 0.5, None, [], "TOR", "BOS", f"nhl:{AS_OF}:BOS@TOR", START, 60.0,
    )
    assert trained_row is not None
    assert trained_row["nhl_model"] == "trained_v1"
    assert trained_row["engine"] == "nhl_trained_v1"

    hier_row = dp.make_row(
        "nhl", "assists", "Ann Forward", cf.norm_name("Ann Forward"),
        logs.idx["skater"][cf.norm_name("Ann Forward")], cf.NHL_SKATER_STATS["assists"],
        logs, 0.5, None, [], "TOR", "BOS", f"nhl:{AS_OF}:BOS@TOR", START, 60.0,
    )
    assert hier_row is not None
    assert hier_row["nhl_model"] == "hierarchical_v1"
    assert hier_row["engine"] == "hierarchical_v1"


def test_nba_hierarchical_row_engine_label():
    logs = _nba_logs()
    rows = logs.idx["all"][cf.norm_name("Jo Doe")]
    row = dp.make_row(
        "nba", "points", "Jo Doe", cf.norm_name("Jo Doe"), rows, cf.NBA_STATS["points"],
        logs, 22.5, None, [], "LAL", "BOS", "nba:2026-02-04:BOS@LAL", START, 60.0,
    )
    assert row is not None
    assert row["nba_model"] == "hier_v1"
    assert row["engine"] == "hier_v1"


def test_plain_ewm_fallback_row_engine_label(monkeypatch):
    logs, args = _logs_and_args(dp)
    monkeypatch.setattr(dp.GameLogs, "nfl_live", lambda self: None)
    monkeypatch.setattr(dp.GameLogs, "env_lines", lambda self: {})
    monkeypatch.delenv("NFL_ENGINE", raising=False)
    monkeypatch.delenv("NFL_HIER_STATS", raising=False)

    row = dp.make_row(*args("rec_yds", "wr1"))
    assert row is not None
    assert row["engine"] == "empirical_ewm_v2"
    assert not dp.is_hierarchical_nfl_row(row)
    assert dp.live_served_rows([row]) == [row]
    assert dp._row_engine({}, None, None) == "empirical_ewm_v2"


def test_nfl_hierarchical_row_still_gated_by_is_hierarchical_nfl_row(monkeypatch):
    logs, args = _logs_and_args(dp)
    monkeypatch.setattr(dp.GameLogs, "nfl_live", lambda self: None)
    monkeypatch.setattr(dp.GameLogs, "env_lines", lambda self: {})
    monkeypatch.setenv("NFL_ENGINE", "hierarchical")
    monkeypatch.setenv("NFL_HIER_STATS", "rec_yds")

    hier_row = dp.make_row(*args("rec_yds", "wr1"))
    plain_row = dp.make_row(*args("receptions", "wr1"))
    assert hier_row is not None and plain_row is not None
    assert hier_row["engine"] == "hierarchical_v1"
    assert plain_row["engine"] == "empirical_ewm_v2"
    assert dp.is_hierarchical_nfl_row(hier_row) is True
    assert dp.is_hierarchical_nfl_row(plain_row) is False
    assert dp.live_served_rows([hier_row, plain_row]) == [plain_row]


def test_engine_id_summarizes_configured_mix(monkeypatch):
    monkeypatch.delenv("NHL_TRAINED_PROPS", raising=False)
    monkeypatch.delenv("NFL_ENGINE", raising=False)
    monkeypatch.delenv("NFL_HIER_STATS", raising=False)

    assert dp.engine_id("nhl") == "hierarchical_v1"
    monkeypatch.setenv("NHL_TRAINED_PROPS", "1")
    nhl_mix = dp.engine_id("nhl")
    assert "hierarchical_v1" in nhl_mix and "nhl_trained_v1" in nhl_mix
    assert nhl_mix == "hierarchical_v1+nhl_trained_v1"

    assert dp.engine_id("nba") == "hier_v1"
    assert dp.engine_id("nfl") == "empirical_ewm_v2"

    monkeypatch.setenv("NFL_ENGINE", "hierarchical")
    monkeypatch.setenv("NFL_HIER_STATS", "rec_yds")
    assert dp.engine_id("nfl") == "empirical_ewm_v2+hierarchical_v1"

    assert dp.engine_id("other") == "empirical_ewm_v2"


def test_run_sport_nhl_payload_and_rows_carry_truthful_engines(monkeypatch):
    served = {(PIDS["Ann Forward"], AS_OF, "goals"): (0.41, 0.35, "v7")}
    logs = logs_with_ids(served)
    monkeypatch.setenv("NHL_TRAINED_PROPS", "1")
    monkeypatch.setattr(dp, "load_trained", lambda now=None: served)
    monkeypatch.setattr(dp._sh, "load_settled_saves_history", lambda now=None: None)
    ev = {
        "id": "e1",
        "commence_time": START.isoformat().replace("+00:00", "Z"),
        "away_team": "Boston Bruins",
        "home_team": "Toronto Maple Leafs",
        "bookmakers": [
            {"key": b, "markets": [
                {"key": "player_goals", "outcomes": [
                    {"name": "Over", "description": "Ann Forward", "price": 180, "point": 0.5},
                    {"name": "Under", "description": "Ann Forward", "price": -220, "point": 0.5},
                ]},
                {"key": "player_assists", "outcomes": [
                    {"name": "Over", "description": "Ann Forward", "price": 150, "point": 0.5},
                    {"name": "Under", "description": "Ann Forward", "price": -180, "point": 0.5},
                ]},
            ]}
            for b in ("draftkings", "fanduel", "betmgm", "caesars")
        ],
    }
    payload = dp.run_sport("nhl", NOW, logs=logs, events=[ev], meta={}, games=[], write=False, roster=None)
    assert payload["engine"] == "hierarchical_v1+nhl_trained_v1"
    by_stat = {r["stat"]: r for r in payload["rows"] if r["player"] == "Ann Forward"}
    assert by_stat["goals"]["engine"] == "nhl_trained_v1"
    assert by_stat["assists"]["engine"] == "hierarchical_v1"
