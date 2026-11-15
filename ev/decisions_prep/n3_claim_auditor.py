"""N3 -- research-claim evidence auditor: deterministic prep (NO API calls).

DEC-03 is now a TWO-STAGE auditor per RUL-20261113-dec-03-contradiction.
Stage 1 replaces the single "contradicted" head with two non-overlapping heads,
direct_conflict and missing_support, alongside supported, wrong_scope, stale.
Stage 1 blocks, clears, or routes to a Stage 2 isolated confirm pass
(route-side, using build_confirm_questions/confirm_gate defined here).

Argon ruling (Nov 13, 2026; ~/workspace/beezy/profitability/
argon-decisions-api-expansion-VERBATIM-2026-11-13.md, candidate N3, ADOPT
standalone): an internal gate auditing research packets and build briefs
before they reach Luc. Because gemini-decision-1 emits zero text tokens and
cannot verify exact numeric tables by arithmetic, N3 pairs

1. DETERMINISTIC literal-number checks (this module's ``extract_numeric_claims``
   and ``check_numbers``) for structured counts/metrics where manifests exist,
   with
2. Decisions entailment / quote-fidelity / scope questions (``build_questions``,
   in the merged client's published envelope style -- model / input /
   questions-map, see nba/minutes_restriction/decisions_client.py), scored
   later by the canonical client once PR #242/#243-era route code is built.

Stage 1 routing rule:
* supported is None -> block reason "missing_supported_probability".
* elif supported < SUPPORTED_MIN -> block reason f"supported_below_threshold:{supported:.4f}<{SUPPORTED_MIN}".
* scope_stale = (wrong_scope or 0.0) + (stale or 0.0); if scope_stale >= SCOPE_STALE_MAX -> block reason f"wrong_scope_plus_stale_at_threshold:{scope_stale:.4f}>={SCOPE_STALE_MAX}".
* missing_support = probs["missing_support"] or 0.0; if missing_support >= MISSING_SUPPORT_MAX -> block reason f"missing_support_at_threshold:{missing_support:.4f}>={MISSING_SUPPORT_MAX}".
* direct_conflict = probs["direct_conflict"]; if direct_conflict is None -> block reason "missing_direct_conflict_probability" (fail closed: Stage 1 cannot certify a clear without the conflict head, and Stage 2 entry requires a measured flag).
* If any block reasons: decision "block". Elif direct_conflict <= DIRECT_CONFLICT_CLEAR_MAX: decision "clear", reasons []. Else: decision "route_stage2", reasons [f"direct_conflict_flagged:{direct_conflict:.4f}>{DIRECT_CONFLICT_CLEAR_MAX}"].

NOTE: Per RUL-20261113-dec-gate-design Q1, the supported question uses
factual entailment (not verbatim identity) and claims are packaged with
their full cited evidence chain by ev.decisions_prep.n3_evidence_packaging;
gate thresholds unchanged.

Two-stage re-eval bars: contradicted recall 100%, planted-flaw recall >= 92%,
multi-artifact synthesis recall 100%, clean false-block <= 5%,
multi-artifact clean false-block <= 1/14.

This module is pure: no network, no environment reads, no API calls anywhere.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence, Union

MODEL = "gemini-decision-1"

# Gate thresholds, verbatim from the ruling.
SUPPORTED_MIN = 0.90          # block if P(supported) < 0.90
SCOPE_STALE_MAX = 0.25        # block if P(wrong_scope) + P(stale) >= 0.25
MISSING_SUPPORT_MAX = 0.35       # block if P(missing_support) >= 0.35
DIRECT_CONFLICT_CLEAR_MAX = 0.05 # clear if P(direct_conflict) <= 0.05, else route to Stage 2
CONFIRM_DIRECT_CONFLICT_MAX = 0.20  # Stage 2: block iff P_confirm(direct_conflict) > 0.20
CONFIRM_QUESTION_ID = "confirm_direct_conflict"  # Stage 2 isolated confirm question ID

QUESTION_IDS: tuple[str, ...] = (
    "supported",
    "direct_conflict",
    "missing_support",
    "wrong_scope",
    "stale",
)

ClaimLike = Union[str, Mapping[str, Any]]

# ---------------------------------------------------------------------------
# Deterministic numeric extraction
# ---------------------------------------------------------------------------

# One pass, three literal shapes: currency ($1,276 / $30 / $0), percentages
# (80.0% / 2%), and plain literals (182,386 / 0.445 / 386). Alternation order
# matters: currency and percent consume their sigils so the plain branch never
# re-reads the same digits.
_NUMBER_RE = re.compile(
    r"""
    (?P<currency>\$\s?\d[\d,]*(?:\.\d+)?)
    | (?P<percent>\d+(?:\.\d+)?\s?%)
    | (?P<plain>\d[\d,]*(?:\.\d+)?)
    """,
    re.VERBOSE,
)

# The unit/context word that may follow a literal: "quotes", "rows", "/mo",
# "-sided" suffixes are part of the number's own span only when attached to a
# following word via '/' or directly. Kept deliberately small and literal so
# extraction stays deterministic and reviewable.
_UNIT_RE = re.compile(r"\s?/?([A-Za-z][A-Za-z-]*)")


def _literal_value(raw: str) -> float:
    digits = raw.replace("$", "").replace("%", "").replace(",", "").strip()
    return float(digits)


def extract_numeric_claims(text: str) -> list[tuple[float, str]]:
    """Extract literal numbers from claim text, deterministically.

    Returns a list of ``(value, span)`` tuples in text order, where ``value``
    is the numeric value as a float and ``span`` is the matched literal plus
    its unit/context word when one directly follows (e.g. ``"182,386
    quotes"``, ``"80.0%"``, ``"$39/mo"``, ``"386 players"``). A range such as
    "3-10%" yields both endpoints as separate literals. No inference: a
    number that is not written literally in the text is never produced.
    """
    if not text:
        return []
    out: list[tuple[float, str]] = []
    for m in _NUMBER_RE.finditer(text):
        raw = m.group(0)
        span = raw
        # A percent literal's unit is the % itself; only currency and plain
        # literals absorb a following unit/context word.
        if m.lastgroup != "percent":
            unit = _UNIT_RE.match(text, m.end())
            if unit is not None:
                span = raw + unit.group(0)
        out.append((_literal_value(raw), span))
    return out


# ---------------------------------------------------------------------------
# Deterministic check against a structured manifest
# ---------------------------------------------------------------------------


def _manifest_expected(entry: Any) -> list[float]:
    """Normalize one manifest entry to a list of expected values.

    Accepted shapes: a plain list of numbers, or a mapping with an
    ``"expected"`` (or ``"numbers"``) list whose items are numbers or
    mappings carrying a ``"value"``.
    """
    if entry is None:
        return []
    items: Sequence[Any]
    if isinstance(entry, Mapping):
        items = entry.get("expected", entry.get("numbers", []))  # type: ignore[arg-type]
    else:
        items = entry
    values: list[float] = []
    for item in items:  # type: ignore[union-attr]
        if isinstance(item, Mapping):
            values.append(float(item["value"]))
        else:
            values.append(float(item))
    return values


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(1e-9, 1e-6 * abs(b))


def check_numbers(
    claim: ClaimLike, manifest: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Compare a claim's literal numbers against a structured manifest.

    ``manifest`` maps claim_id -> expected literal values (see
    ``_manifest_expected`` for accepted shapes). The deterministic half of
    the N3 gate only has an opinion where a manifest entry exists: a claim
    with no entry returns no mismatches.

    Matching is a multiset comparison with a small relative tolerance:
    every expected value must be consumed by a distinct extracted literal,
    and every extracted literal must be consumed by an expected value.
    Returns a list of mismatch dicts:

    - ``{"claim_id", "type": "missing_expected", "value"}`` -- the manifest
      expects this literal and the claim text does not state it.
    - ``{"claim_id", "type": "unexpected_literal", "value", "span"}`` --
      the claim states a literal the manifest does not expect.
    """
    if isinstance(claim, Mapping):
        claim_id = claim.get("claim_id")
        text = str(claim.get("claim_text", ""))
    else:
        claim_id = None
        text = str(claim)
    if claim_id is None or claim_id not in manifest:
        return []

    expected = _manifest_expected(manifest[claim_id])
    extracted = extract_numeric_claims(text)

    remaining = list(extracted)
    mismatches: list[dict[str, Any]] = []
    for want in expected:
        hit = next((i for i, (val, _span) in enumerate(remaining) if _close(val, want)), None)
        if hit is None:
            mismatches.append(
                {"claim_id": claim_id, "type": "missing_expected", "value": want}
            )
        else:
            remaining.pop(hit)
    for val, span in remaining:
        mismatches.append(
            {
                "claim_id": claim_id,
                "type": "unexpected_literal",
                "value": val,
                "span": span,
            }
        )
    return mismatches


# ---------------------------------------------------------------------------
# Decisions question pack (envelope style of the merged client)
# ---------------------------------------------------------------------------

_QUESTION_INSTRUCTIONS: dict[str, str] = {
    "supported": (
        "What is the probability that the claim is factually entailed by the "
        "evidence -- the evidence establishes every fact the claim asserts, the "
        "claim asserts no fact the evidence does not establish, and the claim "
        "omits no caveat in the evidence that materially changes the claim's "
        "meaning? A faithful paraphrase of the evidence counts as supported; "
        "verbatim wording is not required."
    ),
    "direct_conflict": (
        "What is the probability that an attached artifact explicitly asserts "
        "a fact, number, date, or condition about the exact same entity, "
        "metric, and time window as the claim that is logically incompatible "
        "with the claim? Numbers or facts from different metrics, cohorts, "
        "dates, or unrelated sections of the attached artifacts are NOT "
        "direct conflicts and must not raise this probability."
    ),
    "missing_support": (
        "What is the probability that the attached evidence chain fails to "
        "establish a fact the claim asserts, or omits a caveat in the "
        "evidence that materially changes the claim's meaning, WITHOUT any "
        "attached artifact asserting an incompatible fact? This covers "
        "unsupported assertions and dropped load-bearing caveats, not direct "
        "contradictions."
    ),
    "wrong_scope": (
        "What is the probability that the claim's underlying fact is real "
        "but asserted about the wrong population or scope -- a different "
        "league, book, subset, surface, plan, cohort, or time window than "
        "the evidence covers?"
    ),
    "stale": (
        "What is the probability that the claim was true at an earlier date "
        "but has been superseded by a later event, merge, price change, or "
        "correction recorded in the evidence?"
    ),
}

CONFIRM_INSTRUCTION: str = (
    "Considering only the attached passages, what is the probability that an "
    "attached passage explicitly asserts a fact, number, date, or condition "
    "about the exact same entity, metric, and time window as the claim that is "
    "logically incompatible with the claim (a direct contradiction)? Passages "
    "about different metrics, cohorts, dates, or unrelated sections do not "
    "count, and a passage that merely fails to support the claim does not count."
)


def build_questions(claim: ClaimLike, evidence_text: str) -> dict[str, Any]:
    """Build the Decisions request body for one claim audit.

    Published envelope style (merged client, nba/minutes_restriction/
    decisions_client.py): ``{"model", "input", "questions"}`` where
    ``questions`` is a MAP keyed by question id. Each N3 question is a
    ``noul`` question -- a 0..1 probability question -- one per failure
    class plus entailment. This function only constructs the request; it
    makes no API call.
    """
    if isinstance(claim, Mapping):
        claim_text = str(claim.get("claim_text", ""))
        claim_id = claim.get("claim_id")
        source_doc = claim.get("source_doc")
    else:
        claim_text = str(claim)
        claim_id = None
        source_doc = None

    lines = ["Research claim under audit:"]
    if claim_id:
        lines.append(f"Claim ID: {claim_id}")
    lines.append(f"Claim: {claim_text}")
    if source_doc:
        lines.append(f"Cited source: {source_doc}")
    lines.append("")
    lines.append("Evidence (the material the claim must be judged against):")
    lines.append(evidence_text or "(no evidence text supplied)")

    return {
        "model": MODEL,
        "input": "\n".join(lines),
        "questions": {
            qid: {"type": "noul", "instructions": _QUESTION_INSTRUCTIONS[qid]}
            for qid in QUESTION_IDS
        },
    }


def build_confirm_questions(claim: ClaimLike, slice_text: str) -> dict[str, Any]:
    """Build the Decisions request body for a Stage 2 isolated confirm pass.

    Same envelope and claim-header lines as build_questions, but uses the
    evidence header 'Evidence (focused confirm slice):' and asks only the
    CONFIRM_QUESTION_ID question.
    """
    if isinstance(claim, Mapping):
        claim_text = str(claim.get("claim_text", ""))
        claim_id = claim.get("claim_id")
        source_doc = claim.get("source_doc")
    else:
        claim_text = str(claim)
        claim_id = None
        source_doc = None

    lines = ["Research claim under audit:"]
    if claim_id:
        lines.append(f"Claim ID: {claim_id}")
    lines.append(f"Claim: {claim_text}")
    if source_doc:
        lines.append(f"Cited source: {source_doc}")
    lines.append("")
    lines.append("Evidence (focused confirm slice):")
    lines.append(slice_text or "(no evidence text supplied)")

    return {
        "model": MODEL,
        "input": "\n".join(lines),
        "questions": {
            CONFIRM_QUESTION_ID: {
                "type": "noul",
                "instructions": CONFIRM_INSTRUCTION,
            }
        },
    }


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _probability(answer: Mapping[str, Any], qid: str) -> Optional[float]:
    """Read one question's probability from an answers-style mapping.

    Accepts a flat ``{qid: float}`` mapping or the published response shape
    ``{qid: {"probability": p}}`` (``"p"`` also accepted). Anything else is
    treated as missing (None), never guessed.
    """
    raw = answer.get(qid)
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, Mapping):
        for key in ("probability", "p"):
            val = raw.get(key)
            if isinstance(val, (int, float)):
                return float(val)
    return None


def gate(answer: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the Stage 1 routing gate to one claim's Decisions answers.

    Stage 1 routing rule:
    * supported is None -> block reason "missing_supported_probability".
    * elif supported < SUPPORTED_MIN -> block reason f"supported_below_threshold:{supported:.4f}<{SUPPORTED_MIN}".
    * scope_stale = (wrong_scope or 0.0) + (stale or 0.0); if scope_stale >= SCOPE_STALE_MAX -> block reason f"wrong_scope_plus_stale_at_threshold:{scope_stale:.4f}>={SCOPE_STALE_MAX}".
    * missing_support = probs["missing_support"] or 0.0; if missing_support >= MISSING_SUPPORT_MAX -> block reason f"missing_support_at_threshold:{missing_support:.4f}>={MISSING_SUPPORT_MAX}".
    * direct_conflict = probs["direct_conflict"]; if direct_conflict is None -> block reason "missing_direct_conflict_probability" (fail closed: Stage 1 cannot certify a clear without the conflict head, and Stage 2 entry requires a measured flag).
    * If any block reasons: decision "block". Elif direct_conflict <= DIRECT_CONFLICT_CLEAR_MAX: decision "clear", reasons []. Else: decision "route_stage2", reasons [f"direct_conflict_flagged:{direct_conflict:.4f}>{DIRECT_CONFLICT_CLEAR_MAX}"].

    Returns {"decision": decision, "blocked": decision == "block", "routed": decision == "route_stage2", "reasons": reasons, "probabilities": probs}.
    """
    probs = {qid: _probability(answer, qid) for qid in QUESTION_IDS}
    reasons: list[str] = []

    supported = probs["supported"]
    if supported is None:
        reasons.append("missing_supported_probability")
    elif supported < SUPPORTED_MIN:
        reasons.append(f"supported_below_threshold:{supported:.4f}<{SUPPORTED_MIN}")

    scope_stale = (probs["wrong_scope"] or 0.0) + (probs["stale"] or 0.0)
    if scope_stale >= SCOPE_STALE_MAX:
        reasons.append(
            f"wrong_scope_plus_stale_at_threshold:{scope_stale:.4f}>={SCOPE_STALE_MAX}"
        )

    missing_support = probs["missing_support"] or 0.0
    if missing_support >= MISSING_SUPPORT_MAX:
        reasons.append(
            f"missing_support_at_threshold:{missing_support:.4f}>={MISSING_SUPPORT_MAX}"
        )

    direct_conflict = probs["direct_conflict"]
    if direct_conflict is None:
        reasons.append("missing_direct_conflict_probability")

    if reasons:
        decision = "block"
    elif direct_conflict <= DIRECT_CONFLICT_CLEAR_MAX:
        decision = "clear"
        reasons = []
    else:
        decision = "route_stage2"
        reasons = [
            f"direct_conflict_flagged:{direct_conflict:.4f}>{DIRECT_CONFLICT_CLEAR_MAX}"
        ]

    return {
        "decision": decision,
        "blocked": decision == "block",
        "routed": decision == "route_stage2",
        "reasons": reasons,
        "probabilities": probs,
    }


def confirm_gate(answer: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the Stage 2 isolated confirm gate to Decisions answers.

    Stage 2 rule: record CONTRADICTED iff P_confirm > 0.20
    (CONFIRM_DIRECT_CONFLICT_MAX).

    If P(confirm_direct_conflict) is None: blocked True, reason
    "missing_confirm_probability".
    Elif P > CONFIRM_DIRECT_CONFLICT_MAX: blocked True, reason
    f"confirm_direct_conflict_above_threshold:{p:.4f}>{CONFIRM_DIRECT_CONFLICT_MAX}".
    Else: blocked False.

    Returns:
        {"decision": "block" if blocked else "clear", "blocked": blocked,
         "reasons": reasons, "probabilities": {CONFIRM_QUESTION_ID: p}}
    """
    p = _probability(answer, CONFIRM_QUESTION_ID)
    reasons: list[str] = []
    if p is None:
        blocked = True
        reasons.append("missing_confirm_probability")
    elif p > CONFIRM_DIRECT_CONFLICT_MAX:
        blocked = True
        reasons.append(
            f"confirm_direct_conflict_above_threshold:{p:.4f}>{CONFIRM_DIRECT_CONFLICT_MAX}"
        )
    else:
        blocked = False

    return {
        "decision": "block" if blocked else "clear",
        "blocked": blocked,
        "reasons": reasons,
        "probabilities": {CONFIRM_QUESTION_ID: p},
    }
