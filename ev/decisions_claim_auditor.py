"""N3 research-claim evidence auditor route.

Verdicts only: no function in this module modifies claim text.

Argon ruling (Nov 13, 2026; candidate N3, ADOPT standalone; RUL-20261113-dec-gate-design Q1):
an internal gate auditing research packets and build briefs before downstream
consumption. Pairs deterministic literal-number checks against structured manifests
with deterministic evidence-chain packaging (failing closed with MISSING_ARTIFACT)
and a single batched Decisions API call for entailment, scope, and freshness questions.

TWO-STAGE per RUL-20261113-dec-03-contradiction: the Stage 1 call uses the
split conflict heads (direct_conflict / missing_support). Only claims Stage 1
flags (non-conflict prongs pass AND P(direct_conflict) > 0.05) go to a second
batched Stage 2 confirm call over deterministic windowed slices
(ev.decisions_prep.n3_confirm_slice); Stage 2 records a CONTRADICTED block
iff P_confirm(direct_conflict) > 0.20, else clears the claim.

DOCTRINE:
- Deterministic checks run first: claims with numeric mismatches are blocked
  immediately and never sent over the wire to Decisions API.
- Evidence packaging: claims without explicit evidence overrides are packaged with
  their full cited evidence chain; missing local artifacts block fail-closed immediately.
- Fail closed: batch abstention, missing client key, or missing supported
  probability marks remaining claims as abstained and blocked.
- Shadow mode by default: observes and logs verdicts without applying action.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from ev.decisions_client import BatchResult, DecisionsClient, ItemBatch
from ev.decisions_common import (
    MAX_INPUT_TOKENS,
    append_records,
    estimate_tokens,
    make_record,
    route_mode,
)
from ev.decisions_prep.n3_claim_auditor import (
    CONFIRM_QUESTION_ID,
    build_confirm_questions,
    build_questions,
    check_numbers,
    confirm_gate,
    gate,
)
from ev.decisions_prep.n3_confirm_slice import build_confirm_slice
from ev.decisions_prep.n3_evidence_packaging import package_evidence

ROUTE_NAME: str = "n3_claim_auditor"
ENV_ROUTE_MODE: str = "DECISIONS_CLAIMS_MODE"
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
EVIDENCE_CAP: int = 40000  # chars of evidence per claim (full cited doc in practice)


# Superseded by ev.decisions_prep.n3_evidence_packaging in main; retained for test compatibility.
def _resolve_source_text(
    doc_path: Optional[str],
    source_root: Optional[Union[str, os.PathLike[str]]] = None,
) -> Optional[str]:
    """Resolve and read a source document file if accessible on disk."""
    if not doc_path:
        return None

    candidates: list[Path] = []
    if source_root:
        root = Path(source_root)
        candidates.append(root / Path(doc_path).name)
        stripped = doc_path.replace("~/", "").lstrip("/")
        candidates.append(root / stripped)

    expanded = Path(os.path.expanduser(doc_path))
    candidates.append(expanded)

    for p in candidates:
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return None


def audit_claims(
    claims: Sequence[Union[str, Mapping[str, Any]]],
    client: Optional[Any] = None,
    *,
    manifests: Optional[Mapping[str, Any]] = None,
    evidence_texts: Optional[Mapping[str, str]] = None,
    packaging_roots: Optional[Sequence[Any]] = None,
    mode: Optional[str] = None,
    log_path: Optional[Union[str, os.PathLike[str]]] = None,
) -> dict[str, Any]:
    """Audit research claims against evidence and optional numeric manifests.

    Verdicts only: does not modify or auto-repair claim text.

    Args:
        claims: Sequence of claim dictionaries (or strings).
        client: Optional DecisionsClient or compatible test double. If None,
            constructs DecisionsClient(); if construction fails (e.g. missing
            API key), remaining claims are treated as abstained without raising.
        manifests: Optional mapping of claim_id -> expected numbers (either
            {"numbers": [...]} or a plain list of numbers).
        evidence_texts: Optional mapping of claim_id -> evidence string override.
        packaging_roots: Optional sequence of extra search roots for evidence packaging.
        mode: Route mode ("shadow" or "live"). Defaults to env var
            DECISIONS_CLAIMS_MODE or "shadow".
        log_path: Optional path to JSONL log file for decision records.

    Returns:
        {"results": [...], "blocked_ids": [...], "clear_ids": [...], "mode": mode}
    """
    resolved_mode = mode or route_mode(ENV_ROUTE_MODE)
    applied = resolved_mode == "live"

    results_by_index: dict[int, dict[str, Any]] = {}
    batch_candidates: list[tuple[int, Any, str, str]] = []
    packaging_by_index: dict[int, dict[str, Any]] = {}
    packaging_full_by_index: dict[int, Optional[dict[str, Any]]] = {}
    stage2_candidates: list[tuple[int, Any, str, dict[str, Any], str]] = []

    # a) Deterministic numeric check first
    for idx, claim in enumerate(claims):
        if isinstance(claim, Mapping):
            cid = claim.get("claim_id")
        else:
            cid = None
        claim_id = str(cid) if cid is not None else f"claim_{idx:03d}"

        if manifests and manifests.get(claim_id) is not None:
            raw_entry = manifests.get(claim_id)
            if isinstance(raw_entry, list):
                norm_manifest = {claim_id: {"numbers": raw_entry}}
            elif isinstance(raw_entry, Mapping):
                norm_manifest = {claim_id: dict(raw_entry)}
            else:
                norm_manifest = {claim_id: raw_entry}

            findings = check_numbers(claim, norm_manifest)
            if findings:
                reasons: list[str] = []
                for f in findings:
                    if "reason" in f:
                        reasons.append(str(f["reason"]))
                    elif "type" in f and "value" in f:
                        reasons.append(f"{f['type']}:{f['value']}")
                    elif "type" in f:
                        reasons.append(str(f["type"]))
                    else:
                        reasons.append("numeric_mismatch")

                results_by_index[idx] = {
                    "claim_id": claim_id,
                    "verdict": "numeric_mismatch",
                    "blocked": True,
                    "reasons": reasons,
                    "numeric_findings": findings,
                    "gate_result": None,
                    "mode": resolved_mode,
                    "applied": applied,
                    "stage2": {"routed": False},
                }
                continue

        # Claim has no manifest mismatch; prepare for Decisions batch
        ev_text = ""
        if evidence_texts is not None and claim_id in evidence_texts:
            ev_text = evidence_texts[claim_id]
            packaging_by_index[idx] = {"attached": [], "missing": []}
            packaging_full_by_index[idx] = None
        else:
            packaged = package_evidence(claim, extra_roots=packaging_roots or ())
            packaging_full_by_index[idx] = packaged
            if packaged["missing"]:
                results_by_index[idx] = {
                    "claim_id": claim_id,
                    "verdict": "missing_artifact",
                    "blocked": True,
                    "reasons": [f"missing_artifact:{p}" for p in packaged["missing"]],
                    "numeric_findings": [],
                    "gate_result": None,
                    "mode": resolved_mode,
                    "applied": applied,
                    "packaging": {
                        "attached": [a["pointer"] for a in packaged["attached"]],
                        "missing": packaged["missing"],
                    },
                    "stage2": {"routed": False},
                }
                continue
            ev_text = packaged["evidence_text"]
            packaging_by_index[idx] = {
                "attached": [a["pointer"] for a in packaged["attached"]],
                "missing": [],
            }

        batch_candidates.append((idx, claim, claim_id, ev_text))

    # b) Batched call via ItemBatch for remaining claims
    if batch_candidates:
        batch = ItemBatch()
        tag_to_candidate: dict[str, tuple[int, Any, str, str]] = {}

        for item in batch_candidates:
            idx, claim, claim_id, ev_text = item
            q_spec = build_questions(claim, ev_text)
            input_block = q_spec["input"]
            questions = q_spec["questions"]

            clean_tag = str(claim_id)
            if _KEY_RE.match(clean_tag) and len(clean_tag) <= 48:
                tag = batch.add(input_block, questions, tag=clean_tag)
            else:
                tag = batch.add(input_block, questions)
            tag_to_candidate[tag] = item

        input_text, wire_questions = batch.build()

        if estimate_tokens(input_text) > int(MAX_INPUT_TOKENS * 0.9):
            raise ValueError(
                f"Joined input estimated at {estimate_tokens(input_text)} tokens, "
                f"exceeding 90% of MAX_INPUT_TOKENS ({int(MAX_INPUT_TOKENS * 0.9)})"
            )

        active_client = client
        if active_client is None:
            try:
                active_client = DecisionsClient()
            except Exception:
                active_client = None

        if active_client is None:
            # Constructor failure (no key) -> treat remaining claims as abstained
            for idx, _claim, claim_id, _ in batch_candidates:
                results_by_index[idx] = {
                    "claim_id": claim_id,
                    "verdict": "abstained",
                    "blocked": True,
                    "reasons": ["batch_abstained"],
                    "numeric_findings": [],
                    "gate_result": None,
                    "mode": resolved_mode,
                    "applied": applied,
                    "packaging": packaging_by_index.get(idx, {"attached": [], "missing": []}),
                    "stage2": {"routed": False},
                }
        else:
            batch_result = active_client.call(input_text, wire_questions)
            # c) On batch abstention
            if batch_result.abstained:
                for idx, _claim, claim_id, _ in batch_candidates:
                    results_by_index[idx] = {
                        "claim_id": claim_id,
                        "verdict": "abstained",
                        "blocked": True,
                        "reasons": ["batch_abstained"],
                        "numeric_findings": [],
                        "gate_result": None,
                        "mode": resolved_mode,
                        "applied": applied,
                        "packaging": packaging_by_index.get(idx, {"attached": [], "missing": []}),
                        "stage2": {"routed": False},
                    }
            else:
                # d) Otherwise per claim
                split_answers = batch.split(batch_result)
                for tag, (idx, _claim, claim_id, ev_text) in tag_to_candidate.items():
                    raw_answer_dict = split_answers.get(tag, {})
                    supported = DecisionsClient.noul_value(raw_answer_dict.get("supported"))

                    if supported is None:
                        results_by_index[idx] = {
                            "claim_id": claim_id,
                            "verdict": "abstained",
                            "blocked": True,
                            "reasons": ["supported_missing"],
                            "numeric_findings": [],
                            "gate_result": None,
                            "mode": resolved_mode,
                            "applied": applied,
                            "packaging": packaging_by_index.get(idx, {"attached": [], "missing": []}),
                            "stage2": {"routed": False},
                        }
                    else:
                        gate_answer: dict[str, Any] = dict(raw_answer_dict)
                        gate_answer["supported"] = supported
                        for k in ("claim_kind", "scope_match", "evidence_freshness"):
                            if k not in gate_answer or gate_answer[k] is None:
                                gate_answer[k] = {}
                        for qid in ("direct_conflict", "missing_support", "wrong_scope", "stale"):
                            if qid in gate_answer and isinstance(gate_answer[qid], Mapping):
                                if "probability" not in gate_answer[qid] and "noul" in gate_answer[qid]:
                                    m = dict(gate_answer[qid])
                                    m["probability"] = m["noul"]
                                    gate_answer[qid] = m

                        gate_result = gate(gate_answer)
                        if gate_result.get("decision") == "route_stage2":
                            # Defer: the Stage 2 confirm phase below fills
                            # this claim's result.
                            stage2_candidates.append(
                                (idx, _claim, claim_id, gate_result, ev_text)
                            )
                            continue
                        is_blocked = bool(gate_result.get("blocked", False))
                        verdict = "blocked" if is_blocked else "clear"
                        reasons = list(gate_result.get("reasons", []))

                        results_by_index[idx] = {
                            "claim_id": claim_id,
                            "verdict": verdict,
                            "blocked": is_blocked,
                            "reasons": reasons,
                            "numeric_findings": [],
                            "gate_result": gate_result,
                            "mode": resolved_mode,
                            "applied": applied,
                            "packaging": packaging_by_index.get(idx, {"attached": [], "missing": []}),
                            "stage2": {"routed": False},
                        }

    # Stage 2: isolated confirm pass over Stage 1 conflict-flagged claims.
    # Runs only when Stage 1 routed at least one claim; reaching this point
    # with candidates implies the Stage 1 batch produced answers, so
    # active_client is a working client.
    if stage2_candidates:
        def _stage2_abstained(reason: str) -> None:
            for idx, _c, claim_id, gate_result, _ev in stage2_candidates:
                results_by_index[idx] = {
                    "claim_id": claim_id,
                    "verdict": "abstained",
                    "blocked": True,
                    "reasons": [reason],
                    "numeric_findings": [],
                    "gate_result": gate_result,
                    "mode": resolved_mode,
                    "applied": applied,
                    "packaging": packaging_by_index.get(idx, {"attached": [], "missing": []}),
                    "stage2": {
                        "routed": True,
                        "decision": "abstained",
                        "confirm_probability": None,
                        "reasons": [reason],
                        "slice_fallback_full_chain": None,
                        "slice_windows": {},
                    },
                }

        batch2 = ItemBatch()
        tag_to_stage2: dict[str, tuple[int, str, dict[str, Any], Optional[dict[str, Any]]]] = {}
        for idx, claim, claim_id, _gr, ev_text in stage2_candidates:
            packaged = packaging_full_by_index.get(idx)
            if packaged is not None:
                slice_info: Optional[dict[str, Any]] = build_confirm_slice(claim, packaged)
                slice_text = slice_info["slice_text"]
            else:
                slice_info = None
                slice_text = ev_text
            q_spec = build_confirm_questions(claim, slice_text)
            clean_tag = str(claim_id)
            if _KEY_RE.match(clean_tag) and len(clean_tag) <= 48:
                tag = batch2.add(q_spec["input"], q_spec["questions"], tag=clean_tag)
            else:
                tag = batch2.add(q_spec["input"], q_spec["questions"])
            tag_to_stage2[tag] = (idx, claim_id, _gr, slice_info)

        input_text2, wire_questions2 = batch2.build()
        if estimate_tokens(input_text2) > int(MAX_INPUT_TOKENS * 0.9):
            raise ValueError(
                f"Stage 2 joined input estimated at {estimate_tokens(input_text2)} tokens, "
                f"exceeding 90% of MAX_INPUT_TOKENS ({int(MAX_INPUT_TOKENS * 0.9)})"
            )

        result2 = active_client.call(input_text2, wire_questions2)
        if result2.abstained:
            _stage2_abstained("batch_abstained")
        else:
            split2 = batch2.split(result2)
            for tag, (idx, claim_id, gate_result, slice_info) in tag_to_stage2.items():
                raw2 = split2.get(tag, {})
                p = DecisionsClient.noul_value(raw2.get(CONFIRM_QUESTION_ID))
                cg = confirm_gate(
                    {CONFIRM_QUESTION_ID: {"probability": p}} if p is not None else {}
                )
                blocked2 = bool(cg["blocked"])
                if p is None:
                    verdict2 = "abstained"
                elif blocked2:
                    verdict2 = "contradicted"
                else:
                    verdict2 = "clear"
                results_by_index[idx] = {
                    "claim_id": claim_id,
                    "verdict": verdict2,
                    "blocked": blocked2,
                    "reasons": list(cg["reasons"]) if (blocked2 or p is None) else [],
                    "numeric_findings": [],
                    "gate_result": gate_result,
                    "mode": resolved_mode,
                    "applied": applied,
                    "packaging": packaging_by_index.get(idx, {"attached": [], "missing": []}),
                    "stage2": {
                        "routed": True,
                        "decision": "block" if blocked2 else "clear",
                        "confirm_probability": p,
                        "reasons": list(cg["reasons"]),
                        "slice_fallback_full_chain": (slice_info or {}).get("fallback_full_chain"),
                        "slice_windows": (slice_info or {}).get("window_counts", {}),
                    },
                }

    # e) Collect ordered results
    ordered_results = [results_by_index[i] for i in range(len(claims))]
    blocked_ids = [r["claim_id"] for r in ordered_results if r["blocked"]]
    clear_ids = [r["claim_id"] for r in ordered_results if not r["blocked"]]

    if log_path is not None:
        records = [
            make_record(
                route=ROUTE_NAME,
                item_id=str(r["claim_id"]),
                decision=r["verdict"],
                mode=r["mode"],
                applied=r["applied"],
                blocked=r["blocked"],
                reasons=r["reasons"],
                numeric_findings=r["numeric_findings"],
                gate_result=r.get("gate_result"),
            )
            for r in ordered_results
        ]
        append_records(log_path, records)

    return {
        "results": ordered_results,
        "blocked_ids": blocked_ids,
        "clear_ids": clear_ids,
        "mode": resolved_mode,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint for N3 research-claim evidence auditor."""
    parser = argparse.ArgumentParser(description="N3 research-claim evidence auditor")
    parser.add_argument("--claims", required=True, help="Path to input claims JSONL file")
    parser.add_argument("--manifest", help="Path to manifest JSON file (claim_id -> numbers)")
    parser.add_argument("--source-root", help="Path to source root directory for resolving source_doc")
    parser.add_argument("--out", required=True, help="Path to write verdicts JSON file")
    parser.add_argument("--mode", choices=["shadow", "live"], help="Audit route mode (shadow or live)")
    parser.add_argument("--log", help="Path to log JSONL file for decision records")

    args = parser.parse_args(argv)

    claims_path = Path(args.claims)
    if not claims_path.is_file():
        sys.stderr.write(f"Claims file not found: {args.claims}\n")
        return 1

    claims: list[dict[str, Any]] = []
    with open(claims_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                claims.append(json.loads(line))

    manifests: Optional[dict[str, Any]] = None
    if args.manifest:
        manifest_path = Path(args.manifest)
        if not manifest_path.is_file():
            sys.stderr.write(f"Manifest file not found: {args.manifest}\n")
            return 1
        manifests = json.loads(manifest_path.read_text(encoding="utf-8"))

    packaging_roots = [args.source_root] if args.source_root else None

    result = audit_claims(
        claims,
        manifests=manifests,
        packaging_roots=packaging_roots,
        mode=args.mode,
        log_path=args.log,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print(f"Blocked: {len(result['blocked_ids'])}, Clear: {len(result['clear_ids'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
