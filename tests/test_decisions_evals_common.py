"""Tests for Decisions offline evaluation harness common utilities.

Hermetic test suite: zero live Decisions API calls. Tests pacing, chunking,
batched execution across chunk boundaries, abstention handling, and JSONL caching.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from ev.decisions_client import BatchResult, DecisionsClient, DecisionsConfigError
from ev.decisions_evals.common import (
    DEFAULT_MAX_INPUT_TOKENS_PER_CHUNK,
    DEFAULT_MAX_QUESTIONS_PER_CALL_CHUNK,
    DEFAULT_TOKEN_RATE_PER_MIN,
    AnswersCache,
    Pacer,
    chunk_items,
    live_client,
    run_batched,
)


class FakeClock:
    """Deterministic simulated clock and sleep tracker for Pacer testing."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        self.sleeps.append(duration)
        self.now += duration

    def advance(self, delta: float) -> None:
        self.now += delta


class FakeClient:
    """Spy Decisions client that records calls and echoes answers keyed by wire keys."""

    def __init__(
        self,
        abstain: bool = False,
        abstention_reason: str = "safety_filter",
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.abstain = abstain
        self.abstention_reason = abstention_reason

    def call(self, input_text: str, questions: Mapping[str, Any]) -> BatchResult:
        self.calls.append({
            "input_text": input_text,
            "questions": dict(questions),
        })
        if self.abstain:
            return BatchResult.abstain(self.abstention_reason)
        answers = {key: {"noul": 0.5} for key in questions}
        return BatchResult(answers=answers, abstained=False)


def _make_item(num_questions: int, input_text: str = "item context") -> tuple[str, dict[str, Any]]:
    questions = {
        f"q{i:03d}": {
            "type": "noul",
            "instructions": f"Is condition {i} met?",
        }
        for i in range(num_questions)
    }
    return (input_text, questions)


# ---------------------------------------------------------------------------
# Pacer Tests
# ---------------------------------------------------------------------------

def test_pacer_pacing_and_window_reset():
    """Pacer with fake clock/sleep:

    - first pace under budget = no sleep
    - exceeding the per-minute budget triggers exactly one sleep of remaining window
    - window resets after 60s of fake time
    """
    fake = FakeClock(start=0.0)
    pacer = Pacer(tokens_per_min=40000, clock=fake.clock, sleep=fake.sleep)

    # 1. First pace under budget (25k <= 40k) -> no sleep
    pacer.pace(25000)
    assert fake.sleeps == []
    assert fake.now == 0.0

    # 2. Advance 10s into the window, pace another 25k -> total 50k > 40k.
    # Exceeding budget triggers exactly one sleep of the remaining window (60 - 10 = 50s).
    fake.advance(10.0)
    pacer.pace(25000)
    assert fake.sleeps == [50.0]
    # Clock advanced to 60.0, window reset, spent is now 25000
    assert fake.now == 60.0

    # 3. Window resets after 60s of fake time
    # Advance clock by 65s (fake.now becomes 125.0, which is > 60s after window start 60.0)
    fake.advance(65.0)
    assert fake.now == 125.0

    # Next pace under budget (30k <= 40k) should NOT sleep because window reset
    pacer.pace(30000)
    assert fake.sleeps == [50.0]  # No additional sleep


def test_pacer_oversized_first_request_does_not_sleep():
    """First request in a window is permitted even if it exceeds the rate limit."""
    fake = FakeClock(start=0.0)
    pacer = Pacer(tokens_per_min=40000, clock=fake.clock, sleep=fake.sleep)

    pacer.pace(50000)
    assert fake.sleeps == []


# ---------------------------------------------------------------------------
# chunk_items Tests
# ---------------------------------------------------------------------------

def test_chunk_items_question_cap():
    """chunk_items respects question cap: 5 items of 400 questions each -> chunks of 2, 2, 1."""
    items = [_make_item(400) for _ in range(5)]
    chunks = chunk_items(items, max_questions=900, max_input_tokens=60000)

    assert len(chunks) == 3
    assert [len(chunk) for chunk in chunks] == [2, 2, 1]


def test_chunk_items_token_budget_with_tiny_inputs():
    """chunk_items respects token budget with tiny inputs."""
    # Create 4 items with small questions and ~200 estimated input tokens each
    item_text = "a" * 700  # ceil(700 / 3.5) = 200 tokens
    questions = {"q0": {"type": "noul", "instructions": "test"}}
    items = [(item_text, questions) for _ in range(4)]

    # Budget of 500 tokens: each item is ~215 tokens.
    # Two items fit (~430 <= 500); three would exceed (~645 > 500).
    chunks = chunk_items(items, max_questions=900, max_input_tokens=500)
    assert len(chunks) == 2
    assert [len(chunk) for chunk in chunks] == [2, 2]


def test_chunk_items_oversized_single_item_alone_in_chunk():
    """An oversized single item alone forms its own chunk (never dropped, never split)."""
    normal_item = ("short context", {"q0": {"type": "noul", "instructions": "test"}})
    oversized_item = ("x" * 7000, {"q0": {"type": "noul", "instructions": "test"}})  # 2000 tokens

    # Token budget is 500: oversized_item exceeds budget on its own
    items = [normal_item, oversized_item, normal_item]
    chunks = chunk_items(items, max_questions=900, max_input_tokens=500)

    assert len(chunks) == 3
    assert chunks[0] == [normal_item]
    assert chunks[1] == [oversized_item]
    assert chunks[2] == [normal_item]

    # Single oversized item alone
    single_chunk = chunk_items([oversized_item], max_questions=900, max_input_tokens=500)
    assert len(single_chunk) == 1
    assert single_chunk[0] == [oversized_item]


def test_chunk_items_empty():
    """Empty item list returns empty chunks."""
    assert chunk_items([]) == []


# ---------------------------------------------------------------------------
# run_batched Tests
# ---------------------------------------------------------------------------

def test_run_batched_across_chunk_boundary():
    """run_batched with fake client across chunk boundary:

    3 items x 400 questions -> 2 calls, global indices 0..2 all present
    with per-item qids restored.
    """
    items = [_make_item(400, input_text=f"context for item {i}") for i in range(3)]
    fake_client = FakeClient()
    fake_clock = FakeClock()
    pacer = Pacer(tokens_per_min=DEFAULT_TOKEN_RATE_PER_MIN, clock=fake_clock.clock, sleep=fake_clock.sleep)

    results = run_batched(
        fake_client,
        items,
        pacer=pacer,
        max_questions=900,
        max_input_tokens=DEFAULT_MAX_INPUT_TOKENS_PER_CHUNK,
    )

    # 3 items of 400 questions split into chunks of 2 items (800 q) and 1 item (400 q)
    assert len(fake_client.calls) == 2
    assert len(fake_client.calls[0]["questions"]) == 800
    assert len(fake_client.calls[1]["questions"]) == 400

    # Global indices 0, 1, 2 must all be present
    assert set(results.keys()) == {0, 1, 2}

    # Verify per-item question IDs are restored (no i001_ or i002_ prefix in keys)
    for idx in range(3):
        assert len(results[idx]) == 400
        assert "q000" in results[idx]
        assert "q399" in results[idx]
        assert results[idx]["q000"] == {"noul": 0.5}
        assert results[idx]["q399"] == {"noul": 0.5}


def test_run_batched_raises_runtime_error_on_abstention():
    """run_batched raises RuntimeError when the Decisions batch abstains."""
    items = [_make_item(10, input_text="context")]
    abstained_client = FakeClient(abstain=True, abstention_reason="simulated_safety_block")

    with pytest.raises(RuntimeError, match="Decisions batch abstained: simulated_safety_block"):
        run_batched(abstained_client, items)


# ---------------------------------------------------------------------------
# AnswersCache Tests
# ---------------------------------------------------------------------------

def test_answers_cache_round_trip(tmp_path: Path):
    """AnswersCache round trip on tmp_path:

    - missing file loads empty
    - store writes and updates in memory
    - get and __contains__ work
    - reload in a new instance preserves data
    - file on disk is sorted by item_id
    """
    cache_file = tmp_path / "answers_cache.jsonl"

    # 1. Missing file loads empty
    cache1 = AnswersCache(cache_file)
    assert cache1.load() == {}
    assert "item_001" not in cache1
    assert cache1.get("item_001") is None
    assert cache1.get("item_001", default={"fallback": True}) == {"fallback": True}

    # 2. Store entries out of order
    answers_b = {"q001": {"noul": 0.9}}
    answers_a = {"q002": {"noul": 0.1}}
    cache1.store("item_b", answers_b)
    cache1.store("item_a", answers_a)

    assert "item_a" in cache1
    assert "item_b" in cache1
    assert "item_c" not in cache1
    assert cache1.get("item_a") == answers_a
    assert cache1.get("item_b") == answers_b

    # 3. Verify file on disk is written sorted by item_id
    lines = cache_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    row0 = json.loads(lines[0])
    row1 = json.loads(lines[1])
    assert row0["item_id"] == "item_a"
    assert row0["answers"] == answers_a
    assert row1["item_id"] == "item_b"
    assert row1["answers"] == answers_b

    # 4. Reload in a new instance
    cache2 = AnswersCache(cache_file)
    assert "item_a" in cache2
    assert "item_b" in cache2
    assert cache2.get("item_a") == answers_a
    assert cache2.get("item_b") == answers_b
    assert cache2.load() == {"item_a": answers_a, "item_b": answers_b}

    # 5. Update existing key and verify update persists sorted
    updated_answers_a = {"q002": {"noul": 0.95}}
    cache2.store("item_a", updated_answers_a)
    cache3 = AnswersCache(cache_file)
    assert cache3.get("item_a") == updated_answers_a


# ---------------------------------------------------------------------------
# live_client Tests
# ---------------------------------------------------------------------------

def test_live_client_propagates_decisions_config_error(monkeypatch):
    """live_client propagates DecisionsConfigError when GEMINI_API_KEY is not set."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(DecisionsConfigError, match="GEMINI_API_KEY is not set"):
        live_client()


def test_live_client_constructs_when_key_present(monkeypatch):
    """live_client returns DecisionsClient when GEMINI_API_KEY is present."""
    monkeypatch.setenv("GEMINI_API_KEY", "mock-test-key")
    client = live_client()
    assert isinstance(client, DecisionsClient)
    assert client._api_key == "mock-test-key"
