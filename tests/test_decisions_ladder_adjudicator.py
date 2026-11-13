"""Tests for the N1 ladder identity adjudicator route and evaluation harness.

Hermetic and offline: all tests use fake/stub clients and temporary caches with
zero live Decisions API network calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

import pytest

from ev.decisions_client import BatchResult
from ev.decisions_evals.common import AnswersCache
from ev.decisions_evals.eval_ladder import (
    CachedDecisionsClient,
    parse_block_entry_key,
    run_eval,
)
from ev.decisions_ladder_adjudicator import (
    adjudicate_entries,
    build_parser_view,
    candidate_stats_for,
    group_key,
    main as adjudicator_main,
    make_entry_key,
)


class FakeDecisionsClient:
    """Offline Decisions client for test verification."""

    def __init__(
        self,
        canned_answers: Mapping[str, Any] | None = None,
        abstain: bool = False,
        abstention_reason: str = "safety_filter",
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.canned_answers = dict(canned_answers or {})
        self.abstain = abstain
        self.abstention_reason = abstention_reason

    def call(self, input_text: str, questions: Mapping[str, Any]) -> BatchResult:
        self.calls.append({
            "input_text": input_text,
            "questions": dict(questions),
        })
        if self.abstain:
            return BatchResult.abstain(self.abstention_reason)

        answers: dict[str, Any] = {}
        for wire_key in questions:
            if wire_key in self.canned_answers:
                answers[wire_key] = self.canned_answers[wire_key]
            elif wire_key.endswith("canonical_stat"):
                answers[wire_key] = {"choice": "rec_yds"}
            elif wire_key.endswith("scope_is_milestone"):
                answers[wire_key] = {"noul": 0.0, "probability": 0.0}
            elif wire_key.endswith("scope_unknown"):
                answers[wire_key] = {"noul": 0.0, "probability": 0.0}
            else:
                answers[wire_key] = {"noul": 0.0}
        return BatchResult(answers=answers, abstained=False)


def _sample_deterministic_entry(**kw: Any) -> dict[str, Any]:
    base = {
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
    }
    base.update(kw)
    return base


def _sample_ambiguous_entry(**kw: Any) -> dict[str, Any]:
    base = {
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
        "points_for_book_player": [44.5, 64.5],
        "cross_book_main_point": 44.5,
        "candidate_stats": ["rec_yds"],
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_deterministic_entry_never_reaches_client():
    """Deterministic entry (mapped key, two-sided, at main point) never calls Decisions."""
    entry = _sample_deterministic_entry()
    fake_client = FakeDecisionsClient()

    res = adjudicate_entries([entry], client=fake_client)

    assert len(fake_client.calls) == 0
    assert res["summary"]["total"] == 1
    assert res["summary"]["deterministic"] == 1
    assert res["summary"]["ambiguous"] == 0
    assert res["summary"]["accepted_shadow"] == 0
    assert res["summary"]["quarantined"] == 0

    dec = res["decisions"][0]
    assert dec["status"] == "deterministic"
    assert dec["applied"] is False
    assert dec["identity"] == {
        "stat": "rec_yds",
        "rung": 44.5,
        "side": "over",
        "scope": "main",
    }


def test_ambiguous_entry_unmapped_key_is_batched():
    """Ambiguous entry (unmapped key) is identified as ambiguous and batched to Decisions."""
    entry = {
        "book": "pinnacle",
        "player": "Cam Ward",
        "player_norm": "cam ward",
        "event_id": "event_456",
        "raw_market_key": "player_touchdown_passes",
        "raw_description": "Cam Ward",
        "raw_name": "Over 0.5",
        "raw_point": 0.5,
        "raw_price": -115,
        "raw_price_side_info": {"sides_present": ["over", "under"]},
        "parser_candidate_stat": None,
        "cross_book_main_point": 0.5,
        "candidate_stats": ["pass_tds"],
    }
    fake_client = FakeDecisionsClient()

    res = adjudicate_entries([entry], client=fake_client)

    assert len(fake_client.calls) == 1
    assert res["summary"]["total"] == 1
    assert res["summary"]["ambiguous"] == 1


def test_ambiguous_entry_accepted_shadow():
    """Ambiguous entry satisfying all gate conditions achieves accepted_shadow status."""
    entry = _sample_ambiguous_entry()
    fake_client = FakeDecisionsClient(
        canned_answers={
            "i001_canonical_stat": {"choice": "rec_yds"},
            "i001_scope_is_milestone": {"noul": 0.01, "probability": 0.01},
            "i001_scope_unknown": {"noul": 0.02, "probability": 0.02},
        }
    )

    res = adjudicate_entries([entry], client=fake_client)

    assert len(fake_client.calls) == 1
    assert res["summary"]["total"] == 1
    assert res["summary"]["ambiguous"] == 1
    assert res["summary"]["accepted_shadow"] == 1
    assert res["summary"]["quarantined"] == 0

    dec = res["decisions"][0]
    assert dec["status"] == "accepted_shadow"
    assert dec["applied"] is False
    assert dec["identity"] == {
        "stat": "rec_yds",
        "rung": 64.5,
        "side": "over",
        "scope": "alternate",
    }


def test_acceptance_requires_stat_agreement():
    """Model picking a stat different from parser candidate stat is quarantined."""
    entry = _sample_ambiguous_entry(
        raw_market_key="player_receiving_yards",
        parser_candidate_stat="rec_yds",
        points_for_book_player=[20.5, 30.5],
        cross_book_main_point=20.5,
        raw_point=30.5,
    )
    fake_client = FakeDecisionsClient(
        canned_answers={
            "i001_canonical_stat": {"choice": "rush_yds"},  # mismatch
            "i001_scope_is_milestone": {"noul": 0.0, "probability": 0.0},
            "i001_scope_unknown": {"noul": 0.0, "probability": 0.0},
        }
    )

    res = adjudicate_entries([entry], client=fake_client)

    assert res["summary"]["quarantined"] == 1
    dec = res["decisions"][0]
    assert dec["status"] == "quarantined"
    assert dec["identity"] is None
    assert any("choice_stat_mismatch" in r for r in dec["reasons"])


def test_p_unknown_above_threshold_quarantines():
    """P(unknown) > 0.10 fails closed and quarantines."""
    entry = _sample_ambiguous_entry(
        raw_market_key="player_receiving_yards",
        parser_candidate_stat="rec_yds",
        points_for_book_player=[20.5, 30.5],
        cross_book_main_point=20.5,
        raw_point=30.5,
    )
    fake_client = FakeDecisionsClient(
        canned_answers={
            "i001_canonical_stat": {"choice": "rec_yds"},
            "i001_scope_is_milestone": {"noul": 0.0, "probability": 0.0},
            "i001_scope_unknown": {"noul": 0.20, "probability": 0.20},  # > 0.10
        }
    )

    res = adjudicate_entries([entry], client=fake_client)

    assert res["summary"]["quarantined"] == 1
    dec = res["decisions"][0]
    assert dec["status"] == "quarantined"
    assert any("unknown_above_threshold" in r for r in dec["reasons"])


def test_scope_noul_low_quarantines():
    """Scope confidence noul < 0.95 fails closed and quarantines."""
    entry = _sample_ambiguous_entry(
        raw_market_key="player_receiving_yards",
        parser_candidate_stat="rec_yds",
        points_for_book_player=[20.5, 30.5],
        cross_book_main_point=20.5,
        raw_point=30.5,
    )
    fake_client = FakeDecisionsClient(
        canned_answers={
            "i001_canonical_stat": {"choice": "rec_yds"},
            # noul = max(0.60, 0.40) = 0.60 < 0.95
            "i001_scope_is_milestone": {"noul": 0.60, "probability": 0.60},
            "i001_scope_unknown": {"noul": 0.02, "probability": 0.02},
        }
    )

    res = adjudicate_entries([entry], client=fake_client)

    assert res["summary"]["quarantined"] == 1
    dec = res["decisions"][0]
    assert dec["status"] == "quarantined"
    assert any("noul_below_threshold" in r for r in dec["reasons"])


def test_abstention_quarantines_all_ambiguous():
    """Client abstention quarantines all ambiguous entries with batch_abstained reason."""
    entry1 = _sample_ambiguous_entry(raw_point=0.5)
    entry2 = _sample_ambiguous_entry(raw_point=1.5)
    det_entry = _sample_deterministic_entry()
    fake_client = FakeDecisionsClient(abstain=True, abstention_reason="safety_block")

    res = adjudicate_entries([entry1, det_entry, entry2], client=fake_client)

    assert res["summary"]["total"] == 3
    assert res["summary"]["deterministic"] == 1
    assert res["summary"]["ambiguous"] == 2
    assert res["summary"]["quarantined"] == 2

    assert res["decisions"][0]["status"] == "quarantined"
    assert res["decisions"][0]["reasons"] == ["batch_abstained"]
    assert res["decisions"][1]["status"] == "deterministic"
    assert res["decisions"][2]["status"] == "quarantined"
    assert res["decisions"][2]["reasons"] == ["batch_abstained"]


def test_client_construction_failure_abstains(monkeypatch: pytest.MonkeyPatch):
    """When client is None and GEMINI_API_KEY is not set, abstains rather than crashing."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    entry = _sample_ambiguous_entry()

    res = adjudicate_entries([entry], client=None)

    assert res["summary"]["quarantined"] == 1
    assert res["decisions"][0]["status"] == "quarantined"
    assert res["decisions"][0]["reasons"] == ["batch_abstained"]


def test_applied_is_false_even_with_mode_live():
    """applied is False always, even when mode is explicitly set to 'live'."""
    entry = _sample_deterministic_entry()
    res = adjudicate_entries([entry], mode="live")

    dec = res["decisions"][0]
    assert dec["applied"] is False
    assert dec["mode"] == "live"


def test_parser_view_grouping():
    """build_parser_view correctly gathers distinct points and detects two_sidedness."""
    e1 = {
        "book": "novig",
        "player": "Test WR",
        "player_norm": "test wr",
        "event_id": "ev1",
        "raw_market_key": "player_receiving_yards",
        "raw_point": 40.5,
        "raw_price_side_info": {"sides_present": ["over"]},
    }
    e2 = {
        "book": "novig",
        "player": "Test WR",
        "player_norm": "test wr",
        "event_id": "ev1",
        "raw_market_key": "player_receiving_yards",
        "raw_point": 50.5,
        "raw_price_side_info": {"sides_present": ["under"]},
    }
    e3 = {
        "book": "novig",
        "player": "Test WR",
        "player_norm": "test wr",
        "event_id": "ev1",
        "raw_market_key": "player_receiving_yards",
        "raw_point": 60.5,
        "raw_price_side_info": {"sides_present": ["over", "under"]},
        "cross_book_main_point": 50.5,
    }
    group = [e1, e2, e3]

    view = build_parser_view(e1, group)

    assert view["candidate_stat"] == "rec_yds"
    assert view["points_for_book_player"] == [40.5, 50.5, 60.5]
    assert view["two_sided"] is True
    assert view["cross_book_main_point"] is None

    # Candidate stats collection
    c_stats = candidate_stats_for(e1, group)
    assert c_stats == ["rec_yds"]


def test_candidate_stats_fallback():
    """With no resolvable candidate, the fallback is the full canonical stat
    vocabulary (never a single-option choice, which the API rejects)."""
    e = {"raw_market_key": "unmapped_crazy_key"}
    stats = candidate_stats_for(e, [])
    assert "unknown" not in stats and len(stats) >= 2 and "pass_yds" in stats


def test_token_guard_raises_value_error():
    """Token estimation guard raises ValueError when input tokens exceed 90% limit."""
    entry = _sample_ambiguous_entry()
    with patch("ev.decisions_ladder_adjudicator.estimate_tokens", return_value=230_000):
        with pytest.raises(ValueError, match="exceeds 90% of MAX_INPUT_TOKENS"):
            adjudicate_entries([entry], client=FakeDecisionsClient())


def test_log_path_appends_records(tmp_path: Path):
    """Passing log_path records structured JSONL decisions."""
    log_file = tmp_path / "decisions.jsonl"
    entry = _sample_deterministic_entry()

    adjudicate_entries([entry], log_path=log_file)

    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["route"] == "ladder_adjudicator"
    assert rec["applied"] is False
    assert rec["decision"]["status"] == "deterministic"


def test_cli_runner(tmp_path: Path):
    """Test CLI runner with --entries and --out arguments."""
    entries_file = tmp_path / "entries.jsonl"
    out_file = tmp_path / "out.json"
    entry = _sample_deterministic_entry()
    entries_file.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    adjudicator_main_args = [
        "prog",
        "--entries",
        str(entries_file),
        "--out",
        str(out_file),
        "--mode",
        "shadow",
    ]
    with patch("sys.argv", adjudicator_main_args):
        adjudicator_main()

    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert "decisions" in data
    assert "summary" in data
    assert data["summary"]["deterministic"] == 1


# ---------------------------------------------------------------------------
# Evaluation harness tests
# ---------------------------------------------------------------------------


def test_parse_block_entry_key():
    """parse_block_entry_key parses structured context block to entry_key."""
    block = (
        "Book: betrivers\n"
        "Player: Laquon Treadwell\n"
        "Raw market key: player_receiving_yards\n"
        "Outcome name: Yes\n"
        "Outcome description: Laquon Treadwell\n"
        "Raw point: 22.5\n"
    )
    ekey = parse_block_entry_key(block)
    assert ekey == "betrivers|laquon treadwell|player_receiving_yards|22.5|Yes"


def test_cached_decisions_client_replay(tmp_path: Path):
    """CachedDecisionsClient serves cached answers through ItemBatch wire format."""
    cache_path = tmp_path / "answers.jsonl"
    cache = AnswersCache(cache_path)
    ekey = "betrivers|laquon treadwell|player_receiving_yards|22.5|Yes"
    cache.store(
        ekey,
        {
            "canonical_stat": {"choice": "rec_yds"},
            "scope_is_milestone": {"noul": 0.99},
            "scope_unknown": {"noul": 0.01},
        },
    )

    client = CachedDecisionsClient(cache)
    input_text = (
        "[i001]\n"
        "Book: betrivers\n"
        "Player: Laquon Treadwell\n"
        "Raw market key: player_receiving_yards\n"
        "Outcome name: Yes\n"
        "Outcome description: Laquon Treadwell\n"
        "Raw point: 22.5\n"
    )
    questions = {
        "i001_canonical_stat": {"type": "choice"},
        "i001_scope_is_milestone": {"type": "noul"},
        "i001_scope_unknown": {"type": "noul"},
    }
    result = client.call(input_text, questions)

    assert not result.abstained
    assert result.answers["i001_canonical_stat"]["choice"] == "rec_yds"
    assert result.answers["i001_scope_is_milestone"]["noul"] == 0.99
    assert result.answers["i001_scope_unknown"]["noul"] == 0.01


def test_run_eval_raises_when_uncached_and_no_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """run_eval raises RuntimeError when uncached items exist and GEMINI_API_KEY is not set."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    labels_file = tmp_path / "labels.jsonl"
    answers_file = tmp_path / "answers.jsonl"
    entry = _sample_ambiguous_entry()
    labels_file.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not set"):
        run_eval(labels_path=labels_file, answers_path=answers_file, results_path=None)


def test_run_eval_offline_mock(tmp_path: Path):
    """run_eval computes all metrics and checks bars correctly from cached answers."""
    labels_file = tmp_path / "labels.jsonl"
    answers_file = tmp_path / "answers.jsonl"
    results_file = tmp_path / "eval_results.json"

    entry = _sample_ambiguous_entry(
        raw_market_key="player_receiving_yards",
        raw_point=64.5,
        raw_name="Over 64.5",
        parser_candidate_stat="rec_yds",
        cross_book_main_point=44.5,
        points_for_book_player=[44.5, 64.5],
        label={"canonical_stat": "rec_yds", "rung": 64.5, "scope": "alternate", "side": "over"},
        ambiguity_class="twosided_offmain_rung",
    )
    labels_file.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    ekey = make_entry_key(entry)
    cache = AnswersCache(answers_file)
    cache.store(
        ekey,
        {
            "canonical_stat": {"choice": "rec_yds"},
            "scope_is_milestone": {"noul": 0.01},
            "scope_unknown": {"noul": 0.02},
        },
    )

    results = run_eval(
        labels_path=labels_file,
        answers_path=answers_file,
        results_path=results_file,
    )

    assert results_file.exists()
    assert results["total"] == 1
    assert results["accepted"] == 1
    assert results["false_canonicalization_rate"] == 0.0
    assert results["scope_disagreement_rate"] == 0.0
    assert results["quarantine_reduction"] == 1.0
    assert results["gate_passed"] is True


def test_candidate_stats_fallback_full_vocabulary_for_unmapped_key():
    from ev.decisions_ladder_adjudicator import candidate_stats_for

    entry = {"book": "fliff", "raw_market_key": "player_passing_interceptions",
             "parser_candidate_stat": None}
    stats = candidate_stats_for(entry, [entry])
    assert "interceptions" in stats and "pass_yds" in stats and "unknown" not in stats
    assert len(stats) >= 2  # the choice question must never be single-option
