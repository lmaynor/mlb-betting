"""Common utilities for offline Decisions route evaluation harness.

Provides rate-limiting pacing, question/token chunking, batched client execution,
and lightweight JSONL caching for raw evaluation answers.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence, Union

from ev.decisions_client import BatchResult, DecisionsClient, ItemBatch, TooManyQuestionsError
from ev.decisions_common import estimate_tokens

DEFAULT_MAX_QUESTIONS_PER_CALL_CHUNK = 900
DEFAULT_MAX_INPUT_TOKENS_PER_CHUNK = 60000
DEFAULT_TOKEN_RATE_PER_MIN = 40000


class Pacer:
    """Wall-clock token bucket rate limiter for Decisions API requests."""

    def __init__(
        self,
        tokens_per_min: int = DEFAULT_TOKEN_RATE_PER_MIN,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.tokens_per_min = tokens_per_min
        self._clock = clock
        self._sleep = sleep
        self._window_start = self._clock()
        self._spent = 0

    def pace(self, est_tokens: int) -> None:
        """Pace requests according to estimated tokens within a 60s window."""
        now = self._clock()
        if now - self._window_start >= 60:
            self._window_start = now
            self._spent = 0

        if self._spent + est_tokens > self.tokens_per_min and self._spent > 0:
            self._sleep(60 - (now - self._window_start))
            self._window_start = self._clock()
            self._spent = 0

        self._spent += est_tokens


def chunk_items(
    items: Sequence[tuple[str, Mapping[str, Any]]],
    max_questions: int = DEFAULT_MAX_QUESTIONS_PER_CALL_CHUNK,
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS_PER_CHUNK,
) -> list[list[tuple[str, Mapping[str, Any]]]]:
    """Greedily pack items into chunks.

    Each chunk's total question count <= max_questions and total estimated
    input tokens <= max_input_tokens. A single item exceeding the token
    budget forms its own chunk without being dropped or split.
    """
    chunks: list[list[tuple[str, Mapping[str, Any]]]] = []
    current_chunk: list[tuple[str, Mapping[str, Any]]] = []
    curr_q_count = 0
    curr_tokens = 0

    for item in items:
        input_block, questions = item
        item_q_count = len(questions)
        item_tokens = estimate_tokens(input_block) + estimate_tokens(json.dumps(questions))

        if not current_chunk:
            current_chunk.append(item)
            curr_q_count = item_q_count
            curr_tokens = item_tokens
        elif (
            curr_q_count + item_q_count <= max_questions
            and curr_tokens + item_tokens <= max_input_tokens
        ):
            current_chunk.append(item)
            curr_q_count += item_q_count
            curr_tokens += item_tokens
        else:
            chunks.append(current_chunk)
            current_chunk = [item]
            curr_q_count = item_q_count
            curr_tokens = item_tokens

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def run_batched(
    client: Any,
    items: Sequence[tuple[str, Mapping[str, Any]]],
    pacer: Optional[Pacer] = None,
    max_questions: int = DEFAULT_MAX_QUESTIONS_PER_CALL_CHUNK,
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS_PER_CHUNK,
) -> dict[int, dict[str, Any]]:
    """Run batched Decisions calls across chunked items with rate pacing.

    Merges batch.split(result) into a dict keyed by global item index (0-based
    across all chunks) with per-item question IDs restored.
    """
    chunks = chunk_items(items, max_questions=max_questions, max_input_tokens=max_input_tokens)
    out: dict[int, dict[str, Any]] = {}
    global_offset = 0

    for chunk in chunks:
        batch = ItemBatch()
        tags: list[str] = []
        for input_block, questions in chunk:
            tag = batch.add(input_block, questions)
            tags.append(tag)

        input_text, questions = batch.build()
        if pacer is not None:
            pacer.pace(estimate_tokens(input_text) + estimate_tokens(json.dumps(questions)))

        result = client.call(input_text, questions)
        if result.abstained:
            raise RuntimeError(f"Decisions batch abstained: {result.abstention_reason}")

        split_results = batch.split(result)
        for local_idx, tag in enumerate(tags):
            out[global_offset + local_idx] = split_results.get(tag, {})

        global_offset += len(chunk)

    return out


class AnswersCache:
    """Disk cache for Decisions raw evaluation answers stored as JSONL."""

    def __init__(self, path: Union[str, os.PathLike[str]]) -> None:
        self.path = Path(path)
        self._cache: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> dict[str, dict[str, Any]]:
        """Read JSONL lines {"item_id": str, "answers": {...}} if file exists else {}."""
        if not self.path.exists():
            self._cache = {}
            return {}
        cache: dict[str, dict[str, Any]] = {}
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                cache[str(data["item_id"])] = dict(data.get("answers", {}))
        self._cache = cache
        return dict(self._cache)

    def store(self, item_id: str, answers: Mapping[str, Any]) -> None:
        """Append or update answers in memory and rewrite the file sorted by item_id."""
        self._cache[str(item_id)] = dict(answers)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            for k in sorted(self._cache.keys()):
                record = {"item_id": k, "answers": self._cache[k]}
                f.write(json.dumps(record) + "\n")

    def get(self, item_id: str, default: Any = None) -> Any:
        """Retrieve cached answers for item_id, or default if not present."""
        return self._cache.get(str(item_id), default)

    def __contains__(self, item_id: object) -> bool:
        """Check if item_id is present in the cache."""
        return str(item_id) in self._cache


def live_client() -> DecisionsClient:
    """Construct DecisionsClient reading GEMINI_API_KEY from environment."""
    return DecisionsClient()


__all__ = [
    "DEFAULT_MAX_QUESTIONS_PER_CALL_CHUNK",
    "DEFAULT_MAX_INPUT_TOKENS_PER_CHUNK",
    "DEFAULT_TOKEN_RATE_PER_MIN",
    "Pacer",
    "chunk_items",
    "run_batched",
    "AnswersCache",
    "live_client",
]
