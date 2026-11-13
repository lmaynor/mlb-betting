"""Tests for Player Availability Classifier (Decisions-API Build #2).

Offline hermetic test suite: zero live Decisions API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from ev.decisions_availability import (
    GOALIE_NOUL_MIN,
    STATUS_CHANGED_P,
    STATUS_OUT_P,
    STATUS_QUESTIONABLE_P,
    build_day_slates,
    build_goalie_input,
    build_goalie_questions,
    build_n6_input,
    build_n6_questions,
    build_status_input,
    build_status_questions,
    classify_availability,
    gate_goalie,
    gate_status,
    n2_items_for_batch,
    n2_result,
    n6_result,
    n6_team_context,
)
from ev.decisions_client import BatchResult
from ev.decisions_common import MAX_INPUT_TOKENS


class FakeClient:
    """Offline Decisions client recording call count and questions."""

    def __init__(
        self,
        answer_fn=None,
        abstain: bool = False,
        abstention_reason: str = "simulated_timeout",
    ) -> None:
        self.call_count: int = 0
        self.calls: list[dict[str, Any]] = []
        self.answer_fn = answer_fn
        self.abstain = abstain
        self.abstention_reason = abstention_reason

    def call(self, input_text: str, questions: Mapping[str, Any]) -> BatchResult:
        self.call_count += 1
        self.calls.append({"input_text": input_text, "questions": dict(questions)})
        if self.abstain:
            return BatchResult.abstain(self.abstention_reason)
        if self.answer_fn:
            answers = self.answer_fn(questions)
        else:
            answers = {}
            for qid, qdata in questions.items():
                if qdata.get("type") == "noul":
                    answers[qid] = {"noul": 0.95}
                elif qdata.get("type") == "choice":
                    answers[qid] = {
                        "choice": "confirmed",
                        "probabilities": {"confirmed": 0.95, "no_change": 0.05},
                    }
        return BatchResult(answers=answers, abstained=False)


def _sample_goalie():
    return {
        "item_id": "G001",
        "goalie": "Samuel Ersson",
        "team": "PHI",
        "opponent": "TBL",
        "game_date": "2025-11-24",
        "rotowire_status": "Confirmed",
        "rotowire_note": "Samuel Ersson is the confirmed starter for PHI.",
        "espn_blurb": "Samuel Ersson is confirmed starter.",
        "label": "confirmed_starter",
        "realized_starter": True,
    }


def _sample_status():
    return {
        "item_id": "S001",
        "player": "Bradley Beal",
        "team": "LAC",
        "matchup": "POR@LAC",
        "game_date": "2025-10-26",
        "designation_at_decision": "Questionable",
        "reason": "Injury/Illness - Back; Soreness",
        "blurb_text": "Bradley Beal will play.",
        "label": "played",
        "sport": "NBA",
    }


def _sample_n2():
    return {
        "item_id": "N2_001",
        "league": "MLB",
        "game": {
            "away": "Chicago White Sox",
            "home": "New York Yankees",
            "date": "2023-06-07",
        },
        "class": "postponed",
        "blurb_text": "Game postponed due to Canadian wildfires smoke.",
        "label": "postponed",
    }


def _sample_n6():
    return {
        "id": "n6-ARI-2023021168",
        "team": "ARI",
        "game_id": 2023021168,
        "game_date": "2024-03-30",
        "blurb": "Morning skate: ARI kept its 5v5 lines unchanged.",
        "expected_5v5_trios": [
            "Clayton Keller + Nick Bjugstad + Nick Schmaltz",
            "Jack McBain + Josh Doan + Matias Maccelli",
        ],
        "expected_pp_units": {
            "pp1": ["Clayton Keller", "Nick Schmaltz"],
            "pp2": ["Jack McBain"],
        },
        "label": "pp_only_change",
    }


# ---------------------------------------------------------------------------
# Test 1: Exactly ONE underlying call when all four groups present
# ---------------------------------------------------------------------------

def test_single_underlying_call_when_all_four_groups_present():
    fake = FakeClient()
    goalie = _sample_goalie()
    status = _sample_status()
    n2 = _sample_n2()
    n6 = _sample_n6()

    day_slates = {
        "2023-06-07": [
            {
                "game_id": "2023-06-07:Chicago White Sox@New York Yankees",
                "away": "Chicago White Sox",
                "home": "New York Yankees",
                "date": "2023-06-07",
                "league": "MLB",
            }
        ]
    }

    result = classify_availability(
        goalies=[goalie],
        statuses=[status],
        n2_items=[n2],
        day_slates=day_slates,
        n6_items=[n6],
        client=fake,
    )

    assert fake.call_count == 1
    assert len(result["goalies"]) == 1
    assert len(result["statuses"]) == 1
    assert len(result["events"]) == 1
    assert len(result["lines"]) == 1
    assert result["mode"] == "shadow"


# ---------------------------------------------------------------------------
# Test 2: Goalie confirmation threshold & rules
# ---------------------------------------------------------------------------

def test_goalie_confirmed_only_when_confirmed_and_noul_ge_threshold():
    item_confirmed = _sample_goalie()
    item_confirmed["rotowire_status"] = "Confirmed"

    # Confirmed and noul >= 0.90 -> confirmed
    res_90 = gate_goalie(item_confirmed, {"goalie_starts": {"noul": 0.90}})
    assert res_90["confirmed"] is True
    assert res_90["fail_closed"] is False
    assert res_90["reason"] == "confirmed"
    assert res_90["noul"] == 0.90

    # Confirmed and noul == 0.89 -> fails closed, noul_below_threshold
    res_89 = gate_goalie(item_confirmed, {"goalie_starts": {"noul": 0.89}})
    assert res_89["confirmed"] is False
    assert res_89["fail_closed"] is True
    assert res_89["reason"] == "noul_below_threshold"

    # Status Likely with noul 0.99 -> fails closed, status_not_confirmed
    item_likely = dict(item_confirmed, rotowire_status="Likely")
    res_likely = gate_goalie(item_likely, {"goalie_starts": {"noul": 0.99}})
    assert res_likely["confirmed"] is False
    assert res_likely["fail_closed"] is True
    assert res_likely["reason"] == "status_not_confirmed"

    # Status Confirmed with missing noul -> noul_missing
    res_missing = gate_goalie(item_confirmed, {})
    assert res_missing["confirmed"] is False
    assert res_missing["fail_closed"] is True
    assert res_missing["reason"] == "noul_missing"

    # Check doctrine docstring
    assert "noul never modifies any saves projection" in gate_goalie.__doc__.lower()


# ---------------------------------------------------------------------------
# Test 3: Status decision logic
# ---------------------------------------------------------------------------

def test_status_decision_logic():
    item = _sample_status()

    # Status OUT at P(scratched_out) = 0.80
    ans_out = {
        "availability_status": {
            "choice": "scratched_out",
            "probabilities": {"scratched_out": 0.80, "questionable": 0.10, "no_change": 0.10},
        }
    }
    res_out = gate_status(item, ans_out)
    assert res_out["status"] == "out"
    assert res_out["blocked_card"] is True
    assert "OUT" in res_out["suppression"]

    # Status uncertain at P(questionable) = 0.40
    ans_quest = {
        "availability_status": {
            "choice": "questionable",
            "probabilities": {"questionable": 0.40, "no_change": 0.50, "scratched_out": 0.10},
        }
    }
    res_quest = gate_status(item, ans_quest)
    assert res_quest["status"] == "uncertain"
    assert res_quest["blocked_card"] is True
    assert res_quest["reason"] == "status_uncertain"

    # Status uncertain when 1 - P(no_change) = 0.35 (i.e. P(no_change) = 0.65)
    ans_changed = {
        "availability_status": {
            "choice": "confirmed",
            "probabilities": {"no_change": 0.65, "confirmed": 0.25, "questionable": 0.10},
        }
    }
    res_changed = gate_status(item, ans_changed)
    assert res_changed["status"] == "uncertain"
    assert res_changed["blocked_card"] is True
    assert res_changed["reason"] == "status_uncertain"

    # Available otherwise (e.g. no_change = 0.70, 1 - 0.70 = 0.30 < 0.35)
    ans_avail = {
        "availability_status": {
            "choice": "no_change",
            "probabilities": {"no_change": 0.70, "confirmed": 0.20, "scratched_out": 0.05, "questionable": 0.05},
        }
    }
    res_avail = gate_status(item, ans_avail)
    assert res_avail["status"] == "available"
    assert res_avail["blocked_card"] is False

    # Abstained on empty answers / missing distribution
    res_abstain = gate_status(item, {})
    assert res_abstain["status"] == "uncertain"
    assert res_abstain["blocked_card"] is True
    assert res_abstain["reason"] == "abstained"


# ---------------------------------------------------------------------------
# Test 4: Abstention fail-closed per group
# ---------------------------------------------------------------------------

def test_abstention_fail_closed_per_group():
    fake = FakeClient(abstain=True, abstention_reason="api_timeout")
    goalie = _sample_goalie()
    status = _sample_status()
    n2 = _sample_n2()
    n6 = _sample_n6()

    day_slates = {
        "2023-06-07": [
            {
                "game_id": "2023-06-07:Chicago White Sox@New York Yankees",
                "away": "Chicago White Sox",
                "home": "New York Yankees",
                "date": "2023-06-07",
            }
        ]
    }

    res = classify_availability(
        goalies=[goalie],
        statuses=[status],
        n2_items=[n2],
        day_slates=day_slates,
        n6_items=[n6],
        client=fake,
    )

    # Goalie fail-closed
    g = res["goalies"][0]
    assert g["confirmed"] is False
    assert g["fail_closed"] is True
    assert g["reason"] == "noul_missing"

    # Status fail-closed
    s = res["statuses"][0]
    assert s["status"] == "uncertain"
    assert s["blocked_card"] is True
    assert s["reason"] == "abstained"

    # N2 fail-closed
    e = res["events"][0]
    assert e["disposition"] == "event_unconfirmed"
    assert e["witness_off"] is None

    # N6 fail-closed: prep-abstention semantics yields shadow_abstained
    l = res["lines"][0]
    assert l["disposition"] == "shadow_abstained"
    assert l["suspended_trios"] == []
    assert l["applied"] is False
    assert l["n6_shadow"] is True


# ---------------------------------------------------------------------------
# Test 5: N2 unmatched excluded from batch with event_unconfirmed
# ---------------------------------------------------------------------------

def test_n2_unmatched_excluded_from_batch():
    fake = FakeClient()
    matched_item = _sample_n2()
    unmatched_item = {
        "item_id": "N2_UNMATCHED",
        "league": "MLB",
        "game": {
            "away": "Boston Red Sox",
            "home": "Tampa Bay Rays",
            "date": "2023-06-07",
        },
        "class": "postponed",
        "blurb_text": "Game postponed due to rain.",
        "label": "postponed",
    }

    day_slates = {
        "2023-06-07": [
            {
                "game_id": "2023-06-07:Chicago White Sox@New York Yankees",
                "away": "Chicago White Sox",
                "home": "New York Yankees",
                "date": "2023-06-07",
                "league": "MLB",
            }
        ]
    }

    res = classify_availability(
        n2_items=[matched_item, unmatched_item],
        day_slates=day_slates,
        client=fake,
    )

    assert fake.call_count == 1
    # Only the matched item was in the questions sent to the client
    call_questions = fake.calls[0]["questions"]
    # Tags e0000 should be present, but not e0001
    assert any(k.startswith("e0000_") for k in call_questions)
    assert not any(k.startswith("e0001_") for k in call_questions)

    # Both items present in result
    events_by_id = {e["item_id"]: e for e in res["events"]}
    assert events_by_id["N2_001"]["matched"] is True
    assert events_by_id["N2_UNMATCHED"]["matched"] is False
    assert events_by_id["N2_UNMATCHED"]["witness_off"] is None
    assert events_by_id["N2_UNMATCHED"]["disposition"] == "event_unconfirmed"


# ---------------------------------------------------------------------------
# Test 6: N6 forced shadow applied=False even in live mode
# ---------------------------------------------------------------------------

def test_n6_forced_shadow_in_live_mode():
    fake = FakeClient()
    goalie = _sample_goalie()
    n6 = _sample_n6()

    res = classify_availability(
        goalies=[goalie],
        n6_items=[n6],
        mode="live",
        client=fake,
    )

    assert res["mode"] == "live"
    # Goalie is applied=True in live mode
    assert res["goalies"][0]["applied"] is True
    # N6 is applied=False ALWAYS, and n6_shadow=True
    assert res["lines"][0]["applied"] is False
    assert res["lines"][0]["n6_shadow"] is True


# ---------------------------------------------------------------------------
# Test 7: N6 pp_only_change does not suspend
# ---------------------------------------------------------------------------

def test_n6_pp_only_change_does_not_suspend():
    # Helper answer_fn returning pp_only_change with 0.9 prob and intact noul 0.9
    def answer_fn(questions):
        answers = {}
        for qid in questions:
            if "change_kind" in qid:
                answers[qid] = {
                    "choice": "pp_only_change",
                    "probabilities": {"pp_only_change": 0.90, "no_change": 0.10},
                }
            elif "trio_intact" in qid:
                answers[qid] = {"noul": 0.90}
            else:
                answers[qid] = {"noul": 0.90}
        return answers

    fake = FakeClient(answer_fn=answer_fn)
    n6 = _sample_n6()

    res = classify_availability(n6_items=[n6], client=fake)
    line = res["lines"][0]
    assert line["suspended_trios"] == []
    assert line["disposition"] == "shadow_ok"
    assert line["applied"] is False

    # Also test that a line_shuffle answer DOES suspend
    def shuffle_answer_fn(questions):
        answers = {}
        for qid in questions:
            if "change_kind" in qid:
                answers[qid] = {
                    "choice": "line_shuffle",
                    "probabilities": {"line_shuffle": 0.90, "no_change": 0.10},
                }
            elif "trio_intact" in qid:
                answers[qid] = {"noul": 0.20}
            else:
                answers[qid] = {"noul": 0.50}
        return answers

    fake_shuffle = FakeClient(answer_fn=shuffle_answer_fn)
    res_shuffle = classify_availability(n6_items=[n6], client=fake_shuffle)
    line_shuffle = res_shuffle["lines"][0]
    assert len(line_shuffle["suspended_trios"]) == 2
    assert line_shuffle["disposition"] == "shadow_suspend"


# ---------------------------------------------------------------------------
# Test 8: Token limit guard
# ---------------------------------------------------------------------------

def test_token_estimate_guard():
    # Create an item with an enormous blurb exceeding 90% of MAX_INPUT_TOKENS
    fake = FakeClient()
    oversized_text = "x" * int(MAX_INPUT_TOKENS * 3.5)
    huge_status = _sample_status()
    huge_status["blurb_text"] = oversized_text

    with pytest.raises(ValueError, match="exceeds 90% of MAX_INPUT_TOKENS"):
        classify_availability(statuses=[huge_status], client=fake)


# ---------------------------------------------------------------------------
# Test 9: Decision logging
# ---------------------------------------------------------------------------

def test_decision_logging(tmp_path: Path):
    fake = FakeClient()
    log_file = tmp_path / "decisions_log.jsonl"
    goalie = _sample_goalie()
    status = _sample_status()

    classify_availability(
        goalies=[goalie],
        statuses=[status],
        client=fake,
        log_path=log_file,
    )

    assert log_file.exists()
    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    for record in lines:
        assert record["route"] == "availability"
        assert "item_id" in record
        assert "decision" in record
        assert "mode" in record
        assert "applied" in record


# ---------------------------------------------------------------------------
# Test 10: Client None construction failure treated as abstained
# ---------------------------------------------------------------------------

def test_client_construction_failure_treated_as_abstained(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    goalie = _sample_goalie()

    # Calling with client=None without GEMINI_API_KEY should NOT raise;
    # it must fail closed gracefully.
    res = classify_availability(goalies=[goalie], client=None)
    g = res["goalies"][0]
    assert g["confirmed"] is False
    assert g["fail_closed"] is True
    assert g["reason"] == "noul_missing"
