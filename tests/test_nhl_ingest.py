"""Tests for the NHL ingest / refresh / backfill flows with a fake API client + temp local storage."""
import copy
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from mlb_core import storage
from nhl.data.settings import (GAMES_MASTER, GOALIE_BOX_MASTER, LAST_REFRESH, SKATER_BOX_MASTER,
                        TEAM_BOX_MASTER, raw_boxscore_key, raw_pbp_key, shots_key)
from nhl.data import backfill, ingest, refresh

FIX = Path(__file__).parent / "fixtures"
_REAL_REFERENCE = refresh._refresh_reference
GID = 2026020090


@pytest.fixture(autouse=True)
def local_storage(tmp_path, monkeypatch):
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def no_reference(monkeypatch):
    """Default: the roster/team crosswalk step is stubbed out (it would hit the live API)."""
    monkeypatch.setattr(refresh, "_refresh_reference", lambda ref_client=None: {})


def _box(gid=GID, state="FINAL"):
    b = json.loads((FIX / "nhl_boxscore_sample.json").read_text())
    b["id"], b["gameState"] = gid, state
    return b


def _pbp(gid=GID):
    p = json.loads((FIX / "nhl_pbp_sample.json").read_text())
    p["id"] = gid
    return p


def _game(gid, state="OFF", gtype=2, season=20262027):
    return {"id": gid, "season": season, "gameType": gtype, "gameState": state}


class FakeClient:
    """schedule: {week_start: payload}; boxes/pbps keyed by game id. Records every call."""

    def __init__(self, schedule, boxes=None, pbps=None, fail_box=()):
        self.schedule, self.boxes, self.pbps, self.fail_box = schedule, boxes or {}, pbps or {}, set(fail_box)
        self.calls = []

    def get_schedule_week(self, d):
        self.calls.append(("sched", d))
        return self.schedule.get(d)

    def get_boxscore(self, gid):
        self.calls.append(("box", gid))
        if gid in self.fail_box:
            raise RuntimeError("boom")
        return copy.deepcopy(self.boxes.get(gid))

    def get_pbp(self, gid):
        self.calls.append(("pbp", gid))
        return copy.deepcopy(self.pbps.get(gid))


def _week(*days, nxt=None):
    return {"gameWeek": [{"date": d, "games": gs} for d, gs in days], "nextStartDate": nxt}


# -- games_in_weeks ---------------------------------------------------------------
def test_games_in_weeks_follows_next_start_date_and_filters():
    client = FakeClient({
        "2026-10-13": _week(("2026-10-13", [_game(1), _game(2, gtype=4), _game(3, season=20252026)]),
                            nxt="2026-10-20"),
        "2026-10-20": _week(("2026-10-21", [_game(4, state="FUT")]), nxt=None),
    })
    games = ingest.games_in_weeks(client, date(2026, 10, 13), date(2026, 10, 25), season=20262027)
    assert sorted(g["id"] for g in games) == [1, 4]                 # all-star + other-season dropped
    assert {g["id"]: g["_date"] for g in games} == {1: "2026-10-13", 4: "2026-10-21"}
    assert [c for c in client.calls if c[0] == "sched"] == [("sched", "2026-10-13"), ("sched", "2026-10-20")]


def test_games_in_weeks_survives_missing_payload_and_bad_next_start():
    client = FakeClient({"2026-10-13": _week(("2026-10-13", [_game(1)]), nxt="not-a-date")})
    games = ingest.games_in_weeks(client, date(2026, 10, 13), date(2026, 10, 30))
    assert [g["id"] for g in games] == [1]                          # bad nextStartDate -> +7d, no infinite loop


# -- fetch_game / cache -----------------------------------------------------------
def test_fetch_game_caches_final_and_second_call_hits_cache():
    client = FakeClient({}, {GID: _box()}, {GID: _pbp()})
    box, pbp = ingest.fetch_game(client, GID)
    assert box["id"] == GID and pbp["id"] == GID
    assert storage.exists(raw_boxscore_key(GID)) and storage.exists(raw_pbp_key(GID))
    n = len(client.calls)
    box2, pbp2 = ingest.fetch_game(client, GID)
    assert len(client.calls) == n                                   # served from cache
    assert box2["id"] == GID and pbp2["plays"]
    ingest.fetch_game(client, GID, force=True)
    assert len(client.calls) == n + 2                               # force refetches both


def test_fetch_game_never_caches_a_live_game():
    client = FakeClient({}, {GID: _box(state="LIVE")}, {GID: _pbp()})
    box, pbp = ingest.fetch_game(client, GID)
    assert box["gameState"] == "LIVE" and pbp is None               # no pbp fetch for a live game either
    assert not storage.exists(raw_boxscore_key(GID))


def test_fetch_game_tolerates_missing_pbp():
    client = FakeClient({}, {GID: _box()}, {})
    box, pbp = ingest.fetch_game(client, GID)
    assert box is not None and pbp is None
    assert not storage.exists(raw_pbp_key(GID))


def test_ingest_games_isolates_a_failing_game():
    other = GID + 1
    client = FakeClient({}, {GID: _box(), other: _box(other)}, {GID: _pbp()}, fail_box={other})
    acc = ingest.ingest_games(client, [_game(GID), _game(other)])
    assert [g["game_id"] for g in acc["games"]] == [GID]
    assert acc["failed"] == [other]


# -- refresh ----------------------------------------------------------------------
def _sched_for(*games, day="2026-10-13"):
    return {day: _week((day, list(games)))}


def test_refresh_ingests_final_games_and_skips_live_and_allstar():
    live, allstar = GID + 1, GID + 2
    client = FakeClient(
        _sched_for(_game(GID), _game(live, state="LIVE"), _game(allstar, gtype=4)),
        {GID: _box(), live: _box(live, "LIVE")}, {GID: _pbp()})
    res = refresh.refresh_nightly_gcs(date(2026, 10, 13), lookback_days=1, client=client)
    assert res["status"] == "ok" and res["games"] == 1
    assert res["games_added"] == 1 and res["team_rows_added"] == 2
    pbgs = _box()["playerByGameStats"]
    n_skaters = sum(len(pbgs[s][grp]) for s in ("awayTeam", "homeTeam") for grp in ("forwards", "defense"))
    n_goalies = sum(len(pbgs[s]["goalies"]) for s in ("awayTeam", "homeTeam"))
    assert res["skater_rows_added"] == n_skaters and res["goalie_rows_added"] == n_goalies
    assert res["shot_rows_added"] == 21
    assert ("pbp", live) not in client.calls and ("pbp", allstar) not in client.calls   # never fetched
    assert ("box", allstar) not in client.calls
    sentinel = json.loads(storage.read_bytes(LAST_REFRESH))
    assert sentinel["status"] == "ok" and sentinel["games"] == 1 and "timestamp" in sentinel
    assert len(storage.read_csv(GAMES_MASTER)) == 1
    assert len(storage.read_csv(shots_key(2026))) == 21


def test_refresh_is_idempotent_and_applies_corrections():
    client = FakeClient(_sched_for(_game(GID)), {GID: _box()}, {GID: _pbp()})
    refresh.refresh_nightly_gcs(date(2026, 10, 13), lookback_days=1, client=client)
    res2 = refresh.refresh_nightly_gcs(date(2026, 10, 13), lookback_days=1, client=client)
    assert res2["games_added"] == res2["team_rows_added"] == res2["skater_rows_added"] == 0
    assert res2["shot_rows_added"] == 0
    assert len(storage.read_csv(SKATER_BOX_MASTER)) == len(storage.read_csv(SKATER_BOX_MASTER).drop_duplicates(
        ["game_id", "player_id"]))
    # scorekeeper correction: DeBrincat's SOG changes -> upsert keeps the NEW value, row count unchanged
    fixed = _box()
    for p in fixed["playerByGameStats"]["homeTeam"]["forwards"]:
        if p["playerId"] == 8479337:
            p["sog"] = 7
    client.boxes[GID] = fixed
    refresh.refresh_nightly_gcs(date(2026, 10, 13), lookback_days=1, client=client)
    sk = storage.read_csv(SKATER_BOX_MASTER)
    assert sk[sk.player_id == 8479337].sog.item() == 7
    assert len(storage.read_csv(GAMES_MASTER)) == 1


def test_refresh_offseason_writes_skipped_sentinel_and_no_masters():
    client = FakeClient({"2026-07-14": {"gameWeek": [], "nextStartDate": None}})
    res = refresh.refresh_nightly_gcs(date(2026, 7, 14), lookback_days=1, client=client)
    assert res == {"status": "skipped", "start": "2026-07-14", "end": "2026-07-14", "games": 0}
    assert json.loads(storage.read_bytes(LAST_REFRESH))["status"] == "skipped"
    assert not storage.exists(GAMES_MASTER)


def test_refresh_reports_partial_when_a_game_fails():
    bad = GID + 1
    client = FakeClient(_sched_for(_game(GID), _game(bad)), {GID: _box(), bad: _box(bad)}, {GID: _pbp()},
                        fail_box={bad})
    res = refresh.refresh_nightly_gcs(date(2026, 10, 13), lookback_days=1, client=client)
    assert res["status"] == "partial" and res["failed_game_ids"] == [bad] and res["games"] == 1
    assert len(storage.read_csv(GAMES_MASTER)) == 1                  # the good game still landed


def test_refresh_lookback_window_is_filtered_by_schedule_day():
    old = GID + 1
    client = FakeClient({"2026-10-12": _week(("2026-10-12", [_game(old)]), ("2026-10-13", [_game(GID)]),
                                             ("2026-10-15", [_game(GID + 2)]))},
                        {GID: _box(), old: _box(old)}, {GID: _pbp(), old: _pbp(old)})
    res = refresh.refresh_nightly_gcs(date(2026, 10, 13), lookback_days=2, client=client)
    assert res["games"] == 2                                          # 10-12 + 10-13; 10-15 is outside the window


# -- backfill ---------------------------------------------------------------------
def test_backfill_season_walk_limit_and_masters(monkeypatch):
    ids = [2025020001, 2025020002, 2025020003]
    boxes = {i: _box(i) for i in ids}
    for i in ids:
        boxes[i]["season"] = 20252026
    client = FakeClient({
        "2025-08-01": _week(("2025-10-08", [_game(i, season=20252026) for i in ids]
                             + [_game(9, season=20242025), _game(10, season=20252026, state="FUT")])),
    }, boxes, {i: _pbp(i) for i in ids})
    res = backfill.run(seasons=[2025], client=client, limit=2)
    assert res["games_seen"] == 2 and res["failed_game_ids"] == []     # limit applies; FUT + other season dropped
    games = storage.read_csv(GAMES_MASTER)
    assert sorted(games.game_id.tolist()) == [2025020001, 2025020002]
    assert games.season_year.unique().tolist() == [2025]
    assert storage.exists(shots_key(2025))


def test_backfill_skips_a_season_that_has_not_started():
    client = FakeClient({})
    res = backfill.run(seasons=[date.today().year + 3], client=client)
    assert res["games_seen"] == 0 and client.calls == []


def test_backfill_parse_seasons():
    assert backfill._parse_seasons("2019-2021,2024") == [2019, 2020, 2021, 2024]
    assert backfill._parse_seasons("") == backfill.BACKFILL_SEASONS


# -- reference (players/teams crosswalk) step -------------------------------------
def test_reference_step_runs_after_games_and_failed_teams_make_status_partial(monkeypatch, local_storage):
    from nhl.data import players, teams
    order = []
    monkeypatch.setattr(refresh, "_refresh_reference", _REAL_REFERENCE)
    monkeypatch.setattr(players, "refresh_players",
                        lambda c: order.append("players") or {"rows": 10, "added": 10, "failed_teams": ["VAN"]})
    monkeypatch.setattr(teams, "refresh_teams", lambda c: order.append("teams") or {"teams": 32})
    client = FakeClient(_sched_for(_game(GID)), {GID: _box()}, {GID: _pbp()})
    res = refresh.refresh_nightly_gcs(date(2026, 10, 13), lookback_days=1, client=client, ref_client=object())
    assert order == ["players", "teams"]
    assert res["games"] == 1 and len(storage.read_csv(GAMES_MASTER)) == 1     # game ingest unaffected
    assert res["failed_teams"] == ["VAN"] and res["status"] == "partial"      # partial crosswalk is NOT ok
    assert json.loads(storage.read_bytes(LAST_REFRESH))["status"] == "partial"


def test_reference_failure_never_blocks_game_ingest(monkeypatch, local_storage):
    from nhl.data import players
    monkeypatch.setattr(refresh, "_refresh_reference", _REAL_REFERENCE)

    def boom(c):
        raise RuntimeError("429 storm")
    monkeypatch.setattr(players, "refresh_players", boom)
    client = FakeClient(_sched_for(_game(GID)), {GID: _box()}, {GID: _pbp()})
    res = refresh.refresh_nightly_gcs(date(2026, 10, 13), lookback_days=1, client=client, ref_client=object())
    assert len(storage.read_csv(GAMES_MASTER)) == 1
    assert "429 storm" in res["reference_error"] and res["status"] == "partial"


def test_reference_runs_on_no_game_days_and_clean_run_stays_skipped(monkeypatch, local_storage):
    from nhl.data import players, teams
    monkeypatch.setattr(refresh, "_refresh_reference", _REAL_REFERENCE)
    monkeypatch.setattr(players, "refresh_players", lambda c: {"rows": 5, "added": 0, "failed_teams": []})
    monkeypatch.setattr(teams, "refresh_teams", lambda c: {"teams": 32})
    client = FakeClient({"2026-07-14": {"gameWeek": [], "nextStartDate": None}})
    res = refresh.refresh_nightly_gcs(date(2026, 7, 14), lookback_days=1, client=client, ref_client=object())
    assert res["status"] == "skipped" and res["players"]["rows"] == 5 and "failed_teams" not in res


def test_reference_can_be_disabled():
    client = FakeClient(_sched_for(_game(GID)), {GID: _box()}, {GID: _pbp()})
    res = refresh.refresh_nightly_gcs(date(2026, 10, 13), lookback_days=1, client=client, reference=False)
    assert "players" not in res and res["status"] == "ok"
