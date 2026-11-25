"""Tests for MOD-04 Cluster (e) E0(iv): game-day inactives ingest and banking."""
import pandas as pd

from nfl.mod04e import inactives as I


def test_inactives_fetch_and_schema_round_trip(tmp_path):
    rosters_weekly = pd.DataFrame([
        {"season": 2024, "week": 2, "team": "KC", "gsis_id": "P_WR2", "full_name": "Second WR", "status": "ACT", "game_status": "INA"},
        {"season": 2024, "week": 2, "team": "KC", "gsis_id": "P_WR1", "full_name": "Star WR", "status": "ACT", "game_status": "ACT"},
    ])
    injuries = pd.DataFrame([
        {"season": 2024, "week": 2, "team": "CIN", "gsis_id": "P_TE1", "full_name": "Cincy TE",
         "report_status": "Out", "date_modified": "2024-09-15T15:10:00Z"},
    ])
    schedules = pd.DataFrame([
        {"game_id": "2024_02_CIN_KC", "season": 2024, "week": 2, "home_team": "KC", "away_team": "CIN"},
    ])

    recs = I.fetch_inactives(
        2024,
        2,
        source="nflverse",
        rosters_weekly=rosters_weekly,
        injuries=injuries,
        schedules=schedules,
        retrieved_at_utc="2024-09-15T15:15:00Z",
    )
    assert len(recs) == 2
    assert {r.player for r in recs} == {"Second WR", "Cincy TE"}
    for r in recs:
        assert r.game == "2024_02_CIN_KC"
        assert r.retrieved_at_utc.endswith("+00:00") or r.retrieved_at_utc.endswith("Z")

    written = I.bank_inactives(recs, root=tmp_path)
    assert (2024, 2) in written
    pq_path, js_path = written[(2024, 2)]
    assert "NFL/mod04e/inactives/season=2024/week=02/" in pq_path
    assert "NFL/mod04e/inactives/season=2024/week=02/" in js_path

    loaded = I.load_inactives(2024, 2, root=tmp_path)
    assert loaded == recs


def test_rescore_ready_excludes_records_retrieved_after_cutoff():
    kickoff = "2024-09-15T17:00:00Z"  # 90m cutoff is 15:30:00Z
    recs = [
        I.InactiveRecord("Early Out", "KC", "2024_02_CIN_KC", "INA", "live_feed", "2024-09-15T15:25:00Z"),
        I.InactiveRecord("Exact Cutoff", "KC", "2024_02_CIN_KC", "INA", "live_feed", "2024-09-15T15:30:00Z"),
        I.InactiveRecord("Late Scratch", "KC", "2024_02_CIN_KC", "INA", "live_feed", "2024-09-15T15:31:00Z"),
        I.InactiveRecord("Bad Stamp", "KC", "2024_02_CIN_KC", "INA", "live_feed", "not-a-timestamp"),
    ]
    ready = I.rescore_ready(recs, kickoff_utc=kickoff)
    assert [r.player for r in ready] == ["Early Out", "Exact Cutoff"]
