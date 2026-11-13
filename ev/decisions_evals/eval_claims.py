"""Evaluation script for N3 research-claim evidence auditor route.

Evaluates the committed claims.jsonl fixture against Decisions API entailment
gates, scoring recall on planted flaws (contradicted, unsupported, stale,
wrong_scope) and false-block rates on clean claims.

DOCTRINE:
- Offline by default: uses AnswersCache (answers.jsonl) next to the fixture to
  avoid repeated API calls.
- Live fetching: only when missing answers exist and GEMINI_API_KEY is present,
  using live_client, Pacer, and run_batched.
- Grades against Argon ruling kill criteria:
  1. Contradicted recall: 100% (bar: 1.0)
  2. Planted-flaw recall: >= 90% (bar: 0.90)
  3. Clean false-block rate: <= 5% (bar: 0.05)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ev.decisions_claim_auditor import EVIDENCE_CAP, audit_claims
from ev.decisions_client import BatchResult
from ev.decisions_evals.common import AnswersCache, Pacer, live_client, run_batched
from ev.decisions_prep.n3_claim_auditor import build_questions

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


def _build_evidence_text(claim: Mapping[str, Any]) -> str:
    """Construct evidence text capped at EVIDENCE_CAP characters.

    Evidence text per claim = fixture 'evidence' value, plus (when the file
    exists) the content of source_doc expanded with os.path.expanduser.
    """
    ev = str(claim.get("evidence", ""))
    source_doc = claim.get("source_doc")
    doc_content = ""
    if source_doc:
        expanded = Path(os.path.expanduser(str(source_doc)))
        if expanded.is_file():
            try:
                doc_content = expanded.read_text(encoding="utf-8", errors="replace")
            except OSError:
                doc_content = ""

    if doc_content:
        combined = f"{ev}\n\n{doc_content}" if ev else doc_content
    else:
        combined = ev
    return combined[:EVIDENCE_CAP]


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

    evidence_texts = {c["claim_id"]: _build_evidence_text(c) for c in claims}

    # 1. Manage AnswersCache
    cache = AnswersCache(answers_path)
    cached = cache.load()
    uncached = [c for c in claims if c["claim_id"] not in cached]

    if uncached:
        if os.environ.get("GEMINI_API_KEY"):
            # Live path via harness
            client = live_client()
            pacer = Pacer()
            items = [
                (
                    build_questions(c, evidence_texts[c["claim_id"]])["input"],
                    build_questions(c, evidence_texts[c["claim_id"]])["questions"],
                )
                for c in uncached
            ]
            # Packet-scale chunks: this route audits a research packet's claims
            # against their cited docs, and long multi-document contexts were
            # measured to dilute per-claim entailment confidence materially
            # (Nov 13 experiment: 5 of 8 batched-blocked clean claims passed
            # when re-asked singly). ~2 claims per call keeps contexts at the
            # packet scale the route is operated at.
            batched = run_batched(client, items, pacer=pacer, max_input_tokens=7_000)
            for idx, c in enumerate(uncached):
                cache.store(c["claim_id"], batched[idx])
        else:
            raise RuntimeError(
                f"GEMINI_API_KEY is not set and {len(uncached)} claims have no cached "
                "answers. This evaluation only scores live Decisions answers or "
                "previously cached live answers; it never synthesizes answers."
            )

    # 2. Score claims via audit_claims with cache-backed client
    # Manifest check is not applicable (no manifests for this fixture)
    tag_map = {c["claim_id"]: c["claim_id"] for c in claims}
    for idx, c in enumerate(claims):
        tag_map[f"i{idx+1:03d}"] = c["claim_id"]

    cache_client = CachedDecisionsClient(cache, id_map=tag_map)
    # Score in groups: full cited docs make a 100-claim single batch exceed
    # the route's joined-input token guard, which is the intended production
    # behavior (audit packets, not whole corpora, per run).
    all_results: list[dict[str, Any]] = []
    for start in range(0, len(claims), 20):
        group = claims[start:start + 20]
        group_out = audit_claims(
            group,
            client=cache_client,
            evidence_texts=evidence_texts,
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

    passed_all = contradicted_passed and planted_flaw_passed and clean_passed

    report_data = {
        "manifest_check": "not_applicable_no_manifests",
        "total_claims": len(claims),
        "blocked_count": len(audit_output["blocked_ids"]),
        "clear_count": len(audit_output["clear_ids"]),
        "passed_all": passed_all,
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

    def _status(p: bool) -> str:
        return "PASS" if p else "FAIL"

    print(f"  Contradicted recall:    {c_rec['value'] * 100:5.1f}%  (bar: 100.0%) -> {_status(c_rec['passed'])}")
    print(f"  Planted-flaw recall:    {f_rec['value'] * 100:5.1f}%  (bar: >=90.0%) -> {_status(f_rec['passed'])}")
    print(f"  Clean false-block rate: {cl_rate['value'] * 100:5.1f}%  (bar: <= 5.0%) -> {_status(cl_rate['passed'])}")
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
