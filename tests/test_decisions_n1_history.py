"""Tests for DEC-05 Snapshot-History Block and temporal short-circuit.

Argon ruling RUL-20261113-dec-gate-design Q4.
Hermetic and offline: all tests use fabricated payloads and in-memory structures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from ev.decisions_client import BatchResult
from ev.decisions_evals.common import AnswersCache
from ev.decisions_evals.eval_ladder import run_eval
from ev.decisions_ladder_adjudicator import (
    adjudicate_entries,
    make_entry_key,
)
from ev.decisions_prep.n1_snapshot_history import (
    PERIOD_TOKEN_RE,
    build_sidecar,
    compute_history,
    entry_side,
    extract_quotes,
    insufficient_history,
    render_history_block,
    scrape_ts_from_filename,
)
from ev.props import norm_name, split_player


# ---------------------------------------------------------------------------
# 1. extract_quotes and name normalization
# ---------------------------------------------------------------------------


def test_extract_quotes_over_under_and_yes_grouping():
    """extract_quotes groups Over/Under rows and Yes rows correctly; player_norm strips team suffix."""
    payload = {
        "id": "event_001",
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "player_pass_yds",
                        "outcomes": [
                            {
                                "name": "Over 250.5",
                                "description": "Cam Ward (TEN)",
                                "price": -110,
                                "point": 250.5,
                            },
                            {
                                "name": "Under 250.5",
                                "description": "Cam Ward (TEN)",
                                "price": -115,
                                "point": 250.5,
                            },
                        ],
                    },
                    {
                        "key": "player_receiving_yards",
                        "outcomes": [
                            {
                                "name": "Yes",
                                "description": "Laquon Treadwell",
                                "price": 105,
                                "point": 22.5,
                            }
                        ],
                    },
                ],
            }
        ],
    }

    quotes = extract_quotes(payload)
    assert len(quotes) == 2

    # Cam Ward group
    q_ward = next(q for q in quotes if q["raw_market_key"] == "player_pass_yds")
    assert q_ward["book"] == "draftkings"
    assert q_ward["player"] == "Cam Ward (TEN)"
    assert q_ward["player_norm"] == "cam ward"
    # Suffix stripping matches norm_name of the base player description
    assert q_ward["player_norm"] == norm_name(split_player("Cam Ward (TEN)")[0])
    assert q_ward["raw_point"] == 250.5
    assert q_ward["sides_present"] == ["over", "under"]
    assert q_ward["prices"] == {"over": -110, "under": -115}
    assert q_ward["names"] == ["Over 250.5", "Under 250.5"]

    # Treadwell group
    q_treadwell = next(q for q in quotes if q["raw_market_key"] == "player_receiving_yards")
    assert q_treadwell["book"] == "draftkings"
    assert q_treadwell["player"] == "Laquon Treadwell"
    assert q_treadwell["player_norm"] == "laquon treadwell"
    assert q_treadwell["raw_point"] == 22.5
    assert q_treadwell["sides_present"] == ["yes"]
    assert q_treadwell["prices"] == {"yes": 105}
    assert q_treadwell["names"] == ["Yes"]


# ---------------------------------------------------------------------------
# 2. compute_history on 3-snapshot series
# ---------------------------------------------------------------------------


def test_compute_history_3_snapshot_series():
    """compute_history on a 3-snapshot series: 2 one-sided, 1 two-sided, price moves, and causality."""
    entry = {
        "book": "betrivers",
        "event_id": "event_123",
        "player": "Laquon Treadwell",
        "player_norm": "laquon treadwell",
        "raw_market_key": "player_receiving_yards",
        "raw_point": 22.5,
        "raw_name": "Yes",
    }

    # Snapshot 1: Yes only @ -110
    payload_1 = {
        "id": "event_123",
        "bookmakers": [
            {
                "key": "betrivers",
                "markets": [
                    {
                        "key": "player_receiving_yards",
                        "outcomes": [
                            {"name": "Yes", "description": "Laquon Treadwell", "price": -110, "point": 22.5}
                        ],
                    }
                ],
            }
        ],
    }

    # Snapshot 2: Yes only @ -110 (no price change)
    payload_2 = {
        "id": "event_123",
        "bookmakers": [
            {
                "key": "betrivers",
                "markets": [
                    {
                        "key": "player_receiving_yards",
                        "outcomes": [
                            {"name": "Yes", "description": "Laquon Treadwell", "price": -110, "point": 22.5}
                        ],
                    }
                ],
            }
        ],
    }

    # Snapshot 3: Both Yes @ -120 and No @ +100 (two-sided, price change)
    payload_3 = {
        "id": "event_123",
        "bookmakers": [
            {
                "key": "betrivers",
                "markets": [
                    {
                        "key": "player_receiving_yards",
                        "outcomes": [
                            {"name": "Yes", "description": "Laquon Treadwell", "price": -120, "point": 22.5},
                            {"name": "No", "description": "Laquon Treadwell", "price": 100, "point": 22.5},
                        ],
                    }
                ],
            }
        ],
    }

    snapshots = [
        ("2026-10-04T12:00:00", payload_1),
        ("2026-10-04T12:30:00", payload_2),
        ("2026-10-04T13:00:00", payload_3),
    ]
    eval_ts = "2026-10-04T13:00:00"

    features = compute_history(entry, snapshots, eval_ts)

    assert features["snapshot_count"] == 3
    assert features["first_seen_ts"] == "2026-10-04T12:00:00"
    assert features["last_seen_ts"] == "2026-10-04T13:00:00"
    assert features["elapsed_span_minutes"] == 60.0
    assert features["two_sided_count"] == 1
    assert features["yes_only_count"] == 2
    assert features["over_only_count"] == 0
    assert features["under_only_count"] == 0
    assert features["no_only_count"] == 0
    assert features["one_sided_snapshot_ratio"] == round(2 / 3, 4)  # 0.6667
    assert features["ever_two_sided"] is True
    assert features["opposite_side_ever_appeared"] is True
    # Prices: [-110, -110, -120] -> consecutive pairs: (-110, -110) same, (-110, -120) changed -> 1
    assert features["price_move_count"] == 1
    assert features["distinct_rungs"] == [22.5]
    assert features["min_rung"] == 22.5
    assert features["max_rung"] == 22.5
    assert features["event_snapshot_count"] == 3
    assert features["eval_ts"] == eval_ts


def test_scrape_ts_from_filename_parsing():
    """scrape_ts_from_filename correctly parses timestamps."""
    ts1 = scrape_ts_from_filename("2026-10-04", "1edcfd0fbbb7e0c8efd180c275878895_125725.json")
    assert ts1 == "2026-10-04T12:57:25"

    ts2 = scrape_ts_from_filename("2026-10-04", "/path/to/raw/2026-10-04/abc_130000.json")
    assert ts2 == "2026-10-04T13:00:00"

    assert ts1 < ts2


# ---------------------------------------------------------------------------
# 3. insufficient_history truth table
# ---------------------------------------------------------------------------


def test_insufficient_history_truth_table():
    """insufficient_history: count 2 + no collisions -> True; count 2 + collision -> False; count 3 -> False."""
    # count 2, no collisions -> True
    assert insufficient_history({
        "snapshot_count": 2,
        "sibling_full_game": False,
        "sibling_period_scoped": False,
        "same_book_distinct_full_game_market": False,
    }) is True

    # count 2 + sibling_full_game -> False
    assert insufficient_history({
        "snapshot_count": 2,
        "sibling_full_game": True,
        "sibling_period_scoped": False,
        "same_book_distinct_full_game_market": False,
    }) is False

    # count 2 + sibling_period_scoped -> False
    assert insufficient_history({
        "snapshot_count": 2,
        "sibling_full_game": False,
        "sibling_period_scoped": True,
        "same_book_distinct_full_game_market": False,
    }) is False

    # count 2 + same_book_distinct_full_game_market -> False
    assert insufficient_history({
        "snapshot_count": 2,
        "sibling_full_game": False,
        "sibling_period_scoped": False,
        "same_book_distinct_full_game_market": True,
    }) is False

    # count 3, no collisions -> False
    assert insufficient_history({
        "snapshot_count": 3,
        "sibling_full_game": False,
        "sibling_period_scoped": False,
        "same_book_distinct_full_game_market": False,
    }) is False


# ---------------------------------------------------------------------------
# 4. Sibling collisions in eval snapshot
# ---------------------------------------------------------------------------


def test_sibling_collisions():
    """Sibling collisions flags are correctly resolved in the eval snapshot."""
    entry_mapped = {
        "book": "betrivers",
        "event_id": "event_123",
        "player": "Test Player",
        "player_norm": "test player",
        "raw_market_key": "player_pass_yds",
        "raw_point": 250.5,
        "raw_name": "Over 250.5",
    }

    # Eval payload with:
    # 1. Another book (draftkings) posting same stat (pass_yds) under player_passing_yards
    # 2. Same book (betrivers) posting a different key for pass_yds (player_passing_yards)
    # 3. A 1q market for the same player
    eval_payload = {
        "id": "event_123",
        "bookmakers": [
            {
                "key": "betrivers",
                "markets": [
                    {
                        "key": "player_passing_yards",
                        "outcomes": [{"name": "Over 250.5", "description": "Test Player", "point": 250.5}],
                    }
                ],
            },
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "player_passing_yards",
                        "outcomes": [{"name": "Over 250.5", "description": "Test Player", "point": 250.5}],
                    },
                    {
                        "key": "player_pass_yds_1q",
                        "outcomes": [{"name": "Over 60.5", "description": "Test Player", "point": 60.5}],
                    },
                ],
            },
        ],
    }

    snapshots = [("2026-10-04T12:00:00", eval_payload)]
    eval_ts = "2026-10-04T12:00:00"

    features = compute_history(entry_mapped, snapshots, eval_ts)
    assert features["sibling_full_game"] is True
    assert features["same_book_distinct_full_game_market"] is True
    assert features["sibling_period_scoped"] is True
    assert features["period_token_ever"] is True

    # Unmapped key entry with cross-book mapped quote
    entry_unmapped = {
        "book": "betrivers",
        "event_id": "event_123",
        "player": "Test Player",
        "player_norm": "test player",
        "raw_market_key": "unmapped_player_stat_xyz",
        "raw_point": 250.5,
        "raw_name": "Over 250.5",
    }
    features_unmapped = compute_history(entry_unmapped, snapshots, eval_ts)
    # Draftkings quotes have a valid stat_of key without period token -> sibling_full_game True
    assert features_unmapped["sibling_full_game"] is True


# ---------------------------------------------------------------------------
# 5. Adjudication route short-circuit
# ---------------------------------------------------------------------------


class RaisingDecisionsClient:
    """Decisions client that raises if called."""

    def call(self, *args: Any, **kwargs: Any) -> BatchResult:
        raise AssertionError("Decisions client call() should not be invoked for short-circuited entries!")


def test_adjudicate_entries_short_circuit_insufficient_history():
    """Ambiguous entries with insufficient history are short-circuited immediately."""
    entry = {
        "book": "novig",
        "player": "Test Player",
        "player_norm": "test player",
        "event_id": "event_456",
        "raw_market_key": "player_receiving_yards",
        "raw_description": "Test Player",
        "raw_name": "Over 64.5",
        "raw_point": 64.5,
        "raw_price": -115,
        "raw_price_side_info": {"sides_present": ["over", "under"]},
        "parser_candidate_stat": "rec_yds",
        "cross_book_main_point": 44.5,
        "points_for_book_player": [44.5, 64.5],
        "ambiguity_class": "twosided_offmain_rung",
    }
    ekey = make_entry_key(entry)

    # 1 snapshot, no collisions -> insufficient_history is True
    histories = {
        ekey: {
            "snapshot_count": 1,
            "sibling_full_game": False,
            "sibling_period_scoped": False,
            "same_book_distinct_full_game_market": False,
        }
    }

    client = RaisingDecisionsClient()
    result = adjudicate_entries([entry], client=client, snapshot_histories=histories)

    assert len(result["decisions"]) == 1
    d = result["decisions"][0]
    assert d["status"] == "quarantined"
    assert d["identity"] is None
    assert d["reasons"] == ["insufficient_temporal_history"]
    assert d["applied"] is False
    assert result["summary"]["insufficient_temporal_history"] == 1
    assert result["summary"]["quarantined"] == 1


# ---------------------------------------------------------------------------
# 6. render_history_block
# ---------------------------------------------------------------------------


def test_render_history_block_ruled_fields():
    """render_history_block contains all ruled field names in causal order."""
    features = {
        "snapshot_count": 3,
        "first_seen_ts": "2026-10-04T12:00:00",
        "last_seen_ts": "2026-10-04T13:00:00",
        "elapsed_span_minutes": 60.0,
        "two_sided_count": 1,
        "over_only_count": 0,
        "under_only_count": 0,
        "yes_only_count": 2,
        "no_only_count": 0,
        "one_sided_snapshot_ratio": 0.6667,
        "ever_two_sided": True,
        "opposite_side_ever_appeared": False,
        "price_move_count": 1,
        "distinct_rungs": [22.5, 24.5],
        "min_rung": 22.5,
        "max_rung": 24.5,
        "period_token_ever": False,
        "sibling_full_game": True,
        "sibling_period_scoped": False,
        "same_book_distinct_full_game_market": False,
        "event_snapshot_count": 3,
        "eval_ts": "2026-10-04T13:00:00",
    }

    block = render_history_block(features)
    assert block.startswith(
        "Snapshot history (deterministic, causal: only snapshots at or before the evaluated snapshot):"
    )
    assert "snapshot_count: 3" in block
    assert "one_sided_snapshot_ratio: 0.6667" in block
    assert "ever_two_sided: yes" in block
    assert "price_move_count: 1" in block
    assert "sibling_full_game: yes" in block
    assert "sibling_period_scoped: no" in block
    assert "same_book_distinct_full_game_market: no" in block
    assert "Evaluated snapshot scrape time: 2026-10-04T13:00:00" in block


# ---------------------------------------------------------------------------
# 7. build_sidecar end-to-end
# ---------------------------------------------------------------------------


def test_build_sidecar(tmp_path: Path):
    """build_sidecar computes causal features and outputs sidecar JSONL."""
    raw_dir = tmp_path / "raw"
    day_dir = raw_dir / "2026-10-04"
    day_dir.mkdir(parents=True)

    event_id = "test_event_1"
    eval_filename = f"{event_id}_123000.json"
    eval_fpath = day_dir / eval_filename
    earlier_fpath = day_dir / f"{event_id}_120000.json"
    future_fpath = day_dir / f"{event_id}_130000.json"

    quote_outcome = {
        "name": "Yes",
        "description": "Test Player",
        "price": -110,
        "point": 22.5,
    }
    payload = {
        "id": event_id,
        "bookmakers": [
            {
                "key": "betrivers",
                "markets": [
                    {
                        "key": "player_receiving_yards",
                        "outcomes": [quote_outcome],
                    }
                ],
            }
        ],
    }

    earlier_fpath.write_text(json.dumps(payload), encoding="utf-8")
    eval_fpath.write_text(json.dumps(payload), encoding="utf-8")
    future_fpath.write_text(json.dumps(payload), encoding="utf-8")

    labels_file = tmp_path / "labels.jsonl"
    label_entry = {
        "event_id": event_id,
        "source_day": "2026-10-04",
        "source_object": f"raw/2026-10-04/{eval_filename}",
        "book": "betrivers",
        "player": "Test Player",
        "player_norm": "test player",
        "raw_market_key": "player_receiving_yards",
        "raw_point": 22.5,
        "raw_name": "Yes",
        "entry_key": "betrivers|test player|player_receiving_yards|22.5|Yes",
    }
    labels_file.write_text(json.dumps(label_entry) + "\n", encoding="utf-8")

    out_file = tmp_path / "sidecar.jsonl"
    summary = build_sidecar(labels_file, raw_dir, out_file)

    assert summary["entries"] == 1
    assert summary["events"] == 1
    assert summary["warnings"] == 0
    assert summary["tuple_absent_in_eval_snapshot"] == 0

    assert out_file.exists()
    sidecar_row = json.loads(out_file.read_text(encoding="utf-8").strip())
    assert sidecar_row["entry_key"] == label_entry["entry_key"]
    assert sidecar_row["eval_ts"] == "2026-10-04T12:30:00"
    # Earlier + eval included; future excluded -> count is 2
    assert sidecar_row["features"]["snapshot_count"] == 2
    assert sidecar_row["features"]["event_snapshot_count"] == 2


# ---------------------------------------------------------------------------
# 8. run_eval with and without sidecar
# ---------------------------------------------------------------------------


def test_run_eval_stat_agreement_and_sidecar_status(tmp_path: Path):
    """run_eval includes stat_agreement gate bar and handles sidecar present/absent."""
    labels_file = tmp_path / "labels.jsonl"
    answers_file = tmp_path / "answers.jsonl"
    results_file = tmp_path / "eval_results.json"

    entry = {
        "book": "draftkings",
        "player": "Test Player",
        "player_norm": "test player",
        "event_id": "event_123",
        "raw_market_key": "player_rec_yds",
        "raw_description": "Test Player",
        "raw_name": "Over 44.5",
        "raw_point": 44.5,
        "raw_price": -110,
        "raw_price_side_info": {"sides_present": ["over", "under"]},
        "parser_candidate_stat": "rec_yds",
        "cross_book_main_point": 44.5,
        "points_for_book_player": [44.5],
        "label": {"canonical_stat": "rec_yds", "rung": 44.5, "scope": "main", "side": "over"},
        "ambiguity_class": "twosided_offmain_rung",
    }
    labels_file.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    ekey = make_entry_key(entry)

    cache = AnswersCache(answers_file)
    cache.store(
        ekey,
        {
            "canonical_stat": {"choice": "rec_yds"},
            "scope_is_milestone": {"noul": 0.0},
            "scope_unknown": {"noul": 0.0},
        },
    )

    # 1. Run without sidecar -> absent
    res_absent = run_eval(
        labels_path=labels_file,
        answers_path=answers_file,
        results_path=results_file,
        sidecar_path=tmp_path / "nonexistent.jsonl",
    )
    assert res_absent["snapshot_history"] == "absent"
    assert "stat_agreement" in res_absent["bars"]
    assert res_absent["bars"]["stat_agreement"]["bar"] == 0.99
    assert res_absent["bars"]["stat_agreement"]["passed"] is True

    # 2. Run with sidecar -> present
    sidecar_file = tmp_path / "sidecar.jsonl"
    sidecar_file.write_text(
        json.dumps({
            "entry_key": ekey,
            "eval_ts": "2026-10-04T12:00:00",
            "features": {"snapshot_count": 5, "sibling_full_game": False, "sibling_period_scoped": False, "same_book_distinct_full_game_market": False},
        }) + "\n",
        encoding="utf-8",
    )
    res_present = run_eval(
        labels_path=labels_file,
        answers_path=answers_file,
        results_path=results_file,
        sidecar_path=sidecar_file,
    )
    assert res_present["snapshot_history"] == "present"
