"""Evaluation script for N3 research-claim evidence auditor route.

Evaluates the committed claims.jsonl fixture against Decisions API entailment
gates, scoring recall on planted flaws (contradicted, unsupported, stale,
wrong_scope) and false-block rates on clean claims.

Argon ruling RUL-20261113-dec-gate-design Q1 bars:
1. Contradicted recall: 100% (bar: 1.0)
2. Planted-flaw recall: >= 90% (bar: 0.90) AND 100% on multi-artifact synthesis flaws (bar: 1.0)
3. Clean false-block rate: <= 5% (bar: 0.05) overall AND on the multi-artifact clean subset (bar: 0.05)
Held thresholds: supported >= 0.90 / contradicted <= 0.05 / wrong_scope+stale < 0.25.

DOCTRINE:
- Offline by default: uses AnswersCache (answers.jsonl) next to the fixture to
  avoid repeated API calls.
- Live fetching: only when missing answers exist and GEMINI_API_KEY is present,
  using live_client, Pacer, and run_batched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ev.decisions_claim_auditor import audit_claims
from ev.decisions_client import BatchResult
from ev.decisions_common import estimate_tokens
from ev.decisions_evals.common import AnswersCache, Pacer, live_client, run_batched
from ev.decisions_prep.n3_claim_auditor import build_questions
from ev.decisions_prep.n3_evidence_packaging import package_evidence

FLAW_LABELS: tuple[str, ...] = ("contradicted", "unsupported", "stale", "wrong_scope")
LABEL_ORDER: tuple[str, ...] = ("clean", "contradicted", "unsupported", "stale", "wrong_scope")


def _find_fixture_dir() -> Path:
    """Locate the N3 claims fixture directory."""
    base = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "decisions"
    candidates = [
        base / "n3_claims",
        base / "n3_claim_auditor",
    ]
    for c in candidates:
        if (c / "claims.jsonl").is_file():
            return c
    return candidates[0]


class CachedDecisionsClient:
    """Client stub that replays cached live answers through the wire format.

    Serves only answers present in the AnswersCache (which come from live
    Decisions calls); a question with no cached answer stays absent, so the
    route under evaluation abstains on it exactly as it would live.
    """

    def __init__(self, cache: AnswersCache, id_map: Optional[Mapping[str, str]] = None) -> None:
        self.cache = cache
        self.id_map = dict(id_map or {})

    def call(self, input_text: str, questions: Mapping[str, Any]) -> BatchResult:
        tag_to_cid: dict[str, str] = dict(self.id_map)
        current_tag: Optional[str] = None
        for line in input_text.splitlines():
            line_s = line.strip()
            if line_s.startswith("[") and line_s.endswith("]"):
                current_tag = line_s[1:-1]
            elif line_s.startswith("Claim ID:") and current_tag:
                cid = line_s.split(":", 1)[1].strip()
                tag_to_cid[current_tag] = cid

        wire_answers: dict[str, Any] = {}
        for wire_key in questions:
            if "_" in wire_key:
                tag, qid = wire_key.rsplit("_", 1)
                claim_id = tag_to_cid.get(tag, tag)
                ans = self.cache.get(claim_id, {})
                if qid in ans:
                    wire_answers[wire_key] = ans[qid]

        return BatchResult(answers=wire_answers, abstained=False)


def run_eval(
    fixture_dir: Optional[Union[str, os.PathLike[str]]] = None,
    out_dir: Optional[Union[str, os.PathLike[str]]] = None,
) -> dict[str, Any]:
    """Execute fixture evaluation, write eval_results.json, and return summary dict."""
    fdir = Path(fixture_dir) if fixture_dir else _find_fixture_dir()
    claims_path = fdir / "claims.jsonl"
    answers_path = fdir / "answers.jsonl"
    results_path = (Path(out_dir) if out_dir else fdir) / "eval_results.json"

    if not claims_path.is_file():
        raise FileNotFoundError(f"Fixture claims file not found: {claims_path}")

    claims: list[dict[str, Any]] = []
    with open(claims_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                claims.append(json.loads(line))

    packaged_by_id = {c["claim_id"]: package_evidence(c) for c in claims}
    evidence_texts = {c["claim_id"]: packaged_by_id[c["claim_id"]]["evidence_text"] for c in claims}

    # 1. Manage AnswersCache
    cache = AnswersCache(answers_path)
    cached = cache.load()
    uncached = [c for c in claims if c["claim_id"] not in cached]

    # Skip claims whose packaging has missing pointers (deterministically blocked in scoring; no live answers needed)
    uncached_to_fetch = [c for c in uncached if not packaged_by_id[c["claim_id"]]["missing"]]

    if uncached_to_fetch:
        if os.environ.get("GEMINI_API_KEY"):
            # Live path via harness.
            # Per RUL-20261113-dec-gate-design Q1: the clearance re-eval is mandated at 1 claim/call.
            client = live_client()
            pacer = Pacer()
            for c in uncached_to_fetch:
                cid = c["claim_id"]
                item = (
                    build_questions(c, evidence_texts[cid])["input"],
                    build_questions(c, evidence_texts[cid])["questions"],
                )
                batched = run_batched(client, [item], pacer=pacer)
                cache.store(cid, batched[0])
        else:
            raise RuntimeError(
                f"GEMINI_API_KEY is not set and {len(uncached_to_fetch)} claims have no cached "
                "answers. This evaluation only scores live Decisions answers or "
                "previously cached live answers; it never synthesizes answers."
            )

    # 2. Score claims via audit_claims with cache-backed client
    # Manifest check is not applicable (no manifests for this fixture)
    tag_map = {c["claim_id"]: c["claim_id"] for c in claims}
    for idx, c in enumerate(claims):
        tag_map[f"i{idx+1:03d}"] = c["claim_id"]

    cache_client = CachedDecisionsClient(cache, id_map=tag_map)

    # Adaptive groups: accumulate claims into a group while the summed estimate_tokens
    # of their build_questions inputs stays <= 200_000; a single claim always forms a valid group.
    groups: list[list[dict[str, Any]]] = []
    current_group: list[dict[str, Any]] = []
    current_tokens: int = 0

    for c in claims:
        cid = c["claim_id"]
        q_input = build_questions(c, evidence_texts[cid])["input"]
        toks = estimate_tokens(q_input)
        if current_group and (current_tokens + toks > 200_000):
            groups.append(current_group)
            current_group = [c]
            current_tokens = toks
        else:
            current_group.append(c)
            current_tokens += toks
    if current_group:
        groups.append(current_group)

    all_results: list[dict[str, Any]] = []
    for group in groups:
        group_out = audit_claims(
            group,
            client=cache_client,
            mode="shadow",
        )
        all_results.extend(group_out["results"])

    audit_output = {
        "results": all_results,
        "blocked_ids": [r["claim_id"] for r in all_results if r["blocked"]],
        "clear_ids": [r["claim_id"] for r in all_results if not r["blocked"]],
        "mode": "shadow",
    }

    results_by_id = {r["claim_id"]: r for r in audit_output["results"]}

    # 3. Compute metrics
    per_label: dict[str, dict[str, Any]] = {}
    for lbl in LABEL_ORDER:
        matching = [c for c in claims if c["label"] == lbl]
        total = len(matching)
        blocked = sum(1 for c in matching if results_by_id[c["claim_id"]]["blocked"])
        clear = total - blocked
        rate = blocked / total if total > 0 else 0.0
        per_label[lbl] = {
            "total": total,
            "blocked": blocked,
            "clear": clear,
            "block_rate": round(rate, 4),
        }

    # Contradicted recall (bar 100%)
    c_data = per_label["contradicted"]
    contradicted_recall = c_data["blocked"] / c_data["total"] if c_data["total"] > 0 else 0.0
    contradicted_passed = contradicted_recall >= 1.0

    # Planted-flaw recall (bar >= 90%)
    flaw_total = sum(per_label[lbl]["total"] for lbl in FLAW_LABELS)
    flaw_blocked = sum(per_label[lbl]["blocked"] for lbl in FLAW_LABELS)
    planted_flaw_recall = flaw_blocked / flaw_total if flaw_total > 0 else 0.0
    planted_flaw_passed = planted_flaw_recall >= 0.90

    # Clean false-block rate (bar <= 5%)
    clean_data = per_label["clean"]
    clean_false_block_rate = clean_data["blocked"] / clean_data["total"] if clean_data["total"] > 0 else 0.0
    clean_passed = clean_false_block_rate <= 0.05

    # Multi-artifact clean false-block (bar <= 5%)
    ma_clean = [
        c for c in claims
        if c["label"] == "clean" and packaged_by_id[c["claim_id"]]["multi_artifact"]
    ]
    ma_clean_total = len(ma_clean)
    if ma_clean_total == 0:
        ma_clean_val = None
        ma_clean_passed = True
        ma_clean_blocked = 0
    else:
        ma_clean_blocked = sum(1 for c in ma_clean if results_by_id[c["claim_id"]]["blocked"])
        ma_clean_val = round(ma_clean_blocked / ma_clean_total, 4)
        ma_clean_passed = (ma_clean_blocked / ma_clean_total) <= 0.05

    # Multi-artifact flaw recall (bar 100%)
    ma_flaw = [
        c for c in claims
        if c["label"] in FLAW_LABELS and packaged_by_id[c["claim_id"]]["multi_artifact"]
    ]
    ma_flaw_total = len(ma_flaw)
    if ma_flaw_total == 0:
        ma_flaw_val = None
        ma_flaw_passed = True
        ma_flaw_blocked = 0
    else:
        ma_flaw_blocked = sum(1 for c in ma_flaw if results_by_id[c["claim_id"]]["blocked"])
        ma_flaw_val = round(ma_flaw_blocked / ma_flaw_total, 4)
        ma_flaw_passed = (ma_flaw_blocked / ma_flaw_total) >= 1.0

    passed_all = (
        contradicted_passed
        and planted_flaw_passed
        and clean_passed
        and (ma_clean_passed if ma_clean_total > 0 else True)
        and (ma_flaw_passed if ma_flaw_total > 0 else True)
    )

    all_ext: set[str] = set()
    for p in packaged_by_id.values():
        all_ext.update(p["external"])

    report_data = {
        "manifest_check": "not_applicable_no_manifests",
        "total_claims": len(claims),
        "blocked_count": len(audit_output["blocked_ids"]),
        "clear_count": len(audit_output["clear_ids"]),
        "passed_all": passed_all,
        "packaging": {
            "claims_with_missing_pointers": [
                c["claim_id"] for c in claims if packaged_by_id[c["claim_id"]]["missing"]
            ],
            "multi_artifact_claim_ids": [
                c["claim_id"] for c in claims if packaged_by_id[c["claim_id"]]["multi_artifact"]
            ],
            "external_pointers": sorted(list(all_ext)),
        },
        "metrics": {
            "contradicted_recall": {
                "value": round(contradicted_recall, 4),
                "bar": 1.0,
                "passed": contradicted_passed,
                "blocked": c_data["blocked"],
                "total": c_data["total"],
            },
            "planted_flaw_recall": {
                "value": round(planted_flaw_recall, 4),
                "bar": 0.90,
                "passed": planted_flaw_passed,
                "blocked": flaw_blocked,
                "total": flaw_total,
            },
            "clean_false_block_rate": {
                "value": round(clean_false_block_rate, 4),
                "bar": 0.05,
                "passed": clean_passed,
                "blocked": clean_data["blocked"],
                "total": clean_data["total"],
            },
            "multi_artifact_clean_false_block": {
                "value": ma_clean_val,
                "bar": 0.05,
                "passed": ma_clean_passed,
                "blocked": ma_clean_blocked,
                "total": ma_clean_total,
            },
            "multi_artifact_flaw_recall": {
                "value": ma_flaw_val,
                "bar": 1.0,
                "passed": ma_flaw_passed,
                "blocked": ma_flaw_blocked,
                "total": ma_flaw_total,
            },
        },
        "per_label": per_label,
    }

    # 4. Write eval_results.json
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(report_data, indent=2) + "\n", encoding="utf-8")

    # 5. Print summary report
    _print_report(report_data, str(claims_path), str(results_path))

    return report_data


def _print_report(data: dict[str, Any], claims_path: str, out_path: str) -> None:
    """Print formatted evaluation tables to stdout."""
    print("=" * 72)
    print("N3 RESEARCH-CLAIM EVIDENCE AUDITOR EVALUATION")
    print("=" * 72)
    print(f"Fixture:        {claims_path} ({data['total_claims']} claims)")
    print(f"Output:         {out_path}")
    print(f"Manifest check: {data['manifest_check']}")
    print("-" * 72)
    print(f"{'Label':<15} {'Total':>8} {'Blocked':>8} {'Clear':>8} {'Block Rate':>12}")
    print("-" * 72)
    for lbl in LABEL_ORDER:
        info = data["per_label"][lbl]
        rate_str = f"{info['block_rate'] * 100:6.1f}%"
        print(f"{lbl:<15} {info['total']:>8} {info['blocked']:>8} {info['clear']:>8} {rate_str:>12}")
    print("-" * 72)
    print("KILL CRITERIA:")
    m = data["metrics"]
    c_rec = m["contradicted_recall"]
    f_rec = m["planted_flaw_recall"]
    cl_rate = m["clean_false_block_rate"]
    ma_cl = m["multi_artifact_clean_false_block"]
    ma_fl = m["multi_artifact_flaw_recall"]

    def _status(p: bool) -> str:
        return "PASS" if p else "FAIL"

    ma_cl_str = f"{ma_cl['value'] * 100:5.1f}%" if ma_cl["value"] is not None else " N/A "
    ma_fl_str = f"{ma_fl['value'] * 100:5.1f}%" if ma_fl["value"] is not None else " N/A "

    print(f"  Contradicted recall:              {c_rec['value'] * 100:5.1f}%  (bar: 100.0%) -> {_status(c_rec['passed'])}")
    print(f"  Planted-flaw recall:              {f_rec['value'] * 100:5.1f}%  (bar: >=90.0%) -> {_status(f_rec['passed'])}")
    print(f"  Clean false-block rate:           {cl_rate['value'] * 100:5.1f}%  (bar: <= 5.0%) -> {_status(cl_rate['passed'])}")
    print(f"  Multi-artifact clean false-block: {ma_cl_str}  (bar: <= 5.0%) -> {_status(ma_cl['passed'])}")
    print(f"  Multi-artifact flaw recall:       {ma_fl_str}  (bar: 100.0%) -> {_status(ma_fl['passed'])}")
    print("-" * 72)
    overall = "PASS" if data["passed_all"] else "FAIL"
    print(f"OVERALL EVALUATION: {overall}")
    print("=" * 72)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint for running eval_claims."""
    parser = argparse.ArgumentParser(description="Evaluate N3 claims fixture")
    parser.add_argument("--fixture-dir", help="Path to fixture directory containing claims.jsonl")
    parser.add_argument("--out-dir", help="Path to write eval_results.json")
    args = parser.parse_args(argv)

    try:
        results = run_eval(fixture_dir=args.fixture_dir, out_dir=args.out_dir)
        return 0 if results["passed_all"] else 1
    except Exception as exc:
        sys.stderr.write(f"Evaluation failed: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
