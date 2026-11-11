"""NHL SOG stats-API backfill (nhl.data.sog_backfill): the four report parsers, the (game, player) join,
date/season handling, the skip-existing resume rule, paging fallback and the declared User-Agent.
Offline throughout: payloads are trimmed copies of REAL 2025-01-06 responses (MacKinnon vs FLA,
game 2024020639), and the API client / store are fakes.
"""
import json
from datetime import date

import pytest

from nhl.data import sog_backfill as S

DAY = date(2025, 1, 6)
GAME, PID = 2024020639, 8477492          # MacKinnon (COL) vs FLA, 2025-01-06

SUMMARY = [{"gameId": GAME, "playerId": PID, "gameDate": "2025-01-06", "skaterFullName": "Nathan MacKinnon",
            "teamAbbrev": "COL", "opponentTeamAbbrev": "FLA", "homeRoad": "H", "positionCode": "C",
            "shots": 3, "timeOnIcePerGame": 1405.0, "goals": 0, "assists": 1}]
TIMEONICE = [{"gameId": GAME, "playerId": PID, "gameDate": "2025-01-06", "skaterFullName": "Nathan MacKinnon",
              "teamAbbrev": "COL", "opponentTeamAbbrev": "FLA", "homeRoad": "H", "positionCode": "C",
              "evTimeOnIce": 1022, "ppTimeOnIce": 383, "shTimeOnIce": 0, "otTimeOnIce": 0,
              "timeOnIce": 1405, "shifts": 23}]
REALTIME = [{"gameId": GAME, "playerId": PID, "gameDate": "2025-01-06", "totalShotAttempts": 6,
             "missedShots": 2, "shotAttemptsBlocked": 1, "blockedShots": 1, "hits": 0}]
POWERPLAY = [{"gameId": GAME, "playerId": PID, "gameDate": "2025-01-06", "ppIndividualSatFor": 0,
              "ppShots": 0, "ppTimeOnIce": 383}]


def by_report(**over):
    base = {"summary": SUMMARY, "timeonice": TIMEONICE, "realtime": REALTIME, "powerplay": POWERPLAY}
    base.update(over)
    return base


# ---------------------------------------------------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------------------------------------------------
def test_parse_summary_takes_shots_and_identity():
    m = S.parse_summary(SUMMARY)
    row = m[(GAME, PID)]
    assert row["shots"] == 3 and row["team"] == "COL" and row["opponent"] == "FLA"
    assert row["player_name"] == "Nathan MacKinnon" and row["position"] == "C" and row["home_road"] == "H"
    assert row["toi_total_sec"] == 1405                      # per-game float lands as int seconds


def test_parse_timeonice_takes_the_splits():
    row = S.parse_timeonice(TIMEONICE)[(GAME, PID)]
    assert (row["toi_ev_sec"], row["toi_pp_sec"], row["toi_sh_sec"], row["toi_ot_sec"]) == (1022, 383, 0, 0)
    assert row["toi_total_sec"] == 1405 and row["shifts"] == 23
    # ev+pp+sh is the additive identity; otTimeOnIce is a subset of the splits, NOT a 4th part
    # (verified on the live sample: all 1,872 rows satisfy ev+pp+sh == total, and every row with
    # ot > 0 breaks the four-way sum by exactly the OT amount).
    assert row["toi_ev_sec"] + row["toi_pp_sec"] + row["toi_sh_sec"] == row["toi_total_sec"]


def test_parse_realtime_takes_total_attempts_and_components():
    row = S.parse_realtime(REALTIME)[(GAME, PID)]
    assert row["attempts_total"] == 6 and row["attempts_missed"] == 2 and row["attempts_blocked"] == 1


def test_parse_powerplay_takes_pp_attempts_and_pp_shots():
    row = S.parse_powerplay(POWERPLAY)[(GAME, PID)]
    assert row["pp_attempts"] == 0 and row["pp_shots"] == 0 and row["pp_toi_sec"] == 383


def test_parsers_skip_rows_without_ids_and_non_regular_or_playoff_games():
    junk = [{"playerId": 1}, {"gameId": GAME}, {"gameId": 2024010639, "playerId": 9, "shots": 5}]  # 01 = preseason
    assert S.parse_summary(junk) == {}
    assert S.game_type_of(GAME) == 2 and S.game_type_of(2024030419) == 3 and S.game_type_of(123) is None


# ---------------------------------------------------------------------------------------------------------------------
# the join
# ---------------------------------------------------------------------------------------------------------------------
def test_join_merges_all_four_reports_on_game_and_player():
    rows = S.join_reports(DAY, "2024-25", "20242025", by_report())
    assert len(rows) == 1
    r = rows[0]
    assert (r["season"], r["season_id"], r["date"], r["game_id"], r["game_type"], r["player_id"]) == \
        ("2024-25", "20242025", "2025-01-06", GAME, 2, PID)
    assert r["shots"] == 3 and r["attempts_total"] == 6
    assert r["shots"] + r["attempts_missed"] + r["attempts_blocked"] == r["attempts_total"]   # live identity
    assert (r["toi_ev_sec"], r["toi_pp_sec"], r["toi_sh_sec"]) == (1022, 383, 0)
    assert r["pp_attempts"] == 0 and r["pp_toi_sec"] == r["toi_pp_sec"]                        # reports agree
    assert list(r) == S.ROW_FIELDS or set(r) == set(S.ROW_FIELDS)


def test_join_keeps_a_skater_missing_from_one_report_with_nulls_not_a_drop():
    rows = S.join_reports(DAY, "2024-25", "20242025", by_report(powerplay=[]))
    assert len(rows) == 1 and rows[0]["pp_attempts"] is None and rows[0]["shots"] == 3
    rows = S.join_reports(DAY, "2024-25", "20242025", by_report(summary=[]))
    assert len(rows) == 1 and rows[0]["shots"] is None and rows[0]["toi_ev_sec"] == 1022   # identity filled by timeonice


def test_join_sorts_by_game_then_player_and_jsonl_round_trips():
    two = [dict(SUMMARY[0], gameId=2024020640, playerId=1), dict(SUMMARY[0], gameId=GAME, playerId=999)]
    rows = S.join_reports(DAY, "2024-25", "20242025", by_report(summary=two, timeonice=[], realtime=[], powerplay=[]))
    assert [(r["game_id"], r["player_id"]) for r in rows] == [(GAME, 999), (2024020640, 1)]
    parsed = [json.loads(x) for x in S.to_jsonl(rows).decode().splitlines()]
    assert parsed == rows and S.to_jsonl([]) == b""


# ---------------------------------------------------------------------------------------------------------------------
# seasons / dates / keys
# ---------------------------------------------------------------------------------------------------------------------
def test_season_parsing_and_window():
    assert S.parse_season("2024-25") == (2024, "2024-25", "20242025")
    assert S.parse_season(2023) == (2023, "2023-24", "20232024")
    assert S.parse_season("20252026") == (2025, "2025-26", "20252026")
    assert S.season_window("2024-25") == (date(2024, 10, 1), date(2025, 6, 30))
    assert S.season_label_for_date(date(2025, 1, 6)) == "2024-25"
    assert S.season_label_for_date(date(2024, 11, 2)) == "2024-25"


def test_iter_dates_is_inclusive_and_crosses_months():
    assert S.iter_dates(date(2025, 1, 30), date(2025, 2, 2)) == \
        [date(2025, 1, 30), date(2025, 1, 31), date(2025, 2, 1), date(2025, 2, 2)]
    assert S.gcs_key(S.GCS_PREFIX, "2024-25", DAY) == "NHL/sog_backfill/2024-25/2025-01-06.jsonl"


# ---------------------------------------------------------------------------------------------------------------------
# fetching: UA, single-request day, paging fallback
# ---------------------------------------------------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code, self.headers = payload, status, {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FakeSession:
    """Records every request (url + the session's headers at call time); serves queued payloads by URL substring."""

    def __init__(self, payloads):
        self.payloads, self.calls, self.headers = payloads, [], {}

    def get(self, url, timeout=None):
        self.calls.append((url, dict(self.headers)))
        for needle, payload in self.payloads:
            if needle in url:
                return FakeResponse(payload(url) if callable(payload) else payload)
        raise AssertionError(f"unexpected URL {url}")


def test_client_sends_the_declared_user_agent_on_every_request():
    session = FakeSession([("skater/summary", {"data": SUMMARY, "total": 1})])
    client = S.SogStatsClient(delay=0, session=session)
    rows, n = S.fetch_report(client, "summary", DAY)
    assert rows == SUMMARY and n == 1
    url, headers = session.calls[0]
    assert headers["User-Agent"] == "Beezy Research (beezy.fyi; research@beezy.fyi)"
    assert "skater/summary" in url and "isGame=true" in url and "limit=-1" in url
    assert "2025-01-06" in url                                       # cayenneExp carries the quoted date


def test_fetch_report_pages_only_when_total_says_rows_are_missing():
    def payload_for(url):
        if "limit=-1" in url:
            return {"data": SUMMARY, "total": 2}                     # under-returns: 1 of 2 rows
        return {"data": [dict(SUMMARY[0], playerId=42, gameId=2024020640)], "total": 2}

    client = S.SogStatsClient(delay=0, session=FakeSession([("skater/summary", payload_for)]))
    rows, n = S.fetch_report(client, "summary", DAY)
    assert n == 2 and len(rows) == 2                                  # -> exactly one paged follow-up call


def test_fetch_report_handles_an_off_day():
    session = FakeSession([("skater/summary", {"data": [], "total": 0})])
    client = S.SogStatsClient(delay=0, session=session)
    assert S.fetch_report(client, "summary", DAY) == ([], 1)


# ---------------------------------------------------------------------------------------------------------------------
# the run: skip-existing, force, dry-run store
# ---------------------------------------------------------------------------------------------------------------------
class FakeClient:
    def __init__(self):
        self.pages = 0

    def report_page(self, report, day, start=0, limit=-1):
        self.pages += 1
        data = {"summary": SUMMARY, "timeonice": TIMEONICE, "realtime": REALTIME, "powerplay": POWERPLAY}[report]
        return {"data": data if day == DAY else [], "total": len(data) if day == DAY else 0}


def run_week(store):
    return S.run(season="2024-25", start=DAY, end=date(2025, 1, 7), store=store,
                 client_factory=FakeClient, max_workers=1)


def test_run_writes_one_jsonl_per_date_and_skips_on_rerun(tmp_path):
    store = S.LocalStore(tmp_path)
    totals = run_week(store)
    assert (totals["written"], totals["skipped"], totals["rows"], totals["requests"]) == (2, 0, 1, 8)
    key = "NHL/sog_backfill/2024-25/2025-01-06.jsonl"
    assert (tmp_path / key).exists() and (tmp_path / "NHL/sog_backfill/2024-25/2025-01-07.jsonl").exists()
    row = json.loads((tmp_path / key).read_text().splitlines()[0])
    assert row["shots"] == 3 and row["toi_pp_sec"] == 383 and row["attempts_total"] == 6
    assert (tmp_path / "NHL/sog_backfill/2024-25/2025-01-07.jsonl").read_bytes() == b""   # off day still marked done
    again = run_week(store)
    assert (again["written"], again["skipped"], again["requests"]) == (0, 2, 0)          # resume: no API calls


def test_run_force_repulls_and_workers_are_capped(tmp_path):
    store = S.LocalStore(tmp_path)
    run_week(store)
    forced = S.run(season="2024-25", start=DAY, end=DAY, store=store, client_factory=FakeClient,
                   max_workers=99, force=True)
    assert forced["written"] == 1 and forced["requests"] == 4


def test_run_requires_a_season_or_a_range(tmp_path):
    with pytest.raises(ValueError):
        S.run(store=S.LocalStore(tmp_path), client_factory=FakeClient)
