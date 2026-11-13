"""Offline evaluation of the weather guidance-shift classifier against cycles.jsonl.

DOCTRINE:
The evaluation only scores live or cached-live Decisions API answers.
Without GEMINI_API_KEY and with uncached items, it RAISES rather than synthesizing
or simulating answers.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from ev.decisions_evals.common import AnswersCache, Pacer, live_client, run_batched
from ev.decisions_weather import build_input, build_questions, gate

FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "decisions" / "weather"
CYCLES_PATH = FIXTURE_DIR / "cycles.jsonl"
ANSWERS_PATH = FIXTURE_DIR / "answers.jsonl"
RESULTS_PATH = FIXTURE_DIR / "eval_results.json"


def evaluate_weather(
    cycles_path: Path = CYCLES_PATH,
    answers_path: Path = ANSWERS_PATH,
    results_path: Optional[Path] = RESULTS_PATH,
    *,
    client: Optional[Any] = None,
    pacer: Optional[Pacer] = None,
) -> dict[str, Any]:
    """Evaluate weather guidance shift classifier on cycle pairs.

    Reads items from cycles_path, checks answers_path cache for cached answers,
    fetches any uncached items via live DecisionsClient (or raises if no GEMINI_API_KEY),
    and scores guidance shift predictions against cycle labels.
    """
    items: list[dict[str, Any]] = []
    with open(cycles_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))

    cache = AnswersCache(answers_path)
    missing = [it for it in items if it["item_id"] not in cache]

    if missing:
        if client is None and not os.environ.get("GEMINI_API_KEY"):
            raise RuntimeError(
                f"GEMINI_API_KEY is not set and {len(missing)} uncached items remain. "
                "Refusing to synthesize or simulate answers. Real live or cached answers required."
            )

        active_client = client or live_client()
        active_pacer = pacer or Pacer()

        chunkable = [(build_input(item), build_questions(item)) for item in missing]
        results_by_idx = run_batched(active_client, chunkable, pacer=active_pacer)
        for idx, item in enumerate(missing):
            ans = results_by_idx.get(idx, {})
            cache.store(item["item_id"], ans)

    predictions: dict[str, str] = {}
    gate_results: dict[str, dict[str, Any]] = {}

    for item in items:
        item_id = item["item_id"]
        ans = cache.get(item_id, {})
        gate_res = gate(item, ans, market_direction=None)
        gate_results[item_id] = gate_res

        pick = gate_res.get("shift")
        probs = gate_res.get("p_shift") or {}
        p_pick = probs.get(pick, 0.0) if pick else 0.0

        if pick in ("warmer", "cooler") and p_pick >= 0.5:
            predicted_shift = pick
        elif pick == "steady" and p_pick >= 0.5:
            predicted_shift = "steady"
        else:
            predicted_shift = "steady"

        predictions[item_id] = predicted_shift

    shift_items = [it for it in items if it.get("label") in ("warmer", "cooler")]
    correct_shifts = sum(
        1 for it in shift_items if predictions[it["item_id"]] == it.get("label")
    )
    shift_recall = correct_shifts / len(shift_items) if shift_items else 0.0

    shift_attempted = [
        it for it in shift_items if predictions[it["item_id"]] in ("warmer", "cooler")
    ]
    direction_accuracy = (
        sum(1 for it in shift_attempted if predictions[it["item_id"]] == it.get("label"))
        / len(shift_attempted)
        if shift_attempted
        else 0.0
    )

    steady_items = [it for it in items if it.get("label") == "steady"]
    steady_fas = sum(
        1 for it in steady_items if predictions[it["item_id"]] in ("warmer", "cooler")
    )
    steady_false_alarm_rate = (
        steady_fas / len(steady_items) if steady_items else 0.0
    )

    classes = ("warmer", "cooler", "steady")
    confusion: dict[str, dict[str, int]] = {
        act: {pred: 0 for pred in classes} for act in classes
    }
    for it in items:
        act = it.get("label", "steady")
        pred = predictions[it["item_id"]]
        if act in confusion and pred in confusion[act]:
            confusion[act][pred] += 1

    conf_by_label: dict[str, list[float]] = {lbl: [] for lbl in classes}
    for it in items:
        act = it.get("label")
        score = gate_results[it["item_id"]].get("confidence_score")
        if score is not None and act in conf_by_label:
            conf_by_label[act].append(float(score))

    mean_confidence = {
        lbl: (round(sum(vals) / len(vals), 4) if vals else None)
        for lbl, vals in conf_by_label.items()
    }

    eval_results = {
        "n_total": len(items),
        "n_shift": len(shift_items),
        "n_steady": len(steady_items),
        "shift_recall": round(shift_recall, 4),
        "direction_accuracy": round(direction_accuracy, 4),
        "steady_false_alarm_rate": round(steady_false_alarm_rate, 4),
        "per_class_confusion_counts": confusion,
        "mean_confidence_score_by_label": mean_confidence,
    }

    if results_path is not None:
        rpath = Path(results_path)
        rpath.parent.mkdir(parents=True, exist_ok=True)
        with open(rpath, "w", encoding="utf-8") as f:
            json.dump(eval_results, f, indent=2)

    _print_report(eval_results)
    return eval_results


def _print_report(res: dict[str, Any]) -> None:
    """Print structured evaluation summary."""
    print("=" * 60)
    print("WEATHER GUIDANCE-SHIFT CLASSIFIER EVALUATION REPORT")
    print("=" * 60)
    print(f"Total cycle pairs:          {res['n_total']}")
    print(f"Shift items (warmer/cooler): {res['n_shift']}")
    print(f"Steady items:                {res['n_steady']}")
    print(f"Shift Recall:                {res['shift_recall']:.2%}")
    print(f"Direction Accuracy:          {res['direction_accuracy']:.2%}")
    print(f"Steady False Alarm Rate:     {res['steady_false_alarm_rate']:.2%}")
    print("\nConfusion Counts:")
    for actual, preds in res["per_class_confusion_counts"].items():
        print(f"  Actual {actual:7s} -> " + ", ".join(f"{p}: {c}" for p, c in preds.items()))
    print("\nMean Confidence Score by Label:")
    for lbl, score in res["mean_confidence_score_by_label"].items():
        score_str = f"{score:.2f}" if score is not None else "N/A"
        print(f"  {lbl:7s}: {score_str}")
    print("=" * 60)


if __name__ == "__main__":
    evaluate_weather()
