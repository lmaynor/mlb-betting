"""Google Decisions API client for Arm T (text) of the minutes-restriction
program.

Design (pre-registered; see
``~/workspace/beezy/minutes-restriction-unders-spike-2026-11-11.md`` sections
4-5 and the corrected prose check
``~/workspace/beezy/rotowire-historic-prose-check-2026-11-11.md``):

- The classifier grades RAMP-UP TRAJECTORY, not cap transcription. Verified
  evidence: explicit pre-return cap language appeared in 0 of 3 sampled return
  windows; blurbs carry coach quotes, practice reports, G League assignments
  and status trajectory. The archive also preserves superseded/contradictory
  updates raw, so the context passes blurbs through UNEDITED, in timestamp
  order, restricted to blurbs published at or before decision time.
- One batched call per game-case: a single ``choice`` question over
  [out, heavy_restriction, light_restriction, full_workload]. The response
  carries a picked option, a full probability distribution, and a confidence.
- Model ``gemini-decision-1`` at
  ``https://generativelanguage.googleapis.com/v1beta/decisions``. Request
  envelope per Google's published Decisions API shape (launched
  Nov 11, 2026): ``{"model", "input": <shared context text>, "questions":
  {<question_id>: {"type": "choice", "instructions": <question>,
  "criteria": {<option>: <description>}}}}``; the response carries
  ``answers`` as a MAP keyed by question id, each answer holding
  ``choice`` / ``probabilities`` / ``confidence``. NOTE: this client's
  first shipped envelope (a ``context`` field plus a list-shaped
  ``questions`` array) was rejected by the live API on Nov 12, 2026 with
  HTTP 400 ``Unknown parameter 'context'``; the Nov 11 sentinel smoke
  that "verified" the endpoint had used its own request construction,
  not this client's. The envelope below is the published one.
- Hard timeout 20.0s.
- FAIL CLOSED: on timeout, HTTP error, or malformed response the case is an
  ABSTENTION (``band is None``), never defaulted to a band. Abstentions are
  excluded from Arm T grading and counted/reported by the harness.

API key handling: the key is read from the ``GEMINI_API_KEY`` environment
variable at runtime only. In production the runner's environment is populated
from the GCP Secret Manager secret ``gemini-api-key`` (e.g. ``gcloud secrets
versions access latest --secret=gemini-api-key`` injected as the env var, or
the Cloud Run ``--set-secrets=GEMINI_API_KEY=gemini-api-key:latest`` wiring).
The key is NEVER hardcoded, logged, or placed in tests/fixtures.

Transport: a callable ``(url, headers, body_dict, timeout_s) ->
(status_code, parsed_json)``. The default transport uses stdlib urllib.
Tests inject mocked transports; nothing in the test suite makes a live call.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/decisions"
MODEL = "gemini-decision-1"
TIMEOUT_S = 20.0
QUESTION_ID = "minutes_band"

BANDS: tuple[str, ...] = (
    "out",
    "heavy_restriction",
    "light_restriction",
    "full_workload",
)

Transport = Callable[[str, dict, dict, float], tuple[int, Any]]


class DecisionsConfigError(RuntimeError):
    """Raised before any request when the client is misconfigured (no key)."""


class DecisionsTimeoutError(TimeoutError):
    """Raised by transports when the hard timeout is exceeded."""


class DecisionsHTTPError(RuntimeError):
    """Raised by transports on a non-2xx HTTP response."""


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
class BandPrediction:
    """One case's classifier output. ``band is None`` means ABSTENTION."""

    case_id: str
    band: Optional[str] = None
    probabilities: Optional[dict[str, float]] = None
    confidence: Optional[float] = None
    abstention: bool = True
    abstention_reason: Optional[str] = None
    raw: Optional[Any] = field(default=None, repr=False)

    @classmethod
    def abstain(cls, case_id: str, reason: str) -> "BandPrediction":
        return cls(case_id=case_id, band=None, abstention=True, abstention_reason=reason)


class DecisionsBandClassifier:
    """One batched Decisions ``choice`` call per game-case. Fail closed."""

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

    # -- request construction -------------------------------------------------

    def build_context(
        self,
        case: Mapping[str, Any],
        blurbs: Sequence[Mapping[str, Any]],
        mentions: Optional[Sequence[str]] = None,
    ) -> str:
        """Decision-time context. ``blurbs`` must already be cutoff-filtered
        (published <= decision time) and timestamp-ordered by the caller; they
        are rendered raw and unedited, superseded updates included.

        ``mentions`` (Argon Q4 contract): sentence-level excerpts from items
        headlined by OTHER players, rendered in a SEPARATE section under an
        explicit header instructing the classifier that they are secondary
        evidence at <= 0.25x the weight of the subject blurbs. ``None`` (the
        default) renders exactly the pre-Q4 context.
        """
        lines = [
            f"Player: {case.get('player')} ({case.get('team')})",
            f"Game date: {case.get('game_date')}",
            "Official injury report designation at decision time: "
            f"{case.get('designation')}"
            + (f" -- {case['reason']}" if case.get("reason") else ""),
            f"Games missed before this game: {case.get('games_missed')}",
            f"Baseline minutes (pre-absence mean): {case.get('baseline_minutes')}",
            "",
            "Pre-decision news blurbs for this player, oldest first, unedited. "
            "The archive preserves superseded and occasionally contradictory "
            "updates; weigh the trajectory, not any single item:",
        ]
        if blurbs:
            for b in blurbs:
                lines.append(
                    f"[{b.get('published_ts')}] {b.get('headline')}: {b.get('body')}"
                )
        else:
            lines.append("(no pre-decision blurbs available)")
        if mentions:
            from .arm_t_config import MENTION_FIELD_HEADER

            lines.append("")
            lines.append(MENTION_FIELD_HEADER)
            for excerpt in mentions:
                lines.append(f"- {excerpt}")
        return "\n".join(lines)

    def build_request(
        self,
        case: Mapping[str, Any],
        blurbs: Sequence[Mapping[str, Any]],
        mentions: Optional[Sequence[str]] = None,
    ) -> dict:
        """The single batched request body for one game-case.

        Published Decisions API envelope: the shared context rides in
        ``input``; ``questions`` is a MAP keyed by question id; a choice
        question carries ``instructions`` (the model never sees the key)
        and ``criteria`` mapping each option id to its description.
        """
        return {
            "model": self.model,
            "input": self.build_context(case, blurbs, mentions=mentions),
            "questions": {
                QUESTION_ID: {
                    "type": "choice",
                    "instructions": (
                        "Based only on the information available at decision "
                        "time, which workload band will this player realize "
                        "in this game, as a ratio of his baseline minutes?"
                    ),
                    "criteria": {
                        "out": "Does not play at all in this game (0 minutes).",
                        "heavy_restriction": (
                            "Plays, but at under 70% of his baseline minutes "
                            "(heavy minutes restriction)."
                        ),
                        "light_restriction": (
                            "Plays at 70% to under 90% of his baseline "
                            "minutes (light restriction or ramp-up)."
                        ),
                        "full_workload": (
                            "Plays at 90% or more of his baseline minutes "
                            "(full normal workload)."
                        ),
                    },
                }
            },
        }

    # -- response parsing -----------------------------------------------------

    @staticmethod
    def _extract_answer(payload: Any) -> Optional[Mapping[str, Any]]:
        # Published response shape: ``answers`` is a MAP keyed by question
        # id. Anything else is malformed and fails closed upstream.
        if not isinstance(payload, Mapping):
            return None
        answers = payload.get("answers")
        if isinstance(answers, Mapping):
            answer = answers.get(QUESTION_ID)
            return answer if isinstance(answer, Mapping) else None
        return None

    @staticmethod
    def _parse_distribution(answer: Mapping[str, Any]) -> Optional[dict[str, float]]:
        dist = answer.get("probabilities", answer.get("distribution"))
        probs: dict[str, float] = {}
        if isinstance(dist, Mapping):
            items = dist.items()
        elif isinstance(dist, Sequence) and not isinstance(dist, (str, bytes)):
            items = (
                (entry.get("option", entry.get("band")), entry.get("probability", entry.get("p")))
                for entry in dist
                if isinstance(entry, Mapping)
            )
        else:
            return None
        for band, p in items:
            if band in BANDS and isinstance(p, (int, float)):
                probs[str(band)] = float(p)
        if set(probs) != set(BANDS):
            return None
        total = sum(probs.values())
        if total <= 0 or abs(total - 1.0) > 0.05:
            return None
        return {b: probs[b] / total for b in BANDS}

    def parse_response(self, case_id: str, payload: Any) -> BandPrediction:
        """Parse a Decisions response. Anything malformed -> abstention."""
        answer = self._extract_answer(payload)
        if answer is None:
            return BandPrediction.abstain(case_id, "malformed_response: no answer object")
        picked = answer.get("picked", answer.get("option", answer.get("choice")))
        probs = self._parse_distribution(answer)
        if probs is None:
            return BandPrediction.abstain(
                case_id, "malformed_response: missing/invalid probability distribution"
            )
        if picked is None:
            picked = max(BANDS, key=lambda b: probs[b])
        if picked not in BANDS:
            return BandPrediction.abstain(
                case_id, f"malformed_response: picked option {picked!r} not a band"
            )
        confidence = answer.get("confidence")
        if confidence is not None and not isinstance(confidence, (int, float)):
            return BandPrediction.abstain(case_id, "malformed_response: confidence not numeric")
        return BandPrediction(
            case_id=case_id,
            band=str(picked),
            probabilities=probs,
            confidence=float(confidence) if confidence is not None else None,
            abstention=False,
            raw=payload,
        )

    # -- the one call -----------------------------------------------------------

    def classify_case(
        self,
        case: Mapping[str, Any],
        blurbs: Sequence[Mapping[str, Any]],
        mentions: Optional[Sequence[str]] = None,
    ) -> BandPrediction:
        """Classify one game-case with exactly ONE Decisions call.

        Fail closed: timeout, HTTP error, transport error, or a malformed
        response all produce an abstention, never a defaulted band.
        """
        case_id = str(case.get("case_id"))
        body = self.build_request(case, blurbs, mentions=mentions)
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key,
        }
        try:
            status, payload = self._transport(self.endpoint, headers, body, self.timeout_s)
        except DecisionsTimeoutError:
            return BandPrediction.abstain(case_id, "timeout")
        except TimeoutError:
            return BandPrediction.abstain(case_id, "timeout")
        except Exception as exc:  # transport-level failure: fail closed
            return BandPrediction.abstain(case_id, f"transport_error: {type(exc).__name__}")
        if status != 200:
            return BandPrediction.abstain(case_id, f"http_error: status {status}")
        return self.parse_response(case_id, payload)
