"""Tests for the N5 prep: question schema, deterministic matcher, quarantine
gate, 48h reinstate rule, and the deterministic input capture.

Pure/offline: no API calls, no network. Fixture-shape tests cover the
committed capture sample; the F4 reconstruction-set fixture is validated
by test_decisions_prep_n2.py's sibling data tests if present, and by its
README counts.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ev.decisions_prep import n5_capture as cap
from ev.decisions_prep import n5_correction_sentinel as n5

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "decisions" / "n5_corrections"

SETTLED = {
    "game_key": "nhl:2026-11-02:COL@CHI",
    "player_id": 8477476,
    "player": "Artturi Lehkonen",
    "stat": "goals",
    "line": 0.5,
    "actual": 0,
    "settled_ts": "2026-11-03T13:31:05+00:00",
    "void": False,
}


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------

def test_build_questions_envelope_shape():
    body = n5.build_questions(SETTLED, {"published_ts": "2026-11-04T02:00:00Z",
                                        "headline": "Scoring change", "body": "..."})
    assert body["model"] == "gemini-decision-1"
    assert isinstance(body["questions"], dict)
    assert body["questions"][n5.Q_CORRECTION]["type"] == "noul"
    assert body["questions"][n5.Q_NO_CORRECTION]["type"] == "noul"
    kind = body["questions"][n5.Q_KIND]
    assert kind["type"] == "choice"
    assert set(kind["criteria"]) == set(n5.CORRECTION_KINDS)
    assert "Artturi Lehkonen" in body["input"]


# --------------------------------------------------------------------------
# matcher
# --------------------------------------------------------------------------

def test_match_settled_row_exact_and_ambiguous():
    rows = [SETTLED, {**SETTLED, "stat": "points", "actual": 1}]
    item = {"game_key": SETTLED["game_key"], "player_id": 8477476, "stat": "goals"}
    assert n5.match_settled_row(item, rows) is SETTLED
    assert n5.match_settled_row({**item, "stat": "assists"}, rows) is None
    dup = [SETTLED, dict(SETTLED)]
    assert n5.match_settled_row(item, dup) is None  # ambiguous -> fail closed


def test_match_settled_row_name_fallback():
    item = {"game_key": SETTLED["game_key"], "player": "Artturi Lehkonen", "stat": "goals"}
    row = {k: v for k, v in SETTLED.items() if k != "player_id"}
    assert n5.match_settled_row(item, [row]) is row


# --------------------------------------------------------------------------
# gate + reinstate
# --------------------------------------------------------------------------

def test_gate_quarantine_paths():
    assert n5.gate({n5.Q_CORRECTION: 0.85, n5.Q_NO_CORRECTION: 0.9})["action"] == "quarantine"
    assert n5.gate({n5.Q_CORRECTION: 0.10, n5.Q_NO_CORRECTION: 0.20})["action"] == "quarantine"
    assert n5.gate({n5.Q_CORRECTION: {"probability": 0.9},
                    n5.Q_NO_CORRECTION: {"probability": 0.9}})["action"] == "quarantine"


def test_gate_pass_and_abstention():
    assert n5.gate({n5.Q_CORRECTION: 0.05, n5.Q_NO_CORRECTION: 0.97})["action"] == "pass"
    out = n5.gate({})
    assert out["action"] == "quarantine" and out["reasons"] == ["abstention"]


def test_reinstate_rule():
    assert n5.reinstate_decision(49.0, False) == "reinstate"
    assert n5.reinstate_decision(10.0, False) == "pending"
    assert n5.reinstate_decision(10.0, True) == "route_to_human"
    assert n5.reinstate_decision(96.0, True) == "route_to_human"


# --------------------------------------------------------------------------
# capture: pairing, diff, adapters
# --------------------------------------------------------------------------

def test_parse_and_game_id_lookup():
    assert cap.parse_game_key("nhl:2026-11-02:COL@CHI") == {
        "sport": "nhl", "date": "2026-11-02", "away": "COL", "home": "CHI"}
    assert cap.parse_game_key("garbage") is None
    idx = cap.build_games_index(
        [{"date": "2026-11-02", "away_abbr": "COL", "home_abbr": "CHI", "game_id": 2026020234}])
    assert cap.game_id_for("nhl:2026-11-02:COL@CHI", idx) == 2026020234
    assert cap.game_id_for("nhl:2026-11-02:XXX@CHI", idx) is None


def test_capture_record_delta_and_correction_flag():
    rec = cap.build_capture_record(
        SETTLED, source=cap.CAPTURE_SOURCE_NHL_PAPER, game_id=2026020234,
        boxscore_current=1.0, boxscore_source="master", captured_ts="2026-11-13T00:00:00+00:00")
    assert rec["delta"] == 1.0 and rec["correction_detected"] is True
    rec2 = cap.build_capture_record(
        SETTLED, source=cap.CAPTURE_SOURCE_NHL_PAPER, game_id=2026020234,
        boxscore_current=0.0, boxscore_source="master", captured_ts="2026-11-13T00:00:00+00:00")
    assert rec2["delta"] == 0.0 and rec2["correction_detected"] is False
    rec3 = cap.build_capture_record(
        SETTLED, source=cap.CAPTURE_SOURCE_NHL_PAPER, game_id=None,
        boxscore_current=None, boxscore_source=None, captured_ts="2026-11-13T00:00:00+00:00")
    assert rec3["delta"] is None and rec3["correction_detected"] is False


def test_capture_record_unmatched_reason_field():
    rec = cap.build_capture_record(
        SETTLED, source=cap.CAPTURE_SOURCE_NHL_PAPER, game_id=2026020234,
        boxscore_current=1.0, boxscore_source="master", captured_ts="2026-11-13T00:00:00+00:00")
    assert rec["unmatched_reason"] is None
    rec_absent = cap.build_capture_record(
        SETTLED, source=cap.CAPTURE_SOURCE_NHL_PAPER, game_id=2026020234,
        boxscore_current=None, boxscore_source="master",
        captured_ts="2026-11-13T00:00:00+00:00",
        unmatched_reason=cap.UNMATCHED_PLAYER_ABSENT)
    assert rec_absent["unmatched_reason"] == "player_absent_from_boxscore"
    assert rec_absent["boxscore_current"] is None


def test_runner_unmatched_reasons(tmp_path):
    # One matched row, one whose game does not resolve against the games
    # master, one whose game resolves but who has no boxscore row
    # (did-not-dress). The runner must label each residue row's reason.
    settled_dir = tmp_path / "settled"
    settled_dir.mkdir()
    rows = [
        {"game_key": "nhl:2026-11-02:COL@CHI", "player_id": 8477476,
         "player": "Artturi Lehkonen", "stat": "goals", "actual": 0},
        {"game_key": "nhl:2026-11-02:XXX@CHI", "player_id": 8477476,
         "player": "Artturi Lehkonen", "stat": "goals", "actual": 0},
        {"game_key": "nhl:2026-11-02:COL@CHI", "player_id": 9999999,
         "player": "Dressed Never", "stat": "goals", "actual": 0},
    ]
    (settled_dir / "2026-11-02.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    games = tmp_path / "games.csv"
    games.write_text("date,away_abbr,home_abbr,game_id\n"
                     "2026-11-02,COL,CHI,2026020234\n")
    skater = tmp_path / "skater.csv"
    skater.write_text("game_id,player_id,goals,assists,points,sog\n"
                      "2026020234,8477476,1,0,1,3\n")
    goalie = tmp_path / "goalie.csv"
    goalie.write_text("game_id,player_id,saves\n")
    out = tmp_path / "capture.jsonl"
    counts = cap.run_nhl_paper_capture(settled_dir, games, skater, goalie, out)
    assert counts["unmatched_boxscore"] == 2
    assert counts["unmatched_game_unresolved"] == 1
    assert counts["unmatched_player_absent"] == 1
    records = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    reasons = {r["capture_id"]: r["unmatched_reason"] for r in records}
    assert sorted(v for v in reasons.values() if v) == [
        "game_unresolved", "player_absent_from_boxscore"]
    matched = [r for r in records if r["unmatched_reason"] is None]
    assert len(matched) == 1 and matched[0]["player_id"] == 8477476


def test_diff_captures():
    base = {"capture_id": "x", "stat": "saves", "boxscore_current": 25.0,
            "boxscore_captured_ts": "t1"}
    later = {**base, "boxscore_current": 24.0, "boxscore_captured_ts": "t2"}
    diffs = cap.diff_captures([base], [later])
    assert len(diffs) == 1 and diffs[0]["earlier_value"] == 25.0 and diffs[0]["later_value"] == 24.0
    assert cap.diff_captures([base], [base]) == []


def test_tracker_adapter_reads_settled_only(tmp_path):
    db = tmp_path / "bets.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE bets (game_date TEXT, player TEXT, bet_type TEXT, "
                 "result TEXT, settled_at TEXT, notes TEXT)")
    conn.execute("INSERT INTO bets VALUES ('2026-11-01','P One','goals_over_0.5','win','2026-11-02','')")
    conn.execute("INSERT INTO bets VALUES ('2026-11-01','P Two','goals_over_0.5',NULL,NULL,'')")
    conn.commit(); conn.close()
    rows = cap.read_tracker_settled(db)
    assert len(rows) == 1 and rows[0]["player"] == "P One"


def test_capture_sample_shape():
    sample = FIXTURE_DIR / "capture_sample.jsonl"
    assert sample.exists(), "capture sample missing; run the first pass (see CAPTURE.md)"
    records = [json.loads(l) for l in sample.read_text().splitlines() if l.strip()]
    assert 0 < len(records) <= 20
    required = {"capture_id", "source", "game_key", "player", "stat",
                "settled_actual", "boxscore_current", "delta", "correction_detected"}
    for rec in records:
        assert required <= set(rec)
    assert any(r["correction_detected"] for r in records), \
        "sample should include real detected corrections from the first pass"
