"""Offline hermetic tests for Settlement Rules Auditor (ev/settlement_auditor.py).

Zero live Decisions API calls: uses a fake client recording calls and returning
canned BatchResult-like responses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

from ev.decisions_common import MAX_INPUT_TOKENS
from ev.decisions_evals.eval_settlement import (
    adapt_entry_answers,
    evaluate_settlement,
    score_settlement_eval,
)
from ev.settlement_auditor import (
    DIMENSION_INSTRUCTIONS,
    DIMENSIONS,
    DIVERGENCE_THRESHOLD,
    REJ_CODE,
    audit_entries,
    build_entry_input,
    build_entry_questions,
    gate_entry,
)


@dataclass
class FakeBatchResult:
    """Canned BatchResult-like object with .ok and .answers."""

    ok: bool = True
    answers: dict[str, Any] = field(default_factory=dict)
    abstained: bool = False
    abstention_reason: Optional[str] = None


class FakeClient:
    """Fake Decisions client recording calls and returning canned responses."""

    def __init__(
        self,
        ok: bool = True,
        canned_probability: float = 0.05,
        dimension_probabilities: Optional[dict[str, float]] = None,
        custom_answers: Optional[dict[str, Any]] = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.ok = ok
        self.canned_probability = canned_probability
        self.dimension_probabilities = dimension_probabilities or {}
        self.custom_answers = custom_answers

    def call(self, input_text: str, questions: Mapping[str, Any]) -> FakeBatchResult:
        self.calls.append({
            "input_text": input_text,
            "questions": dict(questions),
        })
        if not self.ok:
            return FakeBatchResult(
                ok=False,
                answers={},
                abstained=True,
                abstention_reason="safety_filter",
            )

        if self.custom_answers is not None:
            return FakeBatchResult(
                ok=True,
                answers=dict(self.custom_answers),
                abstained=False,
            )

        # Build responses for wire questions
        answers = {}
        for wire_key in questions:
            dim_matched = None
            for dim in DIMENSIONS:
                if wire_key.endswith(f"__{dim}"):
                    dim_matched = dim
                    break

            if dim_matched and dim_matched in self.dimension_probabilities:
                p = self.dimension_probabilities[dim_matched]
            else:
                p = self.canned_probability

            answers[wire_key] = {"type": "noul", "noul": p, "confidence": 0.95}

        return FakeBatchResult(ok=True, answers=answers, abstained=False)


def _make_sample_entry(
    entry_id: str = "SA-TEST-001",
    venue: str = "DraftKings",
    venue_type: str = "book",
    sport: str = "MLB",
    market: str = "batter_props",
    canonical_text: str = "Canonical settlement: starter must start and have 1 PA.",
    rule_text: str = "Venue settlement: starter must start and have 1 PA.",
) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "venue": venue,
        "venue_type": venue_type,
        "sport": sport,
        "market": market,
        "canonical_text": canonical_text,
        "rule_text": rule_text,
        "divergent_dimensions": [],
    }


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


def test_one_underlying_call_for_many_entries():
    """Verify that multiple entries are batched into exactly ONE underlying call."""
    entries = [_make_sample_entry(entry_id=f"SA-{i:03d}") for i in range(1, 6)]
    fake = FakeClient(ok=True, canned_probability=0.02)

    report = audit_entries(entries, client=fake)

    # Exactly one underlying call
    assert len(fake.calls) == 1
    call = fake.calls[0]

    # Input text combines all 5 entries
    for entry in entries:
        assert entry["entry_id"] in call["input_text"]

    # 5 entries * 5 dimensions = 25 wire questions
    assert len(call["questions"]) == 25
    assert len(report["results"]) == 5
    assert len(report["divergence_report"]["consistent_entries"]) == 5
    assert len(report["divergence_report"]["quarantined_entries"]) == 0


def test_threshold_boundary_quarantines_at_20_and_not_at_19():
    """Verify divergence threshold: 0.20 quarantines, 0.19 does not."""
    entry = _make_sample_entry(entry_id="SA-BOUNDARY")

    # 1. Dimension at 0.20 -> flagged, quarantined
    client_20 = FakeClient(
        ok=True,
        canned_probability=0.01,
        dimension_probabilities={"overtime_included": 0.20},
    )
    report_20 = audit_entries([entry], client=client_20)
    res_20 = report_20["results"][0]
    assert res_20["disposition"] == "quarantine"
    assert res_20["flags"] == ["overtime_included"]
    assert res_20["unresolved"] is False
    assert report_20["divergence_report"]["quarantined_entries"] == ["SA-BOUNDARY"]

    # 2. Dimension at 0.19 -> not flagged, consistent
    client_19 = FakeClient(
        ok=True,
        canned_probability=0.01,
        dimension_probabilities={"overtime_included": 0.19},
    )
    report_19 = audit_entries([entry], client=client_19)
    res_19 = report_19["results"][0]
    assert res_19["disposition"] == "consistent"
    assert res_19["flags"] == []
    assert res_19["unresolved"] is False
    assert report_19["divergence_report"]["consistent_entries"] == ["SA-BOUNDARY"]


def test_missing_answer_causes_unresolved_quarantine():
    """Verify fail closed: missing or unparseable answer marks unresolved and quarantines."""
    entry = _make_sample_entry(entry_id="SA-MISSING")

    # ItemBatch tags the first item as i001
    custom_answers = {
        f"i001_SA-MISSING__{dim}": {"type": "noul", "noul": 0.05}
        for dim in DIMENSIONS
    }
    # Delete one dimension to simulate a missing answer
    del custom_answers["i001_SA-MISSING__tie_push_grading"]

    client = FakeClient(ok=True, custom_answers=custom_answers)
    report = audit_entries([entry], client=client)
    res = report["results"][0]

    assert res["unresolved"] is True
    assert res["disposition"] == "quarantine"
    assert res["dimensions"]["tie_push_grading"] is None
    assert "SA-MISSING" in report["divergence_report"]["unresolved_entries"]
    assert "SA-MISSING" in report["divergence_report"]["quarantined_entries"]


def test_malformed_non_float_answer_causes_unresolved_quarantine():
    """Verify non-float answer cannot parse as noul and produces unresolved quarantine."""
    entry = _make_sample_entry(entry_id="SA-NON-FLOAT")

    custom_answers = {
        f"i001_SA-NON-FLOAT__{dim}": {"type": "noul", "noul": 0.05}
        for dim in DIMENSIONS
    }
    # Put a non-numeric string value for one dimension
    custom_answers["i001_SA-NON-FLOAT__stat_source_mismatch"] = {
        "type": "noul",
        "noul": "not_a_probability",
    }

    client = FakeClient(ok=True, custom_answers=custom_answers)
    report = audit_entries([entry], client=client)
    res = report["results"][0]

    assert res["unresolved"] is True
    assert res["disposition"] == "quarantine"
    assert res["dimensions"]["stat_source_mismatch"] is None


def test_batch_abstention_quarantines_all():
    """Verify that when the underlying call abstains (ok False), all entries quarantine."""
    entries = [_make_sample_entry(entry_id=f"SA-ABSTAIN-{i}") for i in range(3)]
    client = FakeClient(ok=False)

    report = audit_entries(entries, client=client)

    assert len(report["results"]) == 3
    for res in report["results"]:
        assert res["disposition"] == "quarantine"
        assert res["unresolved"] is True
        assert res["flags"] == []
        assert res["reason"] == "batch_abstained"
        assert all(v is None for v in res["dimensions"].values())

    div_rep = report["divergence_report"]
    assert div_rep["quarantined_entries"] == ["SA-ABSTAIN-0", "SA-ABSTAIN-1", "SA-ABSTAIN-2"]
    assert div_rep["unresolved_entries"] == ["SA-ABSTAIN-0", "SA-ABSTAIN-1", "SA-ABSTAIN-2"]
    assert div_rep["consistent_entries"] == []


def test_proposed_matrix_marks_divergent_and_canonical_correctly():
    """Verify proposed_matrix marks DIVERGENT, CANONICAL, and UNRESOLVED correctly."""
    entry_clean = _make_sample_entry(entry_id="SA-CLEAN", venue="BookA", sport="NBA", market="points")
    entry_divergent = _make_sample_entry(entry_id="SA-DIV", venue="BookB", sport="MLB", market="props")

    # BookA has clean answers (0.01), BookB has overtime_included divergent (0.85)
    custom_answers = {
        f"i001_SA-CLEAN__{dim}": {"noul": 0.01} for dim in DIMENSIONS
    }
    for dim in DIMENSIONS:
        p = 0.85 if dim == "overtime_included" else 0.02
        custom_answers[f"i002_SA-DIV__{dim}"] = {"noul": p}

    client = FakeClient(ok=True, custom_answers=custom_answers)
    report = audit_entries([entry_clean, entry_divergent], client=client)
    matrix = report["proposed_matrix"]

    assert len(matrix) == 2

    # Clean entry
    row_clean = matrix[0]
    assert row_clean["venue"] == "BookA"
    assert row_clean["sport"] == "NBA"
    assert row_clean["market"] == "points"
    for dim in DIMENSIONS:
        assert row_clean[dim] == "CANONICAL"

    # Divergent entry
    row_div = matrix[1]
    assert row_div["venue"] == "BookB"
    assert row_div["sport"] == "MLB"
    assert row_div["market"] == "props"
    assert row_div["overtime_included"] == "DIVERGENT"
    for dim in DIMENSIONS:
        if dim != "overtime_included":
            assert row_div[dim] == "CANONICAL"


def test_proposed_matrix_marks_unresolved():
    """Verify proposed_matrix marks unflagged dimensions as UNRESOLVED on an unresolved entry."""
    entry = _make_sample_entry(entry_id="SA-UNRES", venue="BookC", sport="NFL", market="totals")

    # Omit stat_source_mismatch
    custom_answers = {
        f"i001_SA-UNRES__{dim}": {"noul": 0.01}
        for dim in DIMENSIONS
        if dim != "stat_source_mismatch"
    }

    client = FakeClient(ok=True, custom_answers=custom_answers)
    report = audit_entries([entry], client=client)
    matrix = report["proposed_matrix"]

    assert len(matrix) == 1
    row = matrix[0]
    assert row["venue"] == "BookC"
    # Since unresolved is True and no dimension is flagged, all are UNRESOLVED
    for dim in DIMENSIONS:
        assert row[dim] == "UNRESOLVED"


def test_shadow_mode_applied_false_and_live_mode_applied_true():
    """Verify applied flag is False in shadow mode and True in live mode."""
    entry = _make_sample_entry(entry_id="SA-MODE")
    client = FakeClient(ok=True, canned_probability=0.05)

    # 1. Explicit shadow
    report_shadow = audit_entries([entry], client=client, mode="shadow")
    assert report_shadow["mode"] == "shadow"
    assert report_shadow["results"][0]["applied"] is False

    # 2. Explicit live
    report_live = audit_entries([entry], client=client, mode="live")
    assert report_live["mode"] == "live"
    assert report_live["results"][0]["applied"] is True


def test_token_budget_exceeded_raises_value_error():
    """Verify ValueError is raised when input tokens exceed 90% of MAX_INPUT_TOKENS."""
    huge_text = "x" * int(MAX_INPUT_TOKENS * 3.5)
    huge_entry = _make_sample_entry(canonical_text=huge_text)
    client = FakeClient(ok=True)

    with pytest.raises(ValueError, match="exceeds 90% of MAX_INPUT_TOKENS"):
        audit_entries([huge_entry], client=client)


def test_fail_closed_when_client_construction_fails(monkeypatch):
    """Verify client=None catches DecisionsConfigError when GEMINI_API_KEY is unset."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    entry = _make_sample_entry(entry_id="SA-NOKEY")

    report = audit_entries([entry], client=None)

    assert len(report["results"]) == 1
    res = report["results"][0]
    assert res["disposition"] == "quarantine"
    assert res["unresolved"] is True
    assert res["reason"] == "batch_abstained"


def test_decision_logging_appends_records(tmp_path):
    """Verify audit_entries appends structured JSONL records when log_path is provided."""
    log_file = tmp_path / "settlement_decisions.jsonl"
    entry = _make_sample_entry(entry_id="SA-LOGGED", venue="FanDuel", sport="MLB")
    client = FakeClient(
        ok=True,
        dimension_probabilities={"postponement_cutoff_mismatch": 0.45},
    )

    report = audit_entries([entry], client=client, mode="shadow", log_path=log_file)
    assert log_file.is_file()

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])

    assert record["route"] == "settlement_auditor"
    assert record["item_id"] == "SA-LOGGED"
    assert record["decision"] == "quarantine"
    assert record["mode"] == "shadow"
    assert record["applied"] is False
    assert record["venue"] == "FanDuel"
    assert record["sport"] == "MLB"
    assert "postponement_cutoff_mismatch" in record["flags"]


def test_build_entry_questions_and_input_shape():
    """Verify build_entry_questions and build_entry_input formatting and keys."""
    entry = _make_sample_entry(
        entry_id="SA-SHAPE",
        venue="DraftKings",
        venue_type="book",
        sport="MLB",
        market="batter_props",
        canonical_text="Canonical rules here",
        rule_text="Venue rules here",
    )

    input_text = build_entry_input(entry)
    assert "Entry ID: SA-SHAPE" in input_text
    assert "Venue: DraftKings" in input_text
    assert "Canonical settlement rules:\nCanonical rules here" in input_text
    assert "Venue house rules:\nVenue rules here" in input_text

    questions = build_entry_questions(entry)
    assert len(questions) == 5
    for dim in DIMENSIONS:
        qid = f"SA-SHAPE__{dim}"
        assert qid in questions
        assert questions[qid]["type"] == "noul"
        assert questions[qid]["instructions"] == DIMENSION_INSTRUCTIONS[dim]


def test_gate_entry_standalone():
    """Verify gate_entry handles dimension names and prefix forms."""
    entry = _make_sample_entry(entry_id="SA-GATE")

    # Bare dimension keys
    answers_bare = {
        "overtime_included": 0.25,
        "dnp_or_starter_rule": 0.05,
        "stat_source_mismatch": 0.01,
        "postponement_cutoff_mismatch": 0.02,
        "tie_push_grading": 0.03,
    }
    res_bare = gate_entry(entry, answers_bare)
    assert res_bare["flags"] == ["overtime_included"]
    assert res_bare["disposition"] == "quarantine"
    assert res_bare["unresolved"] is False

    # Prefixed dimension keys
    answers_prefixed = {
        f"SA-GATE__{dim}": 0.05 for dim in DIMENSIONS
    }
    res_prefixed = gate_entry(entry, answers_prefixed)
    assert res_prefixed["flags"] == []
    assert res_prefixed["disposition"] == "consistent"
    assert res_prefixed["unresolved"] is False


def test_adapt_entry_answers():
    """Verify adapt_entry_answers strips the f'{entry_id}__' prefix."""
    raw = {
        "SA-001__overtime_included": {"noul": 0.05},
        "SA-001__dnp_or_starter_rule": {"noul": 0.85},
        "other_key": {"noul": 0.10},
    }
    adapted = adapt_entry_answers("SA-001", raw)
    assert adapted["overtime_included"] == {"noul": 0.05}
    assert adapted["dnp_or_starter_rule"] == {"noul": 0.85}
    assert adapted["other_key"] == {"noul": 0.10}


def test_score_settlement_eval_metrics():
    """Verify score_settlement_eval calculates recall, false flags, precision, and clean rates."""
    entries = [
        {"entry_id": "E1", "divergent_dimensions": ["overtime_included"]},
        {"entry_id": "E2", "divergent_dimensions": ["dnp_or_starter_rule"]},
        {"entry_id": "E3", "divergent_dimensions": []},  # clean
        {"entry_id": "E4", "divergent_dimensions": []},  # clean
    ]

    gate_results = [
        # E1: correctly flagged on overtime_included -> TP
        {
            "entry_id": "E1",
            "flags": ["overtime_included"],
            "disposition": "quarantine",
            "unresolved": False,
        },
        # E2: missed flag (FN) -> consistent
        {
            "entry_id": "E2",
            "flags": [],
            "disposition": "consistent",
            "unresolved": False,
        },
        # E3: clean and consistent -> TN
        {
            "entry_id": "E3",
            "flags": [],
            "disposition": "consistent",
            "unresolved": False,
        },
        # E4: clean but falsely flagged on tie_push_grading -> FP
        {
            "entry_id": "E4",
            "flags": ["tie_push_grading"],
            "disposition": "quarantine",
            "unresolved": False,
        },
    ]

    scores = score_settlement_eval(entries, gate_results)

    # Entry-level:
    # Divergent entries: E1, E2 (total 2). Quarantined divergent: E1 (TP=1). Missed divergent: E2 (FN=1).
    # Clean entries: E3, E4 (total 2). Quarantined clean: E4 (FP=1). Consistent clean: E3 (TN=1).
    # Precision: TP / (TP + FP) = 1 / 2 = 0.50
    # Recall: TP / (TP + FN) = 1 / 2 = 0.50
    # Clean false-flag rate: FP (clean quarantined) / Clean total = 1 / 2 = 0.50
    el = scores["entry_level"]
    assert el["tp"] == 1
    assert el["fp"] == 1
    assert el["fn"] == 1
    assert el["tn"] == 1
    assert el["precision"] == 0.50
    assert el["recall"] == 0.50
    assert el["clean_false_flag_rate"] == 0.50

    # Per-dimension:
    # overtime_included: planted=1, flagged_planted=1 -> recall=1.0, unplanted=3, false_flags=0 -> rate=0.0
    ot = scores["per_dimension"]["overtime_included"]
    assert ot["planted"] == 1
    assert ot["flagged_planted"] == 1
    assert ot["recall"] == 1.0
    assert ot["false_flags"] == 0

    # dnp_or_starter_rule: planted=1, flagged_planted=0 -> recall=0.0
    dnp = scores["per_dimension"]["dnp_or_starter_rule"]
    assert dnp["planted"] == 1
    assert dnp["flagged_planted"] == 0
    assert dnp["recall"] == 0.0

    # tie_push_grading: planted=0 -> recall=1.0, unplanted=4, false_flags=1 -> rate=0.25
    tie = scores["per_dimension"]["tie_push_grading"]
    assert tie["planted"] == 0
    assert tie["false_flags"] == 1
    assert tie["unplanted"] == 4
    assert tie["false_flag_rate"] == 0.25


def test_evaluate_settlement_end_to_end_offline(tmp_path):
    """Verify evaluate_settlement runs end-to-end with fake client, writes results file, and caches answers."""
    entries_file = tmp_path / "entries.jsonl"
    answers_file = tmp_path / "answers.jsonl"
    results_file = tmp_path / "eval_results.json"

    entries = [
        _make_sample_entry(entry_id="SA-001", canonical_text="c1", rule_text="r1"),
        _make_sample_entry(entry_id="SA-002", canonical_text="c2", rule_text="r2"),
    ]
    entries[0]["divergent_dimensions"] = ["overtime_included"]
    entries[1]["divergent_dimensions"] = []

    with open(entries_file, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")

    # Fake client with answers: SA-001 flagged on overtime_included (0.85), SA-002 clean (0.02)
    custom_answers = {
        f"i001_SA-001__{dim}": {"noul": 0.85 if dim == "overtime_included" else 0.02}
        for dim in DIMENSIONS
    }
    custom_answers.update({
        f"i002_SA-002__{dim}": {"noul": 0.02}
        for dim in DIMENSIONS
    })
    fake_client = FakeClient(ok=True, custom_answers=custom_answers)

    report = evaluate_settlement(
        entries_path=entries_file,
        cache_path=answers_file,
        out_path=results_file,
        client=fake_client,
    )

    # Answers file written
    assert answers_file.is_file()
    # Results file written
    assert results_file.is_file()

    # Results match expected scoring
    assert report["summary"]["total_entries"] == 2
    assert report["entry_level"]["recall"] == 1.0
    assert report["entry_level"]["precision"] == 1.0
    assert report["entry_level"]["clean_false_flag_rate"] == 0.0
    assert report["per_dimension"]["overtime_included"]["recall"] == 1.0

