"""Publication compliance gate: two-layer verification for bets copy.

DOCTRINE:
- Deterministic verification FIRST: every number in the draft (lines, odds,
  projections, percentages, stats) must trace to the attached card_locked / bets
  row. Foreign numbers (e.g. unmodeled weather, unattached stats, structural
  digits) fail at this layer immediately without consuming API quota.
- Decisions API SECOND: prose only. Evaluates compliance classification (for
  social drafts) and factor entailment (for explanations).
- Rejection threshold: P(unsupported_claim) > 0.15 rejects.
- Fallback doctrine: rejected drafts fall back to the deterministic plain-text
  template (fallback_template) or block rendering. The gate is shadow by
  default and NEVER edits or rewrites draft text.
- Fail-closed behavior: structural numbers not traceable to the row (such as
  the '1' in 'Game 1') will intentionally false-positive and fail the number
  gate; this is intended fail-closed gate doctrine. Any network, transport,
  or schema failure abstains and rejects the batch.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping, Optional, Sequence, Union

from ev.decisions_client import DecisionsClient, ItemBatch
from ev.decisions_common import (
    MAX_INPUT_TOKENS,
    append_records,
    estimate_tokens,
    make_record,
    route_mode,
)

UNSUPPORTED_THRESHOLD: float = 0.15
DEFAULT_MODE: str = "shadow"
ENV_MODE_VAR: str = "DECISIONS_COMPLIANCE_MODE"

# Regex matching numbers in text (including signed odds, decimals, percentages, commas)
_NUMBER_RE = re.compile(
    r"(?<![a-zA-Z])[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)"
)


def extract_numbers(text: str) -> set[float]:
    """Extract all numbers from text as floats.

    Finds integers, decimals, percents, +/- odds, fractions (e.g. 7.5, .512,
    +105, -115, 24.1%, 1,000). Normalizes by stripping commas and converting
    to float.
    """
    if not text or not isinstance(text, str):
        return set()
    nums: set[float] = set()
    for match in _NUMBER_RE.findall(text):
        cleaned = match.replace(",", "")
        try:
            nums.add(float(cleaned))
        except ValueError:
            pass
    return nums


def row_numbers(row: Mapping[str, Any]) -> set[float]:
    """Collect all valid numeric values traceable to a row.

    Sources:
    - Numeric fields: odds, line, model_prob, edge, projection
    - Percent renderings: model_prob * 100, edge * 100 (and their rounded forms)
    - Absolute odds: abs(odds)
    - All numbers appearing inside row's factors strings
    - All numbers appearing inside row's bet_type string
    """
    nums: set[float] = set()
    if not isinstance(row, Mapping):
        return nums

    for field in ("odds", "line", "model_prob", "edge", "projection"):
        val = row.get(field)
        if val is not None:
            try:
                fval = float(val)
                nums.add(fval)
                if field == "odds":
                    nums.add(abs(fval))
                elif field in ("model_prob", "edge"):
                    pct = fval * 100.0
                    nums.add(pct)
                    nums.add(round(pct, 4))
                    nums.add(round(pct, 2))
                    nums.add(round(pct, 1))
            except (ValueError, TypeError):
                pass

    factors = row.get("factors")
    if isinstance(factors, (list, tuple)):
        for f in factors:
            if isinstance(f, str):
                nums.update(extract_numbers(f))
    elif isinstance(factors, str):
        nums.update(extract_numbers(factors))

    bet_type = row.get("bet_type")
    if isinstance(bet_type, str):
        nums.update(extract_numbers(bet_type))

    return nums


def verify_numbers(text: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministically verify that every number in text traces to the row.

    Matches floats within 0.011 tolerance. Structural numbers not present
    in the row (e.g. '1' in 'Game 1') are intentional fail-closed rejections.
    """
    text_nums = extract_numbers(text)
    r_nums = row_numbers(row)
    unknown: list[float] = []
    for tn in sorted(text_nums):
        if not any(abs(tn - rn) <= 0.011 for rn in r_nums):
            unknown.append(tn)
    return {
        "ok": len(unknown) == 0,
        "unknown_numbers": unknown,
    }


def build_explanation_questions(item: Mapping[str, Any]) -> dict[str, Any]:
    """Build the single factor entailment question for explanation review."""
    return {
        "factor_entailment": {
            "type": "noul",
            "instructions": (
                "noul is the probability that the explanation CITES OR RELIES ON "
                "at least one factor, condition, or circumstance that is NOT present "
                "in the attached model row's factor list or numeric fields (an unsupported claim). "
                "Citing only listed factors/numbers = near 0. "
                "Weather, injuries, trends, streaks, pace, or any circumstance absent from the row = near 1. "
                "Paraphrase of a listed factor is supported."
            ),
        }
    }


def build_explanation_input(item: Mapping[str, Any]) -> str:
    """Render row JSON and explanation under review."""
    row = item.get("row") if isinstance(item, Mapping) and "row" in item else item
    explanation = ""
    if isinstance(item, Mapping):
        explanation = str(item.get("explanation", ""))
    row_json = json.dumps(row, indent=1, sort_keys=True)
    return f"{row_json}\n\nExplanation under review:\n{explanation}"


def gate_explanation(item: Mapping[str, Any], answers: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate Decisions factor entailment answer against UNSUPPORTED_THRESHOLD."""
    item_id = str(item.get("item_id", "")) if isinstance(item, Mapping) else ""
    raw = answers.get("factor_entailment") if isinstance(answers, Mapping) else None
    p = DecisionsClient.noul_value(raw)
    if p is None:
        return {
            "item_id": item_id,
            "rejected": True,
            "p_unsupported": None,
            "reason": "entailment_abstained",
        }
    if p > UNSUPPORTED_THRESHOLD:
        return {
            "item_id": item_id,
            "rejected": True,
            "p_unsupported": p,
            "reason": "unsupported_claim",
        }
    return {
        "item_id": item_id,
        "rejected": False,
        "p_unsupported": p,
        "reason": "entailed",
    }


def build_social_questions(item: Mapping[str, Any]) -> dict[str, Any]:
    """Build the single compliance classification choice question for social drafts."""
    return {
        "compliance_class": {
            "type": "choice",
            "instructions": "Classify the social draft's framing.",
            "criteria": {
                "compliant": "plain factual card language: the play, price, book, model probability, projection; no hype",
                "tout": "tout/hype framing: locks, guarantees, free money, can't miss, smash/hammer it, bet of the year, or trend/streak/recent-form hype ('on fire', 'streak', 'hot hand', 'last ten')",
            },
        }
    }


def build_social_input(item: Mapping[str, Any]) -> str:
    """Render row JSON and social draft under review."""
    row = item.get("row") if isinstance(item, Mapping) and "row" in item else item
    draft_text = ""
    if isinstance(item, Mapping):
        draft_text = str(item.get("draft_text", item.get("text", "")))
    row_json = json.dumps(row, indent=1, sort_keys=True)
    return f"{row_json}\n\nSocial draft under review:\n{draft_text}"


def gate_social(item: Mapping[str, Any], answers: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate Decisions compliance choice answer."""
    item_id = str(item.get("item_id", "")) if isinstance(item, Mapping) else ""
    raw = answers.get("compliance_class") if isinstance(answers, Mapping) else None
    if raw is None:
        return {
            "item_id": item_id,
            "rejected": True,
            "pick": None,
            "p_tout": None,
            "reason": "social_abstained",
        }
    pick = DecisionsClient.choice_pick(raw)
    probs = DecisionsClient.probabilities(raw) or {}
    p_tout = probs.get("tout")

    if pick is None and not probs:
        return {
            "item_id": item_id,
            "rejected": True,
            "pick": None,
            "p_tout": None,
            "reason": "social_abstained",
        }

    if pick == "tout" or (probs and probs.get("tout", 0.0) > 0.5):
        return {
            "item_id": item_id,
            "rejected": True,
            "pick": pick,
            "p_tout": p_tout,
            "reason": "tout",
        }

    return {
        "item_id": item_id,
        "rejected": False,
        "pick": pick,
        "p_tout": p_tout,
        "reason": "compliant",
    }


def fallback_template(row: Mapping[str, Any]) -> str:
    """Generate deterministic plain-text fallback from a row."""
    if not isinstance(row, Mapping):
        return ""
    player = str(row.get("player", "Unknown")).strip()
    bet_type = str(row.get("bet_type", "")).strip()

    odds_val = row.get("odds")
    odds_str = ""
    if odds_val is not None:
        try:
            odds_str = f"{int(float(odds_val)):+d}"
        except (ValueError, TypeError):
            odds_str = str(odds_val)

    book = str(row.get("book", "")).strip()

    model_prob = row.get("model_prob")
    prob_str = ""
    if model_prob is not None:
        try:
            prob_str = f"{float(model_prob):.0%}"
        except (ValueError, TypeError):
            prob_str = str(model_prob)

    line = row.get("line")
    line_str = str(line) if line is not None else ""

    head = f"{player} {bet_type}".strip()
    if odds_str:
        head = f"{head} at {odds_str}"
    if book:
        head = f"{head} ({book})"

    tail = ""
    if prob_str and line_str:
        tail = f"Model probability {prob_str} vs line {line_str}."
    elif prob_str:
        tail = f"Model probability {prob_str}."
    elif line_str:
        tail = f"Line {line_str}."

    if head and tail:
        return f"{head}. {tail}"
    elif head:
        return f"{head}."
    return tail


def check_draft(
    text: str,
    row: Mapping[str, Any],
    *,
    kind: str,
    client: Optional[DecisionsClient] = None,
    mode: Optional[str] = None,
) -> dict[str, Any]:
    """Run full compliance pipeline for ONE draft.

    1. Layer 1: Deterministic verification first. If numbers fail, returns
       stage='numbers' WITHOUT calling the Decisions API.
    2. Layer 2: Decisions API evaluation.
    """
    if kind not in ("explanation", "social"):
        raise ValueError(f"Unknown kind {kind!r}; must be 'explanation' or 'social'")

    eff_mode = mode if mode is not None else route_mode(ENV_MODE_VAR)
    applied = (eff_mode == "live")
    item_id = str(row.get("item_id", "draft_1")) if isinstance(row, Mapping) else "draft_1"

    # Layer 1: Numbers verification
    num_res = verify_numbers(text, row)
    if not num_res["ok"]:
        return {
            "item_id": item_id,
            "approved": False,
            "stage": "numbers",
            "unknown_numbers": num_res["unknown_numbers"],
            "reason": "untraced_numbers",
            "fallback": "template",
            "fallback_text": fallback_template(row),
            "mode": eff_mode,
            "applied": applied,
        }

    # Layer 2: Decisions API
    if client is None:
        try:
            client = DecisionsClient()
        except Exception as exc:
            return {
                "item_id": item_id,
                "approved": False,
                "stage": "decisions",
                "reason": f"client_init_failed: {exc}",
                "fallback": "template",
                "fallback_text": fallback_template(row),
                "mode": eff_mode,
                "applied": applied,
            }

    item = {
        "row": row,
        "explanation": text,
        "draft_text": text,
        "item_id": item_id,
    }
    if kind == "explanation":
        input_text = build_explanation_input(item)
        questions = build_explanation_questions(item)
    else:
        input_text = build_social_input(item)
        questions = build_social_questions(item)

    tokens = estimate_tokens(input_text)
    if tokens > 0.9 * MAX_INPUT_TOKENS:
        raise ValueError(
            f"Input tokens ({tokens}) exceeds 90% of MAX_INPUT_TOKENS ({int(0.9 * MAX_INPUT_TOKENS)})"
        )

    batch_res = client.call(input_text, questions)
    if batch_res.abstained:
        return {
            "item_id": item_id,
            "approved": False,
            "stage": "decisions",
            "reason": batch_res.abstention_reason or "abstained",
            "fallback": "template",
            "fallback_text": fallback_template(row),
            "mode": eff_mode,
            "applied": applied,
        }

    if kind == "explanation":
        verdict = gate_explanation(item, batch_res.answers)
        if verdict["rejected"]:
            return {
                "item_id": item_id,
                "approved": False,
                "stage": "decisions",
                "reason": verdict["reason"],
                "p_unsupported": verdict["p_unsupported"],
                "fallback": "template",
                "fallback_text": fallback_template(row),
                "mode": eff_mode,
                "applied": applied,
            }
        return {
            "item_id": item_id,
            "approved": True,
            "stage": "approved",
            "reason": verdict["reason"],
            "p_unsupported": verdict["p_unsupported"],
            "fallback": None,
            "fallback_text": None,
            "mode": eff_mode,
            "applied": applied,
        }
    else:
        verdict = gate_social(item, batch_res.answers)
        if verdict["rejected"]:
            return {
                "item_id": item_id,
                "approved": False,
                "stage": "decisions",
                "reason": verdict["reason"],
                "pick": verdict["pick"],
                "p_tout": verdict["p_tout"],
                "fallback": "template",
                "fallback_text": fallback_template(row),
                "mode": eff_mode,
                "applied": applied,
            }
        return {
            "item_id": item_id,
            "approved": True,
            "stage": "approved",
            "reason": verdict["reason"],
            "pick": verdict["pick"],
            "p_tout": verdict["p_tout"],
            "fallback": None,
            "fallback_text": None,
            "mode": eff_mode,
            "applied": applied,
        }


def _write_decision_records(
    log_path: Union[str, os.PathLike[str]],
    kind: str,
    mode: str,
    results: Sequence[dict[str, Any]],
) -> None:
    records = []
    for res in results:
        rec = make_record(
            route=f"compliance_gate_{kind}",
            item_id=str(res.get("item_id", "")),
            decision="approved" if res.get("approved") else "rejected",
            mode=mode,
            applied=bool(res.get("applied", False)),
            stage=res.get("stage"),
            reason=res.get("reason"),
        )
        records.append(rec)
    append_records(log_path, records)


def check_drafts(
    items: Sequence[Mapping[str, Any]],
    *,
    kind: str,
    client: Optional[DecisionsClient] = None,
    mode: Optional[str] = None,
    log_path: Optional[Union[str, os.PathLike[str]]] = None,
) -> list[dict[str, Any]]:
    """Run compliance gate across multiple items with a single batched Decisions call.

    1. Number-verify all items first: failures resolved deterministically
       and excluded from the batch.
    2. ONE ItemBatch call for all remaining items.
    3. Fail closed on batch abstention or client failure.
    4. Optional structured decision logging to log_path.
    """
    if kind not in ("explanation", "social"):
        raise ValueError(f"Unknown kind {kind!r}; must be 'explanation' or 'social'")

    eff_mode = mode if mode is not None else route_mode(ENV_MODE_VAR)
    applied = (eff_mode == "live")

    results: list[dict[str, Any]] = [{} for _ in range(len(items))]
    batched_indices: list[int] = []

    # Step 1: Number verification first for all items
    for i, item in enumerate(items):
        item_id = str(item.get("item_id", f"item_{i}"))
        row = item.get("row") if isinstance(item, Mapping) and "row" in item else item
        if kind == "explanation":
            text = str(item.get("explanation", item.get("text", "")))
        else:
            text = str(item.get("draft_text", item.get("text", item.get("explanation", ""))))

        num_res = verify_numbers(text, row)
        if not num_res["ok"]:
            results[i] = {
                "item_id": item_id,
                "approved": False,
                "stage": "numbers",
                "unknown_numbers": num_res["unknown_numbers"],
                "reason": "untraced_numbers",
                "fallback": "template",
                "fallback_text": fallback_template(row),
                "mode": eff_mode,
                "applied": applied,
            }
        else:
            batched_indices.append(i)

    # If no items passed number verification, return early
    if not batched_indices:
        if log_path is not None:
            _write_decision_records(log_path, kind, eff_mode, results)
        return results

    # Step 2: Prepare Decisions call for the rest
    if client is None:
        try:
            client = DecisionsClient()
        except Exception:
            for idx in batched_indices:
                item = items[idx]
                row = item.get("row") if isinstance(item, Mapping) and "row" in item else item
                item_id = str(item.get("item_id", f"item_{idx}"))
                results[idx] = {
                    "item_id": item_id,
                    "approved": False,
                    "stage": "decisions",
                    "reason": "batch_abstained",
                    "fallback": "template",
                    "fallback_text": fallback_template(row),
                    "mode": eff_mode,
                    "applied": applied,
                }
            if log_path is not None:
                _write_decision_records(log_path, kind, eff_mode, results)
            return results

    batch = ItemBatch()
    tag_to_idx: dict[str, int] = {}

    for idx in batched_indices:
        item = items[idx]
        if kind == "explanation":
            input_block = build_explanation_input(item)
            questions = build_explanation_questions(item)
        else:
            input_block = build_social_input(item)
            questions = build_social_questions(item)
        tag = batch.add(input_block, questions)
        tag_to_idx[tag] = idx

    combined_input, wire_questions = batch.build()

    # Estimate tokens guard
    tokens = estimate_tokens(combined_input)
    if tokens > 0.9 * MAX_INPUT_TOKENS:
        raise ValueError(
            f"Batched input tokens ({tokens}) exceeds 90% of MAX_INPUT_TOKENS ({int(0.9 * MAX_INPUT_TOKENS)})"
        )

    # ONE ItemBatch call
    batch_res = client.call(combined_input, wire_questions)

    if batch_res.abstained:
        for idx in batched_indices:
            item = items[idx]
            row = item.get("row") if isinstance(item, Mapping) and "row" in item else item
            item_id = str(item.get("item_id", f"item_{idx}"))
            results[idx] = {
                "item_id": item_id,
                "approved": False,
                "stage": "decisions",
                "reason": "batch_abstained",
                "abstention_reason": batch_res.abstention_reason,
                "fallback": "template",
                "fallback_text": fallback_template(row),
                "mode": eff_mode,
                "applied": applied,
            }
    else:
        split_answers = batch.split(batch_res)
        for tag, idx in tag_to_idx.items():
            item = items[idx]
            row = item.get("row") if isinstance(item, Mapping) and "row" in item else item
            item_id = str(item.get("item_id", f"item_{idx}"))
            answers = split_answers.get(tag, {})

            if kind == "explanation":
                verdict = gate_explanation(item, answers)
                if verdict["rejected"]:
                    results[idx] = {
                        "item_id": item_id,
                        "approved": False,
                        "stage": "decisions",
                        "reason": verdict["reason"],
                        "p_unsupported": verdict["p_unsupported"],
                        "fallback": "template",
                        "fallback_text": fallback_template(row),
                        "mode": eff_mode,
                        "applied": applied,
                    }
                else:
                    results[idx] = {
                        "item_id": item_id,
                        "approved": True,
                        "stage": "approved",
                        "reason": verdict["reason"],
                        "p_unsupported": verdict["p_unsupported"],
                        "fallback": None,
                        "fallback_text": None,
                        "mode": eff_mode,
                        "applied": applied,
                    }
            else:
                verdict = gate_social(item, answers)
                if verdict["rejected"]:
                    results[idx] = {
                        "item_id": item_id,
                        "approved": False,
                        "stage": "decisions",
                        "reason": verdict["reason"],
                        "pick": verdict["pick"],
                        "p_tout": verdict["p_tout"],
                        "fallback": "template",
                        "fallback_text": fallback_template(row),
                        "mode": eff_mode,
                        "applied": applied,
                    }
                else:
                    results[idx] = {
                        "item_id": item_id,
                        "approved": True,
                        "stage": "approved",
                        "reason": verdict["reason"],
                        "pick": verdict["pick"],
                        "p_tout": verdict["p_tout"],
                        "fallback": None,
                        "fallback_text": None,
                        "mode": eff_mode,
                        "applied": applied,
                    }

    if log_path is not None:
        _write_decision_records(log_path, kind, eff_mode, results)

    return results


__all__ = [
    "UNSUPPORTED_THRESHOLD",
    "DEFAULT_MODE",
    "ENV_MODE_VAR",
    "extract_numbers",
    "row_numbers",
    "verify_numbers",
    "build_explanation_questions",
    "build_explanation_input",
    "gate_explanation",
    "build_social_questions",
    "build_social_input",
    "gate_social",
    "fallback_template",
    "check_draft",
    "check_drafts",
]
