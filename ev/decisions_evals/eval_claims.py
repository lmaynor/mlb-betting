"""Evaluation script for N3 research-claim evidence auditor route.

Evaluates the committed claims.jsonl fixture against Decisions API entailment
gates, scoring recall on planted flaws (contradicted, unsupported, stale,
wrong_scope) and false-block rates on clean claims.

Argon ruling RUL-20261113-dec-03-contradiction Q3 pre-registered bars
(two-stage end-to-end, 1 claim/call):
1. Contradicted recall = 100% (22/22)
2. Overall planted-flaw recall >= 92% (>= 46/50)
3. Multi-artifact synthesis-flaw recall = 100% (11/11)
4. Clean false-block rate <= 5.0% (<= 2/50)
5. Multi-artifact clean false-block <= 1/14 (7.1%)
6. Stage 2 routing volume <= 25% (<= 25/100)
7. Stage 2 fidelity: 100% confirmation on routed true contradictions;
   >= 85% clearance on routed cleans

Kill rules (kill the design, never tune thresholds post-hoc): contradicted
recall < 100% OR multi-artifact synthesis recall < 100% OR planted-flaw
recall < 90% (< 45/50) OR clean false-block > 10% (> 5/50) OR multi-artifact
clean false-block > 2/14.

DOCTRINE:
- Offline by default: uses AnswersCache (answers.jsonl) next to the fixture to
  avoid repeated API calls.
- Live fetching: only for missing answers when GEMINI_API_KEY is present,
  using live_client, Pacer, and run_batched. Stage 1 answers are fetched at
  1 claim/call; claims Stage 1 routes to Stage 2 then get their confirm
  answers fetched live at 1 claim/call; a final scoring pass produces the
  reported results. The evaluation never synthesizes answers.
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
from ev.decisions_prep.n3_claim_auditor import build_confirm_questions, build_questions
from ev.decisions_prep.n3_confirm_slice import build_confirm_slice
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

    def _resolve(self, wire_key: str, tag_to_cid: Mapping[str, str]) -> Optional[tuple[str, str]]:
        """Resolve a wire key ``{tag}_{qid}`` to (claim_id, qid).

        Question ids may themselves contain underscores (``wrong_scope``,
        ``confirm_direct_conflict``), so a naive ``rsplit("_", 1)`` mangles
        them (``N3-C029_wrong_scope`` -> tag ``N3-C029_wrong``, qid
        ``scope``) and the cached answer is silently dropped. Instead try
        every split: a split is valid when the tag resolves to a claim whose
        cached answers contain the qid; prefer the longest qid among valid
        splits. Returns None when no split resolves to a cached answer.
        """
        best: Optional[tuple[str, str]] = None
        for i, ch in enumerate(wire_key):
            if ch != "_":
                continue
            tag, qid = wire_key[:i], wire_key[i + 1:]
            if not qid:
                continue
            claim_id = tag_to_cid.get(tag, tag)
            ans = self.cache.get(claim_id, {})
            if qid in ans and (best is None or len(qid) > len(best[1])):
                best = (claim_id, qid)
        return best

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
            resolved = self._resolve(wire_key, tag_to_cid)
            if resolved is not None:
                claim_id, qid = resolved
                wire_answers[wire_key] = self.cache.get(claim_id, {})[qid]

        return BatchResult(answers=wire_answers, abstained=False)


def _score(
    claims: list[dict[str, Any]],
    cache: AnswersCache,
    packaged_by_id: Mapping[str, Any],
    evidence_texts: Mapping[str, str],
) -> dict[str, Any]:
    """Score all claims through the route with the cache-backed client.

    Claims are grouped adaptively (summed estimated input tokens <= 200,000
    per group; a single claim always forms a valid group) and each group is
    audited via audit_claims in shadow mode.
    """
    tag_map = {c["claim_id"]: c["claim_id"] for c in claims}
    for idx, c in enumerate(claims):
        tag_map[f"i{idx+1:03d}"] = c["claim_id"]

    cache_client = CachedDecisionsClient(cache, id_map=tag_map)

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

    return {
        "results": all_results,
        "blocked_ids": [r["claim_id"] for r in all_results if r["blocked"]],
        "clear_ids": [r["claim_id"] for r in all_results if not r["blocked"]],
        "mode": "shadow",
    }


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

    # 2. Score claims via audit_claims with cache-backed client.
    # Pass A establishes which claims Stage 1 routes to Stage 2; their
    # confirm answers are then fetched live (1 claim/call) if not cached;
    # pass B is the reported scoring.
    audit_output = _score(claims, cache, packaged_by_id, evidence_texts)

    routed_ids = [
        r["claim_id"]
        for r in audit_output["results"]
        if (r.get("stage2") or {}).get("routed")
    ]
    claims_by_id = {c["claim_id"]: c for c in claims}
    confirm_missing = [
        cid
        for cid in routed_ids
        if "confirm_direct_conflict" not in (cache.get(cid, {}) or {})
    ]
    if confirm_missing:
        if not os.environ.get("GEMINI_API_KEY"):
            raise RuntimeError(
                f"GEMINI_API_KEY is not set and {len(confirm_missing)} Stage 2 routed "
                "claims have no cached confirm answers. This evaluation only scores "
                "live Decisions answers or previously cached live answers; it never "
                "synthesizes answers."
            )
        client = live_client()
        pacer = Pacer()
        for cid in confirm_missing:
            claim = claims_by_id[cid]
            slice_info = build_confirm_slice(claim, packaged_by_id[cid])
            q = build_confirm_questions(claim, slice_info["slice_text"])
            fetched = run_batched(client, [(q["input"], q["questions"])], pacer=pacer)
            merged = {**(cache.get(cid, {}) or {}), **fetched[0]}
            cache.store(cid, merged)
        audit_output = _score(claims, cache, packaged_by_id, evidence_texts)

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

    # Planted-flaw recall (acceptance bar >= 92%; kill floor 90%)
    flaw_total = sum(per_label[lbl]["total"] for lbl in FLAW_LABELS)
    flaw_blocked = sum(per_label[lbl]["blocked"] for lbl in FLAW_LABELS)
    planted_flaw_recall = flaw_blocked / flaw_total if flaw_total > 0 else 0.0
    planted_flaw_passed = planted_flaw_recall >= 0.92

    # Clean false-block rate (bar <= 5%)
    clean_data = per_label["clean"]
    clean_false_block_rate = clean_data["blocked"] / clean_data["total"] if clean_data["total"] > 0 else 0.0
    clean_passed = clean_false_block_rate <= 0.05

    # Multi-artifact clean false-block (bar <= 1/14, discrete count)
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
        ma_clean_passed = ma_clean_blocked <= 1

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

    # Stage 2 routing volume and confirmation fidelity (final pass results)
    label_by_id = {c["claim_id"]: c["label"] for c in claims}
    routed_results = [
        r for r in audit_output["results"] if (r.get("stage2") or {}).get("routed")
    ]
    routed_count = len(routed_results)
    routing_rate = routed_count / len(claims) if claims else 0.0
    routing_passed = routing_rate <= 0.25

    routed_contra = [r for r in routed_results if label_by_id[r["claim_id"]] == "contradicted"]
    contra_confirmed = sum(
        1 for r in routed_contra if r["blocked"] and r["verdict"] == "contradicted"
    )
    if routed_contra:
        fid_contra_val: Optional[float] = round(contra_confirmed / len(routed_contra), 4)
        fid_contra_passed = contra_confirmed == len(routed_contra)
    else:
        fid_contra_val = None
        fid_contra_passed = True

    routed_cleans = [r for r in routed_results if label_by_id[r["claim_id"]] == "clean"]
    cleans_cleared = sum(1 for r in routed_cleans if not r["blocked"])
    if routed_cleans:
        fid_clean_val: Optional[float] = round(cleans_cleared / len(routed_cleans), 4)
        fid_clean_passed = (cleans_cleared / len(routed_cleans)) >= 0.85
    else:
        fid_clean_val = None
        fid_clean_passed = True

    # Kill rules (RUL-20261113-dec-03-contradiction Q3): kill, never tune.
    recall_kill = (
        not contradicted_passed
        or not ma_flaw_passed
        or planted_flaw_recall < 0.90
    )
    false_block_kill = (
        clean_data["blocked"] > 5
        or (ma_clean_total > 0 and ma_clean_blocked > 2)
    )
    design_killed = bool(recall_kill or false_block_kill)

    passed_all = (
        contradicted_passed
        and planted_flaw_passed
        and clean_passed
        and (ma_clean_passed if ma_clean_total > 0 else True)
        and (ma_flaw_passed if ma_flaw_total > 0 else True)
        and routing_passed
        and fid_contra_passed
        and fid_clean_passed
    )
    verdict = "KILLED" if design_killed else ("PASS" if passed_all else "FAIL")

    all_ext: set[str] = set()
    for p in packaged_by_id.values():
        all_ext.update(p["external"])

    report_data = {
        "manifest_check": "not_applicable_no_manifests",
        "total_claims": len(claims),
        "blocked_count": len(audit_output["blocked_ids"]),
        "clear_count": len(audit_output["clear_ids"]),
        "passed_all": passed_all,
        "design_killed": design_killed,
        "verdict": verdict,
        "kill_rules": {
            "recall_kill": bool(recall_kill),
            "false_block_kill": bool(false_block_kill),
            "design_killed": design_killed,
        },
        "stage2": {
            "routed_ids": [r["claim_id"] for r in routed_results],
            "routing_volume": {
                "value": round(routing_rate, 4),
                "bar": 0.25,
                "passed": routing_passed,
                "routed": routed_count,
                "total": len(claims),
            },
            "fidelity_contradicted": {
                "value": fid_contra_val,
                "bar": 1.0,
                "passed": fid_contra_passed,
                "confirmed": contra_confirmed,
                "routed": len(routed_contra),
            },
            "fidelity_cleans": {
                "value": fid_clean_val,
                "bar": 0.85,
                "passed": fid_clean_passed,
                "cleared": cleans_cleared,
                "routed": len(routed_cleans),
            },
        },
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
                "bar": 0.92,
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
                "bar": 0.071,
                "bar_count": 1,
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
    print(f"  Planted-flaw recall:              {f_rec['value'] * 100:5.1f}%  (bar: >=92.0%) -> {_status(f_rec['passed'])}")
    print(f"  Clean false-block rate:           {cl_rate['value'] * 100:5.1f}%  (bar: <= 5.0%) -> {_status(cl_rate['passed'])}")
    print(f"  Multi-artifact clean false-block: {ma_cl_str}  (bar: <= 1/14) -> {_status(ma_cl['passed'])}")
    print(f"  Multi-artifact flaw recall:       {ma_fl_str}  (bar: 100.0%) -> {_status(ma_fl['passed'])}")
    s2 = data["stage2"]
    rv = s2["routing_volume"]
    fc = s2["fidelity_contradicted"]
    fl = s2["fidelity_cleans"]
    fc_str = f"{fc['value'] * 100:5.1f}%" if fc["value"] is not None else " N/A "
    fl_str = f"{fl['value'] * 100:5.1f}%" if fl["value"] is not None else " N/A "
    print(f"  Stage 2 routing volume:           {rv['value'] * 100:5.1f}%  (bar: <=25.0%, {rv['routed']}/{rv['total']}) -> {_status(rv['passed'])}")
    print(f"  Stage 2 fidelity (contradicted):  {fc_str}  (bar: 100.0%, {fc['confirmed']}/{fc['routed']} routed) -> {_status(fc['passed'])}")
    print(f"  Stage 2 fidelity (cleans):        {fl_str}  (bar: >=85.0%, {fl['cleared']}/{fl['routed']} routed) -> {_status(fl['passed'])}")
    print("-" * 72)
    overall = "PASS" if data["passed_all"] else "FAIL"
    print(f"OVERALL EVALUATION: {overall}")
    print(f"DESIGN VERDICT: {data['verdict']}")
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
