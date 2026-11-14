"""Evaluation harness for Settlement Rules Auditor (Build #1).

Evaluates tests/fixtures/decisions/settlement_auditor/entries.jsonl with DecisionsClient
via the evals harness (ItemBatch, chunk_items, Pacer, AnswersCache).

DOCTRINE:
- Offline re-scoring: answers are cached in answers.jsonl; subsequent runs score
  offline without calling the Decisions API.
- Live execution: if uncached entries exist, calls DecisionsClient via run_batched
  with token/rate pacing, caching raw answers keyed by entry_id.
- Question key adaptation: harness run_batched produces full wire keys; answers are
  adapted by stripping the f"{entry_id}__" prefix before passing to gate_entry.
- Grades per-dimension recall, per-dimension false flag rates, entry-level
  quarantine precision/recall, and clean-entry false-flag rates.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from ev.decisions_evals.common import AnswersCache, Pacer, live_client, run_batched
from ev.settlement_auditor import (
    DIMENSIONS,
    build_entry_input,
    build_entry_questions,
    gate_entry,
)

FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "tests"
    / "fixtures"
    / "decisions"
    / "settlement_auditor"
)
DEFAULT_ENTRIES_PATH = FIXTURE_DIR / "entries.jsonl"
DEFAULT_ANSWERS_PATH = FIXTURE_DIR / "answers.jsonl"
DEFAULT_RESULTS_PATH = FIXTURE_DIR / "eval_results.json"


def load_entries(path: Union[str, os.PathLike[str]] = DEFAULT_ENTRIES_PATH) -> list[dict[str, Any]]:
    """Load JSONL settlement rule entries."""
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def adapt_entry_answers(
    entry_id: str,
    raw_answers: Mapping[str, Any],
) -> dict[str, Any]:
    """Strip the f"{entry_id}__" question key prefix so gate_entry receives dimension keys."""
    prefix = f"{entry_id}__"
    return {
        qid[len(prefix):] if qid.startswith(prefix) else qid: ans
        for qid, ans in raw_answers.items()
    }


def fetch_or_load_answers(
    entries: Sequence[Mapping[str, Any]],
    cache_path: Union[str, os.PathLike[str]] = DEFAULT_ANSWERS_PATH,
    client: Optional[Any] = None,
    pacer: Optional[Pacer] = None,
) -> dict[str, dict[str, Any]]:
    """Retrieve answers from AnswersCache, or fetch uncached entries via run_batched."""
    cache = AnswersCache(cache_path)

    uncached_entries = [e for e in entries if str(e["entry_id"]) not in cache]
    if uncached_entries:
        if client is None:
            client = live_client()
        if pacer is None:
            pacer = Pacer()

        items = [
            (build_entry_input(e), build_entry_questions(e))
            for e in uncached_entries
        ]
        batch_out = run_batched(client, items, pacer=pacer)
        for local_idx, e in enumerate(uncached_entries):
            eid = str(e["entry_id"])
            item_answers = batch_out.get(local_idx, {})
            cache.store(eid, item_answers)

    all_answers: dict[str, dict[str, Any]] = {}
    for e in entries:
        eid = str(e["entry_id"])
        all_answers[eid] = cache.get(eid, {})
    return all_answers


def score_settlement_eval(
    entries: Sequence[Mapping[str, Any]],
    gate_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Score gate results against fixture ground-truth labels.

    Computes:
    - per-dimension recall (flagged among planted)
    - per-dimension false flags on entries where dim is not planted
    - entry-level quarantine precision/recall (label divergent = non-empty divergent_dimensions)
    - clean false-flag rate (clean entries quarantined / clean entries)
    """
    per_dim: dict[str, dict[str, Any]] = {}
    for dim in DIMENSIONS:
        planted = 0
        flagged_planted = 0
        unplanted = 0
        false_flags = 0

        for entry, gres in zip(entries, gate_results):
            divergent = entry.get("divergent_dimensions", [])
            is_planted = dim in divergent
            is_flagged = dim in gres.get("flags", [])

            if is_planted:
                planted += 1
                if is_flagged:
                    flagged_planted += 1
            else:
                unplanted += 1
                if is_flagged:
                    false_flags += 1

        rec = (flagged_planted / planted) if planted > 0 else 1.0
        ff_rate = (false_flags / unplanted) if unplanted > 0 else 0.0

        per_dim[dim] = {
            "planted": planted,
            "flagged_planted": flagged_planted,
            "recall": round(rec, 4),
            "unplanted": unplanted,
            "false_flags": false_flags,
            "false_flag_rate": round(ff_rate, 4),
        }

    tp = 0
    fp = 0
    fn = 0
    tn = 0
    clean_entries_count = 0
    clean_quarantined_count = 0

    for entry, gres in zip(entries, gate_results):
        is_divergent = bool(entry.get("divergent_dimensions"))
        is_quarantined = gres.get("disposition") == "quarantine"

        if is_divergent:
            if is_quarantined:
                tp += 1
            else:
                fn += 1
        else:
            clean_entries_count += 1
            if is_quarantined:
                fp += 1
                clean_quarantined_count += 1
            else:
                tn += 1

    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    clean_ff_rate = (
        (clean_quarantined_count / clean_entries_count)
        if clean_entries_count > 0
        else 0.0
    )

    return {
        "summary": {
            "total_entries": len(entries),
            "divergent_entries": tp + fn,
            "clean_entries": clean_entries_count,
            "quarantined_entries": tp + fp,
            "consistent_entries": tn + fn,
        },
        "entry_level": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "clean_false_flag_rate": round(clean_ff_rate, 4),
            "clean_quarantined": clean_quarantined_count,
            "clean_total": clean_entries_count,
        },
        "per_dimension": per_dim,
        "quarantined_entry_ids": [
            g["entry_id"] for g in gate_results if g["disposition"] == "quarantine"
        ],
        "consistent_entry_ids": [
            g["entry_id"] for g in gate_results if g["disposition"] == "consistent"
        ],
        "unresolved_entry_ids": [
            g["entry_id"] for g in gate_results if g.get("unresolved")
        ],
    }


def evaluate_settlement(
    entries_path: Union[str, os.PathLike[str]] = DEFAULT_ENTRIES_PATH,
    cache_path: Union[str, os.PathLike[str]] = DEFAULT_ANSWERS_PATH,
    out_path: Union[str, os.PathLike[str]] = DEFAULT_RESULTS_PATH,
    client: Optional[Any] = None,
    pacer: Optional[Pacer] = None,
) -> dict[str, Any]:
    """Execute settlement auditor evaluation over the fixture."""
    entries = load_entries(entries_path)
    all_answers = fetch_or_load_answers(
        entries, cache_path=cache_path, client=client, pacer=pacer
    )

    gate_results = []
    for entry in entries:
        eid = str(entry["entry_id"])
        raw = all_answers.get(eid, {})
        adapted = adapt_entry_answers(eid, raw)
        gate_results.append(gate_entry(entry, adapted))

    results = score_settlement_eval(entries, gate_results)

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    return results


def main() -> int:
    """CLI entrypoint: runs evaluation, writes eval_results.json, and prints it."""
    results = evaluate_settlement()
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
