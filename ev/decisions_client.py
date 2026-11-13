"""Generic batched Google Decisions API client and multi-item adapter.

DOCTRINE:
- One batched Decisions call per job run: Decisions API calls are expensive,
  deliberate, and structured. Callers must batch multiple questions across one
  shared context into a single network round-trip.
- Hard 20.0s timeout: every call enforces a strict 20.0s deadline.
- FAIL CLOSED: any timeout, HTTP error, transport failure, or malformed
  response makes the whole batch an abstention, never a guessed or defaulted
  answer. Abstentions preserve system safety by withholding action rather
  than hallucinating a pick.
- API key runtime hygiene: the API key is read from the GEMINI_API_KEY
  environment variable at runtime only. In production, this is sourced from
  GCP Secret Manager secret 'gemini-api-key'. The key is NEVER hardcoded,
  logged, or placed in fixtures.
- Hermetic testing: tests inject mocked transports and never call live
  endpoints.
"""

from __future__ import annotations

import copy
import json
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/decisions"
MODEL = "gemini-decision-1"
TIMEOUT_S = 20.0
MAX_QUESTIONS_PER_CALL = 1000

_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_VALID_QUESTION_TYPES = {"noul", "choice", "score"}

Transport = Callable[[str, dict, dict, float], tuple[int, Any]]


class DecisionsConfigError(RuntimeError):
    """Raised before any request when the client is misconfigured (no key)."""


class DecisionsTimeoutError(TimeoutError):
    """Raised by transports when the hard timeout is exceeded."""


class DecisionsHTTPError(RuntimeError):
    """Raised by transports on a non-2xx HTTP response."""


class TooManyQuestionsError(ValueError):
    """Raised when the question count exceeds the API limit (1000)."""


def urllib_transport(url: str, headers: dict, body: dict, timeout_s: float) -> tuple[int, Any]:
    """Default transport (stdlib only). Never used by tests."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # HTTP status is data, not a crash
        raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = None
        return exc.code, parsed
    except (TimeoutError, socket.timeout) as exc:
        raise DecisionsTimeoutError(f"Decisions API timed out after {timeout_s}s") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise DecisionsTimeoutError(f"Decisions API timed out after {timeout_s}s") from exc
        raise DecisionsHTTPError(f"Decisions API request failed: {exc.reason}") from exc


@dataclass
class BatchResult:
    """The result of a batched Decisions API call."""

    answers: dict[str, Any] = field(default_factory=dict)
    abstained: bool = False
    abstention_reason: Optional[str] = None
    usage: Optional[dict] = None
    raw: Optional[Any] = field(default=None, repr=False)

    @classmethod
    def abstain(cls, reason: str) -> "BatchResult":
        """Return an abstained result with empty answers and a reason."""
        return cls(answers={}, abstained=True, abstention_reason=reason)


class DecisionsClient:
    """Generic batched Decisions API client. Fail closed."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        transport: Optional[Transport] = None,
        timeout_s: float = TIMEOUT_S,
        endpoint: str = ENDPOINT,
        model: str = MODEL,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY")
        if not key:
            raise DecisionsConfigError(
                "GEMINI_API_KEY is not set. The Decisions API key is read from "
                "the environment at runtime only (in production it is fetched "
                "from GCP Secret Manager secret 'gemini-api-key'). Refusing "
                "to construct a client without it."
            )
        self._api_key = key
        self._transport: Transport = transport or urllib_transport
        self.timeout_s = float(timeout_s)
        self.endpoint = endpoint
        self.model = model

    def build_request(self, input_text: str, questions: Mapping[str, Any]) -> dict:
        """Validate questions and build the request payload dictionary.

        Raises:
            ValueError: If questions is not a non-empty Mapping, or any key does
                not match _KEY_RE, or any question value is not a Mapping or has
                an invalid type.
            TooManyQuestionsError: If question count exceeds MAX_QUESTIONS_PER_CALL.
        """
        if not isinstance(questions, Mapping) or len(questions) == 0:
            raise ValueError("questions must be a non-empty Mapping")
        if len(questions) > MAX_QUESTIONS_PER_CALL:
            raise TooManyQuestionsError(
                f"Too many questions: {len(questions)} exceeds maximum of {MAX_QUESTIONS_PER_CALL}"
            )
        for key, value in questions.items():
            if not isinstance(key, str) or not _KEY_RE.match(key):
                raise ValueError(f"Invalid question key {key!r}; must match {_KEY_RE.pattern}")
            if not isinstance(value, Mapping):
                raise ValueError(
                    f"Question {key!r} value must be a Mapping, got {type(value).__name__}"
                )
            qtype = value.get("type")
            if qtype not in _VALID_QUESTION_TYPES:
                raise ValueError(
                    f"Question {key!r} type must be one of {sorted(_VALID_QUESTION_TYPES)}, got {qtype!r}"
                )
        return {
            "model": self.model,
            "input": input_text,
            "questions": dict(questions),
        }

    def call(self, input_text: str, questions: Mapping[str, Any]) -> BatchResult:
        """Make exactly ONE Decisions API call and return the batch result.

        Enforces fail-closed discipline: any timeout, network/transport error,
        non-200 HTTP status, or malformed response envelope produces an
        abstained result rather than a guessed answer.
        """
        body = self.build_request(input_text, questions)
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key,
        }
        try:
            status, payload = self._transport(self.endpoint, headers, body, self.timeout_s)
        except (DecisionsTimeoutError, TimeoutError):
            return BatchResult.abstain("timeout")
        except Exception as exc:
            return BatchResult.abstain(f"transport_error: {type(exc).__name__}")

        if status != 200:
            return BatchResult.abstain(f"http_error: status {status}")

        if not isinstance(payload, Mapping):
            return BatchResult.abstain("malformed_response: payload not a mapping")

        raw_answers = payload.get("answers")
        if not isinstance(raw_answers, Mapping):
            return BatchResult.abstain("malformed_response: answers not a mapping")

        filtered = {
            str(k): v
            for k, v in raw_answers.items()
            if isinstance(v, Mapping)
        }
        if not filtered:
            return BatchResult.abstain("malformed_response: no valid mapping answers")

        usage = payload.get("usage")
        usage_dict = usage if isinstance(usage, dict) else None

        return BatchResult(
            answers=filtered,
            abstained=False,
            abstention_reason=None,
            usage=usage_dict,
            raw=payload,
        )

    # -- Static reader helpers ------------------------------------------------

    @staticmethod
    def noul_value(answer: Any) -> Optional[float]:
        """Extract a noul probability from an answer shape.

        Tolerant of live shape (Mapping with 'noul', 'probability', or 'p')
        and flat test shapes (bare float/int, not bool). Returns None on
        anything unusable.
        """
        if isinstance(answer, (int, float)) and not isinstance(answer, bool):
            return float(answer)
        if isinstance(answer, Mapping):
            for key in ("noul", "probability", "p"):
                if key in answer:
                    val = answer[key]
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        return float(val)
        return None

    @staticmethod
    def choice_pick(answer: Any) -> Optional[str]:
        """Extract the picked option id from a choice answer shape.

        Tolerant of live shape (Mapping with 'choice', 'picked', or 'option')
        and flat test shapes (bare str). Returns None on anything unusable.
        """
        if isinstance(answer, str):
            return answer
        if isinstance(answer, Mapping):
            for key in ("choice", "picked", "option"):
                if key in answer:
                    val = answer[key]
                    if isinstance(val, str):
                        return val
        return None

    @staticmethod
    def probabilities(answer: Any) -> Optional[dict[str, float]]:
        """Extract the probability distribution dictionary from an answer shape.

        Tolerant of Mapping answer's 'probabilities' or 'distribution' Mapping,
        keeping only numeric (non-bool) values cast to float. Returns None if
        absent, not a Mapping, or empty.
        """
        if not isinstance(answer, Mapping):
            return None
        dist = answer.get("probabilities")
        if not isinstance(dist, Mapping):
            dist = answer.get("distribution")
        if not isinstance(dist, Mapping):
            return None
        probs = {
            str(k): float(v)
            for k, v in dist.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        return probs if probs else None

    @staticmethod
    def score_value(answer: Any) -> Optional[float]:
        """Extract a numeric score value from a score answer shape.

        Tolerant of live shape (Mapping with 'score' or 'value') and flat
        test shapes (bare number). Returns None on anything unusable.
        """
        if isinstance(answer, (int, float)) and not isinstance(answer, bool):
            return float(answer)
        if isinstance(answer, Mapping):
            for key in ("score", "value"):
                if key in answer:
                    val = answer[key]
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        return float(val)
        return None

    @staticmethod
    def confidence(answer: Any) -> Optional[float]:
        """Extract a confidence score from an answer shape.

        Tolerant of Mapping answer's 'confidence' key and bare numbers.
        Returns None on anything unusable.
        """
        if isinstance(answer, (int, float)) and not isinstance(answer, bool):
            return float(answer)
        if isinstance(answer, Mapping):
            val = answer.get("confidence")
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return float(val)
        return None


# Module-level aliases for reader helpers
noul_value = DecisionsClient.noul_value
choice_pick = DecisionsClient.choice_pick
probabilities = DecisionsClient.probabilities
score_value = DecisionsClient.score_value
confidence = DecisionsClient.confidence


class ItemBatch:
    """Standard multi-item batching adapter for Decisions API calls.

    Namespaces question ids by prefixing with the item tag (e.g. tag_qid),
    prefixes instructions to target each tagged input block, enforces the
    1000-question capacity ceiling, and splits batched results back to
    per-item results.
    """

    def __init__(self) -> None:
        self._items: list[tuple[str, str, list[str]]] = []
        self._questions: dict[str, dict[str, Any]] = {}
        self._keymap: dict[str, tuple[str, str]] = {}
        self._counter: int = 0

    def add(
        self,
        input_block: str,
        questions: Mapping[str, Any],
        tag: Optional[str] = None,
    ) -> str:
        """Add an item and its questions to the batch.

        Args:
            input_block: The context block text for this item.
            questions: Mapping of qid -> question dictionary.
            tag: Optional item tag. Defaults to f"i{len(self._items)+1:03d}".

        Returns:
            The item tag assigned.

        Raises:
            ValueError: If tag or any qid is invalid, or wire key exceeds 64 chars,
                or questions is not a Mapping.
            TooManyQuestionsError: If adding these questions would exceed
                MAX_QUESTIONS_PER_CALL. Raised before modifying any state.
        """
        if tag is None:
            tag = f"i{len(self._items) + 1:03d}"
        if not isinstance(tag, str) or not _KEY_RE.match(tag):
            raise ValueError(f"Invalid item tag {tag!r}; must match {_KEY_RE.pattern}")
        if not isinstance(questions, Mapping):
            raise ValueError("questions must be a Mapping")

        # Capacity guard: check BEFORE adding anything
        if len(self._questions) + len(questions) > MAX_QUESTIONS_PER_CALL:
            raise TooManyQuestionsError(
                f"Adding {len(questions)} questions would bring total to "
                f"{len(self._questions) + len(questions)}, exceeding maximum of {MAX_QUESTIONS_PER_CALL}"
            )

        new_questions: dict[str, dict[str, Any]] = {}
        new_keymap: dict[str, tuple[str, str]] = {}
        qids: list[str] = []

        for qid, qdata in questions.items():
            if not isinstance(qid, str) or not _KEY_RE.match(qid):
                raise ValueError(f"Invalid question id {qid!r}; must match {_KEY_RE.pattern}")
            wire_key = f"{tag}_{qid}"
            if len(wire_key) > 64:
                raise ValueError(
                    f"Combined wire key {wire_key!r} exceeds 64 characters ({len(wire_key)})"
                )
            if not isinstance(qdata, Mapping):
                raise ValueError(
                    f"Question {qid!r} value must be a Mapping, got {type(qdata).__name__}"
                )
            qtype = qdata.get("type")
            if qtype not in _VALID_QUESTION_TYPES:
                raise ValueError(
                    f"Question {qid!r} type must be one of {sorted(_VALID_QUESTION_TYPES)}, got {qtype!r}"
                )

            q_copy = copy.deepcopy(dict(qdata))
            instr = q_copy.get("instructions")
            if isinstance(instr, str):
                q_copy["instructions"] = f"Answer for the input block tagged [{tag}] only. {instr}"
            new_questions[wire_key] = q_copy
            new_keymap[wire_key] = (tag, qid)
            qids.append(qid)

        self._questions.update(new_questions)
        self._keymap.update(new_keymap)
        self._items.append((tag, input_block, qids))
        self._counter += 1
        return tag

    @property
    def question_count(self) -> int:
        """Total number of wire questions currently in the batch."""
        return len(self._questions)

    def build(self) -> tuple[str, dict[str, Any]]:
        """Build the combined input text and wire questions dictionary."""
        input_text = "\n\n".join(f"[{tag}]\n{block}" for tag, block, _ in self._items)
        return input_text, dict(self._questions)

    def split(self, result: BatchResult) -> dict[str, dict[str, Any]]:
        """Split a BatchResult into per-item answer dictionaries.

        Returns {tag: {qid: raw_answer}} for every item tag. An abstained
        batch yields an empty dict per tag. Otherwise PARTIAL answers are
        preserved: each tag gets exactly the answers the API returned for
        it. Missing-field semantics belong to the downstream gates (every
        route gate treats a missing answer as a fail-closed signal for
        that question), so the adapter never discards a usable answer.
        """
        out: dict[str, dict[str, Any]] = {}
        for tag, _, qids in self._items:
            if result.abstained:
                out[tag] = {}
                continue
            out[tag] = {
                qid: result.answers[f"{tag}_{qid}"]
                for qid in qids
                if f"{tag}_{qid}" in result.answers
            }
        return out


__all__ = [
    "ENDPOINT",
    "MODEL",
    "TIMEOUT_S",
    "MAX_QUESTIONS_PER_CALL",
    "DecisionsConfigError",
    "DecisionsTimeoutError",
    "DecisionsHTTPError",
    "TooManyQuestionsError",
    "Transport",
    "urllib_transport",
    "BatchResult",
    "DecisionsClient",
    "ItemBatch",
    "noul_value",
    "choice_pick",
    "probabilities",
    "score_value",
    "confidence",
]
