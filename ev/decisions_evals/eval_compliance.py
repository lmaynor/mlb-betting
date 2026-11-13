"""Offline fixture-evaluation script for publication compliance gate.

Evaluates the committed fixtures in tests/fixtures/decisions/compliance_gate/:
1. explanations.jsonl (100 items: 50 clean, 50 hallucinated)
2. social_drafts.jsonl (40 items: 20 clean, 20 tout)

Cache-backed execution flow:
- Answers for uncached items are fetched through the Decisions eval harness
  (run_batched + Pacer + live_client) and cached to disk as JSONL.
- CRITICAL: Answers are NEVER synthesized, simulated, or fabricated from labels.
  If GEMINI_API_KEY is unset and uncached items remain, this module raises
  RuntimeError immediately.
- Scoring is evaluated directly from cached Decisions answers and deterministic
  number verification.
- Writes eval_results.json to the fixture directory and prints summary.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from ev.decisions_evals.common import AnswersCache, Pacer, live_client, run_batched
from mlb_core.alerts.compliance_gate import (
    build_explanation_input,
    build_explanation_questions,
    build_social_input,
    build_social_questions,
    gate_explanation,
    gate_social,
    verify_numbers,
)

FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "decisions"
    / "compliance_gate"
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def evaluate_explanations(fixture_dir: Path) -> dict[str, Any]:
    """Fetch answers for and score explanations fixture items."""
    jsonl_path = fixture_dir / "explanations.jsonl"
    cache_path = fixture_dir / "answers_explanations.jsonl"

    items = _load_jsonl(jsonl_path)
    cache = AnswersCache(cache_path)

    uncached = [it for it in items if it["item_id"] not in cache]
    if uncached:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                f"GEMINI_API_KEY is unset and {len(uncached)} uncached explanation "
                f"items remain in {cache_path.name}. Decisions eval code must only "
                "score live Decisions answers or cached live answers. Never synthesize, "
                "simulate, or fabricate answers from labels."
            )
        client = live_client()
        pacer = Pacer()
        items_to_run = [
            (build_explanation_input(it), build_explanation_questions(it))
            for it in uncached
        ]
        batched_results = run_batched(client, items_to_run, pacer=pacer)
        for idx, it in enumerate(uncached):
            cache.store(it["item_id"], batched_results[idx])

    # Direct scoring from cached answers and verify_numbers
    total_clean = 0
    clean_false_rejections = 0
    total_hallucinated = 0
    combined_hallucinated_rejections = 0
    entailment_only_rejections = 0
    number_layer_catches = 0

    for it in items:
        label = it.get("label")
        exp_text = it.get("explanation", "")
        row = it.get("row", {})

        num_res = verify_numbers(exp_text, row)
        number_failed = not num_res["ok"]

        cached_answers = cache.get(it["item_id"], {})
        gate_res = gate_explanation(it, cached_answers)
        entailment_rejected = gate_res["rejected"]

        combined_rejected = number_failed or entailment_rejected

        if label == "clean":
            total_clean += 1
            if combined_rejected:
                clean_false_rejections += 1
        else:
            total_hallucinated += 1
            if number_failed:
                number_layer_catches += 1
            if entailment_rejected:
                entailment_only_rejections += 1
            if combined_rejected:
                combined_hallucinated_rejections += 1

    combined_rejection_rate = (
        combined_hallucinated_rejections / total_hallucinated
        if total_hallucinated > 0
        else 0.0
    )
    entailment_rejection_rate = (
        entailment_only_rejections / total_hallucinated
        if total_hallucinated > 0
        else 0.0
    )
    clean_false_rejection_rate = (
        clean_false_rejections / total_clean if total_clean > 0 else 0.0
    )

    return {
        "total_items": len(items),
        "clean_count": total_clean,
        "hallucinated_count": total_hallucinated,
        "combined_rejections": combined_hallucinated_rejections,
        "combined_rejection_rate": round(combined_rejection_rate, 4),
        "entailment_only_rejections": entailment_only_rejections,
        "entailment_rejection_rate": round(entailment_rejection_rate, 4),
        "number_layer_catches": number_layer_catches,
        "clean_false_rejections": clean_false_rejections,
        "clean_false_rejection_rate": round(clean_false_rejection_rate, 4),
        "pass_target_met": combined_rejection_rate >= 1.0,
    }


def evaluate_social(fixture_dir: Path) -> dict[str, Any]:
    """Fetch answers for and score social drafts fixture items."""
    jsonl_path = fixture_dir / "social_drafts.jsonl"
    cache_path = fixture_dir / "answers_social.jsonl"

    items = _load_jsonl(jsonl_path)
    cache = AnswersCache(cache_path)

    uncached = [it for it in items if it["item_id"] not in cache]
    if uncached:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                f"GEMINI_API_KEY is unset and {len(uncached)} uncached social draft "
                f"items remain in {cache_path.name}. Decisions eval code must only "
                "score live Decisions answers or cached live answers. Never synthesize, "
                "simulate, or fabricate answers from labels."
            )
        client = live_client()
        pacer = Pacer()
        items_to_run = [
            (build_social_input(it), build_social_questions(it))
            for it in uncached
        ]
        batched_results = run_batched(client, items_to_run, pacer=pacer)
        for idx, it in enumerate(uncached):
            cache.store(it["item_id"], batched_results[idx])

    # Direct scoring from cached answers and verify_numbers
    total_clean = 0
    clean_false_rejections = 0
    total_tout = 0
    tout_rejections = 0

    for it in items:
        label = it.get("label")
        draft_text = it.get("draft_text", "")
        row = it.get("row", {})

        num_res = verify_numbers(draft_text, row)
        number_failed = not num_res["ok"]

        cached_answers = cache.get(it["item_id"], {})
        gate_res = gate_social(it, cached_answers)
        social_rejected = gate_res["rejected"]

        combined_rejected = number_failed or social_rejected

        if label == "clean":
            total_clean += 1
            if combined_rejected:
                clean_false_rejections += 1
        else:
            total_tout += 1
            if combined_rejected:
                tout_rejections += 1

    tout_rejection_rate = (
        tout_rejections / total_tout if total_tout > 0 else 0.0
    )
    clean_false_rejection_rate = (
        clean_false_rejections / total_clean if total_clean > 0 else 0.0
    )

    return {
        "total_items": len(items),
        "clean_count": total_clean,
        "tout_count": total_tout,
        "tout_rejections": tout_rejections,
        "tout_rejection_rate": round(tout_rejection_rate, 4),
        "clean_false_rejections": clean_false_rejections,
        "clean_false_rejection_rate": round(clean_false_rejection_rate, 4),
        "pass_target_met": tout_rejection_rate >= 1.0,
    }


def run_eval(fixture_dir: Optional[Path] = None) -> dict[str, Any]:
    """Execute evaluation over explanations and social drafts fixtures."""
    target_dir = fixture_dir or FIXTURE_DIR
    exp_report = evaluate_explanations(target_dir)
    social_report = evaluate_social(target_dir)

    results = {
        "gate": "publication_compliance_gate",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "explanations": exp_report,
        "social": social_report,
    }

    results_file = target_dir / "eval_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True)
        f.write("\n")

    print("\n=======================================================")
    print(" PUBLICATION COMPLIANCE GATE EVALUATION RESULTS")
    print("=======================================================")
    print(f"Results written to: {results_file}")
    print("\n[Explanations Gate (noul entailment)]")
    print(f"  Total items:              {exp_report['total_items']}")
    print(f"  Clean items:              {exp_report['clean_count']}")
    print(f"  Hallucinated items:       {exp_report['hallucinated_count']}")
    print(f"  Number-layer catches:     {exp_report['number_layer_catches']}")
    print(f"  Entailment-only reject:   {exp_report['entailment_rejection_rate']:.1%}")
    print(f"  Combined gate reject:     {exp_report['combined_rejection_rate']:.1%} (Bar: 100%)")
    print(f"  Clean false-reject rate:  {exp_report['clean_false_rejection_rate']:.1%}")
    print("\n[Social Drafts Gate (choice tout classification)]")
    print(f"  Total items:              {social_report['total_items']}")
    print(f"  Clean items:              {social_report['clean_count']}")
    print(f"  Tout items:               {social_report['tout_count']}")
    print(f"  Tout reject rate:         {social_report['tout_rejection_rate']:.1%}")
    print(f"  Clean false-reject rate:  {social_report['clean_false_rejection_rate']:.1%}")
    print("=======================================================\n")

    return results


def main() -> None:
    run_eval()


if __name__ == "__main__":
    main()
