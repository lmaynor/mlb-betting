"""ev.decisions_evals.eval_ladder -- Evaluation harness for the N1 ladder identity adjudicator.

Evaluates the committed 660-label fixture:
- tests/fixtures/decisions/n1_ladder_identity/labels.jsonl
- AnswersCache at answers.jsonl in the fixture dir, keyed by entry_key
- Rate-limited batched execution via Pacer and run_batched for uncached ambiguity set items (~1980 questions)
- Offline cache-replay adjudication via CachedDecisionsClient
- Scores against labels: false canonicalization (<= 0.5%), scope disagreement (<= 1.0%),
  and quarantine reduction vs parser-only baseline (>= 30%).
- Writes eval_results.json and prints human-readable metrics.

CRITICAL: Scores only live or cached-live answers; without GEMINI_API_KEY and with uncached
items it RAISES. Never synthesize or simulate answers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from ev.decisions_client import BatchResult
from ev.decisions_evals.common import AnswersCache, Pacer, run_batched
from ev.decisions_ladder_adjudicator import (
    adjudicate_entries,
    batch_item_for,
    build_parser_view,
    candidate_stats_for,
    group_key,
    make_entry_key,
)
from ev.decisions_prep.n1_ladder_identity import (
    build_questions,
    build_request_input,
    select_ambiguity_set,
)
from ev.props import norm_name

FIXTURE_DIR = (
    Path(__file__).parent.parent.parent
    / "tests"
    / "fixtures"
    / "decisions"
    / "n1_ladder_identity"
)
LABELS_PATH = FIXTURE_DIR / "labels.jsonl"
ANSWERS_PATH = FIXTURE_DIR / "answers.jsonl"
RESULTS_PATH = FIXTURE_DIR / "eval_results.json"

BAR_FALSE_CANONICALIZATION = 0.005  # <= 0.5%
BAR_SCOPE_DISAGREEMENT = 0.010       # <= 1.0%
BAR_QUARANTINE_REDUCTION = 0.30     # >= 30%


def parse_block_entry_key(block: str) -> str:
    """Reconstruct canonical entry_key from a build_request_input text block.

    Block fields:
        Book: ...
        Player: ...
        Raw market key: ...
        Outcome name: ...
        Outcome description: ...
        Raw point: ...
    """
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip()

    book = fields.get("Book", "")
    player = fields.get("Player", "")
    player_norm = norm_name(player)
    mkey = fields.get("Raw market key", "")
    pt = fields.get("Raw point", "")
    name = fields.get("Outcome name", "")
    return f"{book}|{player_norm}|{mkey}|{pt}|{name}"


class CachedDecisionsClient:
    """Decisions client that serves answers from AnswersCache through ItemBatch wire format."""

    def __init__(self, cache: AnswersCache) -> None:
        self.cache = cache
        self.calls: list[dict[str, Any]] = []

    def call(self, input_text: str, questions: Mapping[str, Any]) -> BatchResult:
        """Serve answers for wire questions by parsing tagged blocks in input_text."""
        self.calls.append({
            "input_text": input_text,
            "questions": dict(questions),
        })

        tag_to_key: dict[str, str] = {}
        pattern = re.compile(
            r"^\[([A-Za-z0-9_-]+)\]\n(.*?)(?=(?:^\[[A-Za-z0-9_-]+\]\n)|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        for match in pattern.finditer(input_text):
            tag = match.group(1)
            block = match.group(2)
            ekey = parse_block_entry_key(block)
            tag_to_key[tag] = ekey

        answers: dict[str, Any] = {}
        for wire_key in questions:
            # wire_key is f"{tag}_{qid}"
            matched_tag = None
            matched_qid = None
            for tag in tag_to_key:
                prefix = f"{tag}_"
                if wire_key.startswith(prefix):
                    matched_tag = tag
                    matched_qid = wire_key[len(prefix):]
                    break

            if not matched_tag or not matched_qid:
                return BatchResult.abstain(f"unknown_wire_key: {wire_key}")

            ekey = tag_to_key[matched_tag]
            if ekey not in self.cache:
                return BatchResult.abstain(f"uncached_item: tag={matched_tag}, ekey={ekey}")

            cached_item = self.cache.get(ekey)
            if not isinstance(cached_item, Mapping) or matched_qid not in cached_item:
                return BatchResult.abstain(
                    f"uncached_question: tag={matched_tag}, qid={matched_qid}, ekey={ekey}"
                )

            ans = cached_item[matched_qid]
            if isinstance(ans, Mapping):
                answers[wire_key] = dict(ans)
            elif isinstance(ans, (int, float)) and not isinstance(ans, bool):
                answers[wire_key] = {"noul": float(ans), "probability": float(ans)}
            elif isinstance(ans, str):
                answers[wire_key] = {"choice": ans}
            else:
                answers[wire_key] = {"value": ans}

        return BatchResult(answers=answers, abstained=False)


def run_eval(
    labels_path: Union[str, os.PathLike[str]] = LABELS_PATH,
    answers_path: Union[str, os.PathLike[str]] = ANSWERS_PATH,
    results_path: Optional[Union[str, os.PathLike[str]]] = RESULTS_PATH,
    client: Optional[Any] = None,
) -> dict[str, Any]:
    """Execute evaluation over the fixture entries.

    Fetches answers for uncached ambiguous entries if live client/key is available;
    raises if uncached items exist and GEMINI_API_KEY is unset. Replays cached answers
    through CachedDecisionsClient and adjudicate_entries. Computes metrics vs fixture labels.
    """
    labels_file = Path(labels_path)
    if not labels_file.exists():
        raise FileNotFoundError(f"Fixture labels file not found: {labels_file}")

    entries: list[dict[str, Any]] = []
    with open(labels_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    # Build groups and views
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for e in entries:
        groups.setdefault(group_key(e), []).append(e)

    views = [build_parser_view(e, groups[group_key(e)]) for e in entries]
    ambiguous_entries = select_ambiguity_set(entries, views)

    # Cache handling
    cache = AnswersCache(answers_path)
    uncached = [e for e in ambiguous_entries if make_entry_key(e) not in cache]

    if uncached:
        active_client = client
        if active_client is None:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError(
                    f"Cannot evaluate: {len(uncached)} uncached items exist in ambiguity set "
                    f"and GEMINI_API_KEY is not set. Decisions eval scores only live or cached-live answers."
                )
            from ev.decisions_client import DecisionsClient

            active_client = DecisionsClient(api_key=api_key)

        items_to_fetch: list[tuple[str, Mapping[str, Any]]] = []
        for e in uncached:
            v = build_parser_view(e, groups[group_key(e)])
            items_to_fetch.append(batch_item_for(e, v, groups[group_key(e)], entries))

        pacer = Pacer()
        batched_results = run_batched(active_client, items_to_fetch, pacer=pacer)
        for idx, e in enumerate(uncached):
            ekey = make_entry_key(e)
            cache.store(ekey, batched_results[idx])

    # Replay through adjudicate_entries with CachedDecisionsClient
    replay_client = CachedDecisionsClient(cache)
    adj_result = adjudicate_entries(entries, client=replay_client)
    decisions = adj_result["decisions"]
    summary = adj_result["summary"]

    # Metrics vs labels
    total_entries = len(entries)
    accepted_entries = [
        (e, d)
        for e, d in zip(entries, decisions)
        if d.get("status") == "accepted_shadow" and d.get("identity") is not None
    ]
    n_accepted = len(accepted_entries)

    false_canon_count = 0
    scope_disagree_count = 0

    for e, d in accepted_entries:
        identity = d["identity"]
        label = e["label"]

        # Stat agreement
        stat_eq = identity.get("stat") == label.get("canonical_stat")

        # Rung agreement
        id_rung = identity.get("rung")
        lab_rung = label.get("rung")
        rung_eq = (
            float(id_rung) == float(lab_rung)
            if id_rung is not None and lab_rung is not None
            else id_rung == lab_rung
        )

        # Scope agreement
        scope_eq = identity.get("scope") == label.get("scope")

        if not scope_eq:
            scope_disagree_count += 1
        if not (stat_eq and rung_eq and scope_eq):
            false_canon_count += 1

    false_canon_rate = false_canon_count / n_accepted if n_accepted > 0 else 0.0
    scope_disagree_rate = scope_disagree_count / n_accepted if n_accepted > 0 else 0.0

    # Parser-only baseline:
    # parser_only_quarantined = number of entries NOT resolved deterministically
    # (ambiguity set + det failures) = total - deterministic
    det_resolved = summary.get("deterministic", 0)
    parser_only_quarantined = total_entries - det_resolved
    route_quarantined = summary.get("quarantined", 0)
    quarantine_reduction = (
        (parser_only_quarantined - route_quarantined) / parser_only_quarantined
        if parser_only_quarantined > 0
        else 0.0
    )

    # Per ambiguity_class acceptance
    by_class: dict[str, dict[str, Any]] = {}
    for e, d in zip(entries, decisions):
        ac = e.get("ambiguity_class", "unknown")
        if ac not in by_class:
            by_class[ac] = {"total": 0, "accepted": 0, "quarantined": 0}
        by_class[ac]["total"] += 1
        if d.get("status") == "accepted_shadow":
            by_class[ac]["accepted"] += 1
        elif d.get("status") == "quarantined":
            by_class[ac]["quarantined"] += 1

    for ac, cdata in by_class.items():
        tot = cdata["total"]
        cdata["rate"] = cdata["accepted"] / tot if tot > 0 else 0.0

    pass_false_canon = false_canon_rate <= BAR_FALSE_CANONICALIZATION
    pass_scope_disagree = scope_disagree_rate <= BAR_SCOPE_DISAGREEMENT
    pass_quarantine_reduction = quarantine_reduction >= BAR_QUARANTINE_REDUCTION
    gate_passed = pass_false_canon and pass_scope_disagree and pass_quarantine_reduction

    # Diagnostic (not a gate metric): how often the model's stat choice
    # agrees with the label, independent of the scope-confidence prongs.
    _cache = AnswersCache(answers_path)
    _agree = _tot = 0
    for _e in entries:
        _a = _cache.get(make_entry_key(_e), {})
        _pick = _a.get("canonical_stat", {}).get("choice") if isinstance(_a.get("canonical_stat"), Mapping) else None
        if _pick:
            _tot += 1
            _agree += _pick == _e["label"]["canonical_stat"]

    eval_results = {
        "gate_passed": gate_passed,
        "total": total_entries,
        "accepted": n_accepted,
        "stat_choice_agreement": (_agree / _tot) if _tot else None,
        "stat_choice_agreement_n": _tot,
        "summary": summary,
        "parser_only_quarantined": parser_only_quarantined,
        "route_quarantined": route_quarantined,
        "quarantine_reduction": quarantine_reduction,
        "false_canonicalization_count": false_canon_count,
        "false_canonicalization_rate": false_canon_rate,
        "scope_disagreement_count": scope_disagree_count,
        "scope_disagreement_rate": scope_disagree_rate,
        "by_class": by_class,
        "bars": {
            "false_canonicalization": {
                "value": false_canon_rate,
                "bar": BAR_FALSE_CANONICALIZATION,
                "passed": pass_false_canon,
            },
            "scope_disagreement": {
                "value": scope_disagree_rate,
                "bar": BAR_SCOPE_DISAGREEMENT,
                "passed": pass_scope_disagree,
            },
            "quarantine_reduction": {
                "value": quarantine_reduction,
                "bar": BAR_QUARANTINE_REDUCTION,
                "passed": pass_quarantine_reduction,
            },
        },
    }

    if results_path is not None:
        out_file = Path(results_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(eval_results, f, indent=2)

    _print_results(eval_results)
    return eval_results


def _print_results(results: dict[str, Any]) -> None:
    """Print formatted evaluation summary table."""
    print("=" * 70)
    print("N1 LADDER IDENTITY ADJUDICATOR EVALUATION RESULTS")
    print("=" * 70)
    print(f"Total entries:               {results['total']}")
    print(f"Accepted (shadow):           {results['accepted']}")
    print(f"Route quarantined:           {results['route_quarantined']}")
    print(f"Parser-only quarantined:     {results['parser_only_quarantined']}")
    print("-" * 70)
    print("GATE METRICS:")
    bars = results["bars"]
    fc = bars["false_canonicalization"]
    sd = bars["scope_disagreement"]
    qr = bars["quarantine_reduction"]

    print(
        f"  False canonicalization:    {fc['value'] * 100:.2f}% "
        f"(bar <= {fc['bar'] * 100:.1f}%) -> {'PASS' if fc['passed'] else 'FAIL'}"
    )
    print(
        f"  Scope disagreement:        {sd['value'] * 100:.2f}% "
        f"(bar <= {sd['bar'] * 100:.1f}%) -> {'PASS' if sd['passed'] else 'FAIL'}"
    )
    print(
        f"  Quarantine reduction:      {qr['value'] * 100:.2f}% "
        f"(bar >= {qr['bar'] * 100:.1f}%) -> {'PASS' if qr['passed'] else 'FAIL'}"
    )
    print("-" * 70)
    print("PER-CLASS ACCEPTANCE:")
    for ac, cdata in sorted(results.get("by_class", {}).items()):
        print(
            f"  {ac:26s} {cdata['accepted']}/{cdata['total']} "
            f"({cdata['rate'] * 100:.1f}%)"
        )
    print("=" * 70)
    print(f"GATE PASSED: {'YES' if results['gate_passed'] else 'NO'}")
    print("=" * 70)


def main() -> None:
    """CLI entrypoint: evaluate ladder fixture."""
    parser = argparse.ArgumentParser(description="Evaluate N1 ladder identity adjudicator on 660-label fixture.")
    parser.add_argument("--labels", default=str(LABELS_PATH), help="Path to fixture labels.jsonl")
    parser.add_argument("--answers", default=str(ANSWERS_PATH), help="Path to answers.jsonl cache")
    parser.add_argument("--out", default=str(RESULTS_PATH), help="Path to write eval_results.json")
    args = parser.parse_args()

    run_eval(labels_path=args.labels, answers_path=args.answers, results_path=args.out)


if __name__ == "__main__":
    main()
