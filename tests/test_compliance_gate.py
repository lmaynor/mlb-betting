"""Hermetic offline unit tests for publication compliance gate.

NO LIVE NETWORK CALLS: All tests use mocked transports or spy clients.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

import pytest

from ev.decisions_client import BatchResult, DecisionsClient, DecisionsConfigError
from ev.decisions_common import MAX_INPUT_TOKENS
from mlb_core.alerts.compliance_gate import (
    UNSUPPORTED_THRESHOLD,
    build_explanation_input,
    build_explanation_questions,
    build_social_input,
    build_social_questions,
    check_draft,
    check_drafts,
    extract_numbers,
    fallback_template,
    gate_explanation,
    gate_social,
    row_numbers,
    verify_numbers,
)


class SpyDecisionsClient:
    """Mock DecisionsClient that records calls and returns configured BatchResults."""

    def __init__(
        self,
        answers: Optional[dict[str, Any]] = None,
        abstained: bool = False,
        abstention_reason: Optional[str] = None,
    ) -> None:
        self.answers = answers or {}
        self.abstained = abstained
        self.abstention_reason = abstention_reason
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, input_text: str, questions: Mapping[str, Any]) -> BatchResult:
        self.calls.append((input_text, dict(questions)))
        if self.abstained:
            return BatchResult.abstain(self.abstention_reason or "test_timeout")
        return BatchResult(
            answers=dict(self.answers),
            abstained=False,
            raw={"answers": self.answers},
        )


def _sample_row() -> dict[str, Any]:
    return {
        "item_id": "E001",
        "player": "Gerrit Cole",
        "bet_type": "K_OVER_7.5",
        "book": "DraftKings",
        "odds": -115,
        "line": 7.5,
        "model_prob": 0.58,
        "edge": 0.062,
        "projection": 8.4,
        "factors": [
            "projected opposing lineup strikeout rate of 24.1%",
            "an umpire strike-zone tendency worth +3.2% called strikes",
            "a park strikeout factor of 1.04",
        ],
    }


# ---------------------------------------------------------------------------
# Number extraction and row number collection
# ---------------------------------------------------------------------------


def test_extract_numbers_various_formats():
    text = "Line 7.5 at -115, projection 8.4, +105 odds, 58% probability, edge 6.2%, .512 slugging, 1,000 bets."
    nums = extract_numbers(text)
    assert 7.5 in nums
    assert -115.0 in nums
    assert 8.4 in nums
    assert 105.0 in nums
    assert 58.0 in nums
    assert 6.2 in nums
    assert 0.512 in nums
    assert 1000.0 in nums


def test_extract_numbers_empty_and_invalid():
    assert extract_numbers("") == set()
    assert extract_numbers(None) == set()
    assert extract_numbers("No numbers here whatsoever") == set()


def test_row_numbers_collects_fields_and_renderings():
    row = _sample_row()
    r_nums = row_numbers(row)

    # Core numeric fields
    assert 7.5 in r_nums
    assert -115.0 in r_nums
    assert 115.0 in r_nums  # abs(odds)
    assert 8.4 in r_nums
    assert 0.58 in r_nums
    assert 58.0 in r_nums  # model_prob * 100
    assert 0.062 in r_nums
    assert any(abs(n - 6.2) < 0.001 for n in r_nums)  # edge * 100

    # Numbers from factors and bet_type
    assert 24.1 in r_nums
    assert 3.2 in r_nums
    assert 1.04 in r_nums


# ---------------------------------------------------------------------------
# Number verification tests
# ---------------------------------------------------------------------------


def test_number_verification_row_number_passes():
    row = _sample_row()
    text = "Gerrit Cole k over 7.5 projects to 8.4 versus a 7.5 line at -115 with 24.1% strikeout rate and 1.04 park factor."
    res = verify_numbers(text, row)
    assert res["ok"] is True
    assert res["unknown_numbers"] == []


def test_number_verification_foreign_number_fails():
    row = _sample_row()
    # 14 mph wind is an unattached / foreign number
    text = (
        "Gerrit Cole k over 7.5 projects to 8.4 versus a 7.5 line. "
        "Also helping the case: sustained 14 mph winds blowing in."
    )
    res = verify_numbers(text, row)
    assert res["ok"] is False
    assert 14.0 in res["unknown_numbers"]


def test_number_verification_percent_rendering_passes():
    row = _sample_row()  # model_prob = 0.58, edge = 0.062
    text = "Model probability 58% with 6.2% edge on line 7.5."
    res = verify_numbers(text, row)
    assert res["ok"] is True
    assert res["unknown_numbers"] == []


def test_number_verification_structural_number_fails_closed():
    row = _sample_row()
    # Structural "1" in "Game 1" fails closed as documented
    text = "Gerrit Cole k over 7.5 in Game 1 at -115."
    res = verify_numbers(text, row)
    assert res["ok"] is False
    assert 1.0 in res["unknown_numbers"]


# ---------------------------------------------------------------------------
# gate_explanation tests
# ---------------------------------------------------------------------------


def test_gate_explanation_hallucinated_rejects():
    item = {"item_id": "E051"}
    answers = {"factor_entailment": {"noul": 0.90}}
    res = gate_explanation(item, answers)
    assert res["rejected"] is True
    assert res["reason"] == "unsupported_claim"
    assert res["p_unsupported"] == 0.90


def test_gate_explanation_approves():
    item = {"item_id": "E001"}
    answers = {"factor_entailment": {"noul": 0.10}}
    res = gate_explanation(item, answers)
    assert res["rejected"] is False
    assert res["reason"] == "entailed"
    assert res["p_unsupported"] == 0.10


def test_gate_explanation_threshold_edge():
    item = {"item_id": "E001"}
    # 0.16 > 0.15 rejects
    res_high = gate_explanation(item, {"factor_entailment": {"noul": 0.16}})
    assert res_high["rejected"] is True
    assert res_high["reason"] == "unsupported_claim"

    # 0.15 <= 0.15 approves
    res_edge = gate_explanation(item, {"factor_entailment": {"noul": 0.15}})
    assert res_edge["rejected"] is False
    assert res_edge["reason"] == "entailed"


def test_gate_explanation_abstention_rejects():
    item = {"item_id": "E001"}
    # Empty answers map
    res_empty = gate_explanation(item, {})
    assert res_empty["rejected"] is True
    assert res_empty["reason"] == "entailment_abstained"
    assert res_empty["p_unsupported"] is None

    # None answer
    res_none = gate_explanation(item, {"factor_entailment": None})
    assert res_none["rejected"] is True
    assert res_none["reason"] == "entailment_abstained"


# ---------------------------------------------------------------------------
# gate_social tests
# ---------------------------------------------------------------------------


def test_gate_social_tout_pick_rejects():
    item = {"item_id": "D021"}
    answers = {
        "compliance_class": {
            "choice": "tout",
            "probabilities": {"tout": 0.95, "compliant": 0.05},
        }
    }
    res = gate_social(item, answers)
    assert res["rejected"] is True
    assert res["reason"] == "tout"
    assert res["pick"] == "tout"


def test_gate_social_clean_pick_approves():
    item = {"item_id": "D001"}
    answers = {
        "compliance_class": {
            "choice": "compliant",
            "probabilities": {"compliant": 0.92, "tout": 0.08},
        }
    }
    res = gate_social(item, answers)
    assert res["rejected"] is False
    assert res["reason"] == "compliant"
    assert res["pick"] == "compliant"


def test_gate_social_p_tout_probability_rejects():
    item = {"item_id": "D001"}
    # Even if choice is missing or weird, p_tout > 0.5 rejects
    answers = {
        "compliance_class": {
            "probabilities": {"tout": 0.65, "compliant": 0.35},
        }
    }
    res = gate_social(item, answers)
    assert res["rejected"] is True
    assert res["reason"] == "tout"


def test_gate_social_abstention_rejects():
    item = {"item_id": "D001"}
    res_empty = gate_social(item, {})
    assert res_empty["rejected"] is True
    assert res_empty["reason"] == "social_abstained"
    assert res_empty["pick"] is None


# ---------------------------------------------------------------------------
# fallback_template tests
# ---------------------------------------------------------------------------


def test_fallback_template_shape():
    row = _sample_row()
    text = fallback_template(row)
    assert (
        text
        == "Gerrit Cole K_OVER_7.5 at -115 (DraftKings). Model probability 58% vs line 7.5."
    )


def test_fallback_template_missing_fields():
    # Sparse row with missing odds, book, line
    sparse = {"player": "Aaron Judge", "bet_type": "HR_OVER_0.5", "model_prob": 0.40}
    text = fallback_template(sparse)
    assert "Aaron Judge HR_OVER_0.5" in text
    assert "Model probability 40%" in text
    assert "None" not in text


# ---------------------------------------------------------------------------
# check_draft pipeline tests (single item)
# ---------------------------------------------------------------------------


def test_check_draft_number_fail_no_api_call():
    row = _sample_row()
    # Text with foreign number 14
    text = "Gerrit Cole k over 7.5, wind 14 mph."
    spy = SpyDecisionsClient()

    res = check_draft(text, row, kind="explanation", client=spy)
    assert res["approved"] is False
    assert res["stage"] == "numbers"
    assert 14.0 in res["unknown_numbers"]
    assert res["fallback"] == "template"
    assert res["fallback_text"] == fallback_template(row)
    # The client was NEVER called!
    assert len(spy.calls) == 0


def test_check_draft_explanation_approved():
    row = _sample_row()
    text = "Gerrit Cole k over 7.5 projects to 8.4 vs 7.5 line with 24.1% strikeout rate."
    spy = SpyDecisionsClient(answers={"factor_entailment": {"noul": 0.05}})

    res = check_draft(text, row, kind="explanation", client=spy)
    assert res["approved"] is True
    assert res["stage"] == "approved"
    assert res["p_unsupported"] == 0.05
    assert len(spy.calls) == 1


def test_check_draft_social_rejected():
    row = _sample_row()
    text = "Gerrit Cole k over 7.5 at -115 is the lock of the century, smash it."
    spy = SpyDecisionsClient(answers={"compliance_class": {"choice": "tout"}})

    res = check_draft(text, row, kind="social", client=spy)
    assert res["approved"] is False
    assert res["stage"] == "decisions"
    assert res["reason"] == "tout"
    assert res["fallback"] == "template"


# ---------------------------------------------------------------------------
# check_drafts pipeline tests (plural / batched)
# ---------------------------------------------------------------------------


def test_check_drafts_single_batched_call_and_number_failures_excluded():
    row = _sample_row()
    # 3 items:
    # item 0: clean numbers
    # item 1: foreign number 99
    # item 2: clean numbers
    items = [
        {
            "item_id": "I0",
            "row": row,
            "explanation": "Gerrit Cole k over 7.5 projects 8.4 vs 7.5.",
        },
        {
            "item_id": "I1",
            "row": row,
            "explanation": "Gerrit Cole k over 7.5 with 99 mph fastballs.",
        },
        {
            "item_id": "I2",
            "row": row,
            "explanation": "Gerrit Cole k over 7.5 at -115.",
        },
    ]

    # Mock client setup: answers tagged wire questions for items 0 and 2
    # ItemBatch will assign tags i001 and i002 (since I1 is excluded before batch.add)
    mock_answers = {
        "i001_factor_entailment": {"noul": 0.08},
        "i002_factor_entailment": {"noul": 0.04},
    }
    spy = SpyDecisionsClient(answers=mock_answers)

    results = check_drafts(items, kind="explanation", client=spy)

    # Exactly ONE client call made
    assert len(spy.calls) == 1
    input_text, wire_questions = spy.calls[0]

    # Exactly 2 questions in the batch (item 1 was excluded)
    assert len(wire_questions) == 2
    assert "i001_factor_entailment" in wire_questions
    assert "i002_factor_entailment" in wire_questions

    # Item 1 never reached client, failed at numbers
    assert results[1]["approved"] is False
    assert results[1]["stage"] == "numbers"
    assert 99.0 in results[1]["unknown_numbers"]

    # Items 0 and 2 passed Decisions
    assert results[0]["approved"] is True
    assert results[0]["stage"] == "approved"
    assert results[2]["approved"] is True
    assert results[2]["stage"] == "approved"


def test_check_drafts_batch_abstention():
    row = _sample_row()
    items = [
        {"item_id": "I0", "row": row, "explanation": "Gerrit Cole k over 7.5."},
        {"item_id": "I1", "row": row, "explanation": "Gerrit Cole k over 7.5 at -115."},
    ]
    spy = SpyDecisionsClient(abstained=True, abstention_reason="http_500")

    results = check_drafts(items, kind="explanation", client=spy)
    assert len(results) == 2
    for r in results:
        assert r["approved"] is False
        assert r["stage"] == "decisions"
        assert r["reason"] == "batch_abstained"
        assert r["fallback"] == "template"


# ---------------------------------------------------------------------------
# Operating mode: shadow vs live applied flag
# ---------------------------------------------------------------------------


def test_shadow_applied_false_and_live_applied_true():
    row = _sample_row()
    text = "Gerrit Cole k over 7.5 projects 8.4."
    spy = SpyDecisionsClient(answers={"factor_entailment": {"noul": 0.05}})

    # Explicit shadow mode
    res_shadow = check_draft(text, row, kind="explanation", client=spy, mode="shadow")
    assert res_shadow["mode"] == "shadow"
    assert res_shadow["applied"] is False

    # Explicit live mode
    res_live = check_draft(text, row, kind="explanation", client=spy, mode="live")
    assert res_live["mode"] == "live"
    assert res_live["applied"] is True


def test_check_drafts_mode_propagation():
    row = _sample_row()
    items = [
        {"item_id": "I0", "row": row, "explanation": "Gerrit Cole k over 7.5."},
    ]
    spy = SpyDecisionsClient(answers={"i001_factor_entailment": {"noul": 0.05}})

    res_shadow = check_drafts(items, kind="explanation", client=spy, mode="shadow")
    assert res_shadow[0]["applied"] is False
    assert res_shadow[0]["mode"] == "shadow"

    spy.calls.clear()
    res_live = check_drafts(items, kind="explanation", client=spy, mode="live")
    assert res_live[0]["applied"] is True
    assert res_live[0]["mode"] == "live"


# ---------------------------------------------------------------------------
# Token limit guard
# ---------------------------------------------------------------------------


def test_estimate_tokens_guard():
    row = _sample_row()
    # Construct huge explanation that exceeds 90% of MAX_INPUT_TOKENS
    huge_text = "word " * int(MAX_INPUT_TOKENS * 3.5)
    spy = SpyDecisionsClient()

    with pytest.raises(ValueError, match="exceeds 90% of MAX_INPUT_TOKENS"):
        check_draft(huge_text, row, kind="explanation", client=spy)


# ---------------------------------------------------------------------------
# Client construction failure fallback
# ---------------------------------------------------------------------------


def test_client_init_failed_abstains(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    row = _sample_row()
    text = "Gerrit Cole k over 7.5 projects 8.4."

    # When client=None and no GEMINI_API_KEY in env, DecisionsClient() fails closed
    res = check_draft(text, row, kind="explanation", client=None)
    assert res["approved"] is False
    assert res["stage"] == "decisions"
    assert "client_init_failed" in res["reason"]
    assert res["fallback"] == "template"


# ---------------------------------------------------------------------------
# Decision logging
# ---------------------------------------------------------------------------


def test_check_drafts_logging(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    row = _sample_row()
    items = [
        {"item_id": "I0", "row": row, "explanation": "Gerrit Cole k over 7.5."},
        {"item_id": "I1", "row": row, "explanation": "Gerrit Cole k over 7.5, wind 99 mph."},
    ]
    spy = SpyDecisionsClient(answers={"i001_factor_entailment": {"noul": 0.05}})

    results = check_drafts(
        items,
        kind="explanation",
        client=spy,
        mode="live",
        log_path=log_file,
    )
    assert len(results) == 2
    assert log_file.exists()

    lines = [json.loads(line) for line in log_file.read_text().splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0]["route"] == "compliance_gate_explanation"
    assert lines[0]["item_id"] == "I0"
    assert lines[0]["decision"] == "approved"
    assert lines[0]["applied"] is True

    assert lines[1]["item_id"] == "I1"
    assert lines[1]["decision"] == "rejected"
    assert lines[1]["stage"] == "numbers"


# ---------------------------------------------------------------------------
# eval_compliance unit tests
# ---------------------------------------------------------------------------


def test_eval_compliance_raises_when_uncached_and_no_key(tmp_path, monkeypatch):
    from ev.decisions_evals.eval_compliance import (
        evaluate_explanations,
        evaluate_social,
    )

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    # Create dummy fixture dir with explanations.jsonl but no cache
    dummy_dir = tmp_path / "fixture"
    dummy_dir.mkdir()
    exp_file = dummy_dir / "explanations.jsonl"
    exp_file.write_text(
        json.dumps(
            {
                "item_id": "T001",
                "label": "clean",
                "explanation": "Gerrit Cole k over 7.5.",
                "row": _sample_row(),
            }
        )
        + "\n"
    )

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is unset and 1 uncached"):
        evaluate_explanations(dummy_dir)

    soc_file = dummy_dir / "social_drafts.jsonl"
    soc_file.write_text(
        json.dumps(
            {
                "item_id": "S001",
                "label": "clean",
                "draft_text": "Gerrit Cole k over 7.5.",
                "row": _sample_row(),
            }
        )
        + "\n"
    )

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is unset and 1 uncached"):
        evaluate_social(dummy_dir)


def test_eval_compliance_with_cached_answers(tmp_path, monkeypatch):
    from ev.decisions_evals.eval_compliance import run_eval

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    dummy_dir = tmp_path / "fixture"
    dummy_dir.mkdir()

    # Explanations fixture: 1 clean, 1 hallucinated
    exp_file = dummy_dir / "explanations.jsonl"
    exp_file.write_text(
        json.dumps(
            {
                "item_id": "E1",
                "label": "clean",
                "explanation": "Gerrit Cole k over 7.5.",
                "row": _sample_row(),
            }
        )
        + "\n"
        + json.dumps(
            {
                "item_id": "E2",
                "label": "hallucinated",
                "explanation": "Gerrit Cole k over 7.5 with unlisted trend.",
                "row": _sample_row(),
            }
        )
        + "\n"
    )

    # Pre-populate explanations cache
    exp_cache = dummy_dir / "answers_explanations.jsonl"
    exp_cache.write_text(
        json.dumps({"item_id": "E1", "answers": {"factor_entailment": {"noul": 0.05}}})
        + "\n"
        + json.dumps({"item_id": "E2", "answers": {"factor_entailment": {"noul": 0.90}}})
        + "\n"
    )

    # Social fixture: 1 clean, 1 tout
    soc_file = dummy_dir / "social_drafts.jsonl"
    soc_file.write_text(
        json.dumps(
            {
                "item_id": "D1",
                "label": "clean",
                "draft_text": "Gerrit Cole k over 7.5.",
                "row": _sample_row(),
            }
        )
        + "\n"
        + json.dumps(
            {
                "item_id": "D2",
                "label": "tout",
                "draft_text": "Gerrit Cole k over 7.5 is a lock.",
                "row": _sample_row(),
            }
        )
        + "\n"
    )

    # Pre-populate social cache
    soc_cache = dummy_dir / "answers_social.jsonl"
    soc_cache.write_text(
        json.dumps(
            {
                "item_id": "D1",
                "answers": {
                    "compliance_class": {
                        "choice": "compliant",
                        "probabilities": {"compliant": 0.95, "tout": 0.05},
                    }
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "item_id": "D2",
                "answers": {
                    "compliance_class": {
                        "choice": "tout",
                        "probabilities": {"tout": 0.95, "compliant": 0.05},
                    }
                },
            }
        )
        + "\n"
    )

    results = run_eval(dummy_dir)

    assert (dummy_dir / "eval_results.json").exists()
    assert results["explanations"]["total_items"] == 2
    assert results["explanations"]["combined_rejection_rate"] == 1.0
    assert results["explanations"]["clean_false_rejection_rate"] == 0.0
    assert results["social"]["total_items"] == 2
    assert results["social"]["tout_rejection_rate"] == 1.0
    assert results["social"]["clean_false_rejection_rate"] == 0.0

