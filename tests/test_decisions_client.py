"""Tests for generic batched Decisions API client and route plumbing.

Offline and hermetic: all tests use injected spy/mock transports and zero
live network calls.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from ev.decisions_client import (
    ENDPOINT,
    MAX_QUESTIONS_PER_CALL,
    MODEL,
    TIMEOUT_S,
    BatchResult,
    DecisionsClient,
    DecisionsConfigError,
    DecisionsHTTPError,
    DecisionsTimeoutError,
    ItemBatch,
    TooManyQuestionsError,
    choice_pick,
    confidence,
    noul_value,
    probabilities,
    score_value,
)
from ev.decisions_common import (
    DEFAULT_MODE,
    MAX_INPUT_TOKENS,
    ROUTE_MODES,
    append_records,
    estimate_tokens,
    make_record,
    route_mode,
)

# Canned payload shaped EXACTLY like the verified live Decisions API response
CANNED_LIVE_PAYLOAD = {
    "answers": {
        "q_noul": {
            "type": "noul",
            "noul": 0.98,
            "confidence": 0.95,
        },
        "q_choice": {
            "type": "choice",
            "choice": "option_b",
            "probabilities": {
                "option_a": 0.15,
                "option_b": 0.80,
                "option_c": 0.05,
            },
            "confidence": 0.90,
        },
        "q_score": {
            "type": "score",
            "score": 2.7,
            "probabilities": {
                "0": 0.05,
                "1": 0.20,
                "2": 0.50,
                "3": 0.25,
            },
            "legend": {
                "0": "minimal",
                "1": "low",
                "2": "moderate",
                "3": "high",
            },
            "confidence": 0.85,
        },
    },
    "usage": {
        "total_input_tokens": 128,
        "total_output_tokens": 42,
        "total_tokens": 170,
    },
}

_UNSET = object()


class SpyTransport:
    """Mock transport that records calls and returns canned responses."""

    def __init__(self, status: int = 200, payload: object = _UNSET, exc: Exception | None = None) -> None:
        self.status = status
        self.payload = CANNED_LIVE_PAYLOAD if payload is _UNSET else payload
        self.exc = exc
        self.calls: list[dict] = []

    def __call__(self, url: str, headers: dict, body: dict, timeout: float) -> tuple[int, object]:
        self.calls.append({
            "url": url,
            "headers": headers,
            "body": body,
            "timeout": timeout,
        })
        if self.exc is not None:
            raise self.exc
        return self.status, self.payload


# -- DecisionsClient Tests ----------------------------------------------------


def test_call_success_returns_raw_answers_and_usage() -> None:
    transport = SpyTransport(payload=CANNED_LIVE_PAYLOAD)
    client = DecisionsClient(api_key="test-key", transport=transport)
    questions = {
        "q_noul": {"type": "noul", "instructions": "Is this player active?"},
        "q_choice": {
            "type": "choice",
            "instructions": "Pick option",
            "criteria": {"option_a": "A", "option_b": "B", "option_c": "C"},
        },
        "q_score": {
            "type": "score",
            "instructions": "Score level",
            "criteria": ["minimal", "low", "moderate", "high"],
        },
    }

    result = client.call("shared player blurbs and context", questions)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == ENDPOINT
    assert call["timeout"] == TIMEOUT_S
    assert call["headers"]["x-goog-api-key"] == "test-key"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["body"]["model"] == MODEL
    assert call["body"]["input"] == "shared player blurbs and context"
    assert call["body"]["questions"] == questions

    assert not result.abstained
    assert result.abstention_reason is None
    assert result.answers == CANNED_LIVE_PAYLOAD["answers"]
    assert result.usage == CANNED_LIVE_PAYLOAD["usage"]
    assert result.raw == CANNED_LIVE_PAYLOAD


def test_readers_on_live_shapes() -> None:
    noul_ans = CANNED_LIVE_PAYLOAD["answers"]["q_noul"]
    choice_ans = CANNED_LIVE_PAYLOAD["answers"]["q_choice"]
    score_ans = CANNED_LIVE_PAYLOAD["answers"]["q_score"]

    # Live response shapes
    assert DecisionsClient.noul_value(noul_ans) == 0.98
    assert noul_value(noul_ans) == 0.98
    assert confidence(noul_ans) == 0.95

    assert DecisionsClient.choice_pick(choice_ans) == "option_b"
    assert choice_pick(choice_ans) == "option_b"
    assert probabilities(choice_ans) == {
        "option_a": 0.15,
        "option_b": 0.80,
        "option_c": 0.05,
    }
    assert confidence(choice_ans) == 0.90

    assert DecisionsClient.score_value(score_ans) == 2.7
    assert score_value(score_ans) == 2.7
    assert probabilities(score_ans) == {
        "0": 0.05,
        "1": 0.20,
        "2": 0.50,
        "3": 0.25,
    }
    assert confidence(score_ans) == 0.85

    # Flat test shapes: bare float answer
    assert noul_value(0.75) == 0.75
    assert score_value(3.5) == 3.5
    assert confidence(0.8) == 0.8
    assert choice_pick("option_x") == "option_x"

    # Flat test shapes: {"probability": x}, {"p": x}
    assert noul_value({"probability": 0.62}) == 0.62
    assert noul_value({"p": 0.45}) == 0.45

    # Flat test shapes: {"picked": x}, {"option": x}
    assert choice_pick({"picked": "opt_1"}) == "opt_1"
    assert choice_pick({"option": "opt_2"}) == "opt_2"

    # Flat test shapes: {"value": x}
    assert score_value({"value": 4.1}) == 4.1

    # Flat test shapes: {"distribution": {...}}
    assert probabilities({"distribution": {"k1": 0.3, "k2": 0.7}}) == {"k1": 0.3, "k2": 0.7}

    # Unusable / invalid values -> return None (never raise, never guess)
    assert noul_value(True) is None
    assert noul_value("0.5") is None
    assert noul_value(None) is None
    assert choice_pick(123) is None
    assert choice_pick(None) is None
    assert probabilities(None) is None
    assert probabilities({}) is None
    assert probabilities({"probabilities": {}}) is None
    assert probabilities({"probabilities": {"bad": "string"}}) is None
    assert score_value(False) is None
    assert score_value("high") is None
    assert confidence(True) is None
    assert confidence("0.9") is None


def test_too_many_questions_raises_before_any_transport_call() -> None:
    transport = SpyTransport()
    client = DecisionsClient(api_key="test-key", transport=transport)
    # Build 1001 questions programmatically
    questions = {
        f"q_{i:04d}": {"type": "noul", "instructions": f"Q {i}"}
        for i in range(1001)
    }
    with pytest.raises(TooManyQuestionsError) as exc_info:
        client.call("shared context", questions)
    assert "exceeds maximum of 1000" in str(exc_info.value)
    assert len(transport.calls) == 0


def test_invalid_question_key_raises() -> None:
    transport = SpyTransport()
    client = DecisionsClient(api_key="test-key", transport=transport)
    questions = {
        "invalid::key": {"type": "noul", "instructions": "test instructions"}
    }
    with pytest.raises(ValueError) as exc_info:
        client.call("shared context", questions)
    assert "invalid::key" in str(exc_info.value)
    assert len(transport.calls) == 0


def test_timeout_abstains() -> None:
    # DecisionsTimeoutError
    transport = SpyTransport(exc=DecisionsTimeoutError("Decisions API timed out after 20.0s"))
    client = DecisionsClient(api_key="test-key", transport=transport)
    questions = {"q1": {"type": "noul", "instructions": "test"}}
    res1 = client.call("context", questions)
    assert res1.abstained is True
    assert res1.abstention_reason == "timeout"
    assert res1.answers == {}

    # stdlib TimeoutError
    transport_std = SpyTransport(exc=TimeoutError("Socket timed out"))
    client_std = DecisionsClient(api_key="test-key", transport=transport_std)
    res2 = client_std.call("context", questions)
    assert res2.abstained is True
    assert res2.abstention_reason == "timeout"
    assert res2.answers == {}


def test_http_500_abstains() -> None:
    transport = SpyTransport(status=500, payload={"error": "internal error"})
    client = DecisionsClient(api_key="test-key", transport=transport)
    questions = {"q1": {"type": "noul", "instructions": "test"}}
    res = client.call("context", questions)
    assert res.abstained is True
    assert res.abstention_reason == "http_error: status 500"
    assert res.answers == {}


def test_malformed_payload_abstains() -> None:
    client = DecisionsClient(api_key="test-key")
    questions = {"q1": {"type": "noul", "instructions": "test"}}

    # answers not a mapping
    client._transport = SpyTransport(status=200, payload={"answers": ["not", "a", "mapping"]})
    res1 = client.call("context", questions)
    assert res1.abstained is True
    assert res1.abstention_reason is not None and res1.abstention_reason.startswith("malformed_response:")
    assert res1.answers == {}

    # answers empty
    client._transport = SpyTransport(status=200, payload={"answers": {}})
    res2 = client.call("context", questions)
    assert res2.abstained is True
    assert res2.abstention_reason is not None and res2.abstention_reason.startswith("malformed_response:")
    assert res2.answers == {}

    # payload not a mapping
    client._transport = SpyTransport(status=200, payload="string response instead of json dict")
    res3 = client.call("context", questions)
    assert res3.abstained is True
    assert res3.abstention_reason is not None and res3.abstention_reason.startswith("malformed_response:")
    assert res3.answers == {}


def test_non_mapping_answers_filtered() -> None:
    payload = {
        "answers": {
            "q1": {"type": "noul", "noul": 0.85, "confidence": 0.9},
            "q2": "garbage string answer instead of dict",
        },
        "usage": {"total_tokens": 60},
    }
    transport = SpyTransport(status=200, payload=payload)
    client = DecisionsClient(api_key="test-key", transport=transport)
    questions = {
        "q1": {"type": "noul", "instructions": "i1"},
        "q2": {"type": "noul", "instructions": "i2"},
    }
    res = client.call("context", questions)
    assert res.abstained is False
    assert res.abstention_reason is None
    assert "q1" in res.answers
    assert "q2" not in res.answers
    assert res.answers["q1"] == {"type": "noul", "noul": 0.85, "confidence": 0.9}


def test_missing_api_key_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(DecisionsConfigError) as exc_info:
        DecisionsClient(api_key=None)
    assert "GEMINI_API_KEY is not set" in str(exc_info.value)
    assert "gemini-api-key" in str(exc_info.value)


# -- ItemBatch Tests ----------------------------------------------------------


def test_batch_namespacing_and_split_roundtrip() -> None:
    batch = ItemBatch()
    tag1 = batch.add(
        "Context for item 1",
        {
            "status": {
                "type": "choice",
                "instructions": "Is player active?",
                "criteria": {"yes": "Active", "no": "Inactive"},
            }
        },
    )
    tag2 = batch.add(
        "Context for item 2",
        {
            "status": {
                "type": "choice",
                "instructions": "Is player active?",
                "criteria": {"yes": "Active", "no": "Inactive"},
            }
        },
    )
    assert tag1 == "i001"
    assert tag2 == "i002"
    assert batch.question_count == 2

    input_text, wire_questions = batch.build()

    # Input contains both [i001] and [i002] blocks
    assert "[i001]\nContext for item 1" in input_text
    assert "[i002]\nContext for item 2" in input_text

    # Wire questions are namespaced
    assert "i001_status" in wire_questions
    assert "i002_status" in wire_questions

    # Instructions received the tag prefix
    assert (
        wire_questions["i001_status"]["instructions"]
        == "Answer for the input block tagged [i001] only. Is player active?"
    )
    assert (
        wire_questions["i002_status"]["instructions"]
        == "Answer for the input block tagged [i002] only. Is player active?"
    )

    # Fake transport echoes canned payload keyed by wire keys
    canned_answers = {
        "i001_status": {"type": "choice", "choice": "yes", "confidence": 0.95},
        "i002_status": {"type": "choice", "choice": "no", "confidence": 0.88},
    }
    transport = SpyTransport(payload={"answers": canned_answers})
    client = DecisionsClient(api_key="test-key", transport=transport)
    result = client.call(input_text, wire_questions)

    # Split returns per-tag original qids
    split_res = batch.split(result)
    assert split_res == {
        "i001": {"status": {"type": "choice", "choice": "yes", "confidence": 0.95}},
        "i002": {"status": {"type": "choice", "choice": "no", "confidence": 0.88}},
    }


def test_batch_capacity_guard() -> None:
    batch = ItemBatch()
    # Add an item with 100 questions
    q100 = {f"q_{i}": {"type": "noul", "instructions": "test"} for i in range(100)}
    batch.add("Item 1", q100)
    assert batch.question_count == 100
    assert len(batch._items) == 1

    # Adding an item with 901 questions would bring total to 1001 > 1000
    q901 = {f"q_{i}": {"type": "noul", "instructions": "test"} for i in range(901)}
    with pytest.raises(TooManyQuestionsError):
        batch.add("Item 2", q901)

    # Verify batch is left unchanged
    assert batch.question_count == 100
    assert len(batch._items) == 1
    assert "i002_q_0" not in batch._questions


def test_batch_split_on_abstention_returns_empty_dicts() -> None:
    batch = ItemBatch()
    batch.add("Block 1", {"q": {"type": "noul", "instructions": "instr"}})
    batch.add("Block 2", {"q": {"type": "noul", "instructions": "instr"}})

    abstained_result = BatchResult.abstain("timeout")
    split_res = batch.split(abstained_result)
    assert split_res == {"i001": {}, "i002": {}}


# -- decisions_common Tests ---------------------------------------------------


def test_route_mode_defaults_shadow_and_fails_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECISIONS_ROUTE_MODE", raising=False)
    assert route_mode() == "shadow"

    monkeypatch.setenv("DECISIONS_ROUTE_MODE", "live")
    assert route_mode() == "live"

    monkeypatch.setenv("DECISIONS_ROUTE_MODE", "  LIVE  ")
    assert route_mode() == "live"

    monkeypatch.setenv("DECISIONS_ROUTE_MODE", "garbage")
    assert route_mode() == "shadow"

    monkeypatch.setenv("DECISIONS_ROUTE_MODE", "")
    assert route_mode() == "shadow"


def test_make_record_shape() -> None:
    record = make_record(
        route="n1_ladder_identity",
        item_id="item_001",
        decision={"status": "confirmed"},
        mode="shadow",
        applied=False,
        extra_key="extra_val",
    )
    assert record["route"] == "n1_ladder_identity"
    assert record["item_id"] == "item_001"
    assert record["decision"] == {"status": "confirmed"}
    assert record["mode"] == "shadow"
    assert record["applied"] is False
    assert record["extra_key"] == "extra_val"
    assert "ts" in record

    # Verify timestamp is valid ISO 8601 UTC
    dt = datetime.fromisoformat(record["ts"])
    assert dt.tzinfo is not None


def test_append_records_writes_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "logs" / "audit.jsonl"
    records = [
        {"route": "r1", "item_id": "1", "decision": "a"},
        {"route": "r1", "item_id": "2", "decision": "b"},
    ]
    count = append_records(log_path, records)
    assert count == 2
    assert log_path.exists()

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == records[0]
    assert json.loads(lines[1]) == records[1]

    # Append additional record
    count2 = append_records(log_path, [{"route": "r1", "item_id": "3", "decision": "c"}])
    assert count2 == 1
    lines2 = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines2) == 3


def test_estimate_tokens_monotonic_and_empty() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("hello") == 2
    assert estimate_tokens("x" * 350) == 100

    # Monotonicity test
    tokens = [estimate_tokens("x" * i) for i in range(1, 101)]
    for i in range(len(tokens) - 1):
        assert tokens[i] <= tokens[i + 1]


def test_batch_split_preserves_partial_answers() -> None:
    batch = ItemBatch()
    batch.add("block one", {"qa": {"type": "noul", "instructions": "q1"},
                            "qb": {"type": "noul", "instructions": "q2"}})
    result = BatchResult(answers={"i001_qa": {"noul": 0.5, "type": "noul"}})
    split = batch.split(result)
    assert split == {"i001": {"qa": {"noul": 0.5, "type": "noul"}}}
