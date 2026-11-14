"""Tests for DEC-03 evidence-chain packaging fix (Argon ruling RUL-20261113-dec-gate-design Q1).

Pure and hermetic tests for:
- extract_pointers extraction and deduplication rules
- package_evidence artifact resolution, directory listings, binary files, budget truncation, and trailers
- audit_claims route fail-closed deterministic missing_artifact blocking
- build_questions factual entailment instruction alignment
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ev.decisions_claim_auditor import audit_claims
from ev.decisions_prep.n3_claim_auditor import build_questions
from ev.decisions_prep.n3_evidence_packaging import (
    TRUNCATION_MARKER,
    extract_pointers,
    package_evidence,
    resolve_local,
)


# ---------------------------------------------------------------------------
# extract_pointers
# ---------------------------------------------------------------------------


def test_extract_pointers_source_doc_annotation_cut_order_and_dedupe() -> None:
    """extract_pointers cuts ' (annotation)', respects order, and dedupes."""
    claim = {
        "claim_id": "C01",
        "source_doc": "~/workspace/beezy/report.md (ESPN lineup pull wrong-week trap lesson, 2026-10-18)",
        "claim_text": "As noted in ~/workspace/beezy/report.md and secondary_data.csv, results match.",
        "evidence": "Verification against bare_file.json and repeated secondary_data.csv.",
    }
    pointers = extract_pointers(claim)

    # 1. source_doc with " (annotation)" cut
    assert pointers[0] == "~/workspace/beezy/report.md"

    # 2. inline ~/ path and bare filename found in order, deduped
    assert pointers == [
        "~/workspace/beezy/report.md",
        "secondary_data.csv",
        "bare_file.json",
    ]


# ---------------------------------------------------------------------------
# package_evidence with tmp_path
# ---------------------------------------------------------------------------


def test_package_evidence_resolution_and_multi_artifact(tmp_path: Path) -> None:
    """package_evidence attaches source_doc file and second artifact resolved via extra_roots."""
    doc_file = tmp_path / "source_doc.md"
    doc_file.write_text("Source document content line 1.", encoding="utf-8")

    extra_dir = tmp_path / "extra_search"
    extra_dir.mkdir()
    sec_file = extra_dir / "artifact2.csv"
    sec_file.write_text("col1,col2\nval1,val2\n", encoding="utf-8")

    claim = {
        "claim_id": "C02",
        "source_doc": str(doc_file),
        "claim_text": "Claims both artifacts.",
        "evidence": "Check artifact2.csv for raw numbers.",
    }

    packaged = package_evidence(claim, extra_roots=[extra_dir])

    assert len(packaged["attached"]) == 2
    assert packaged["multi_artifact"] is True
    assert packaged["missing"] == []

    attached_pointers = [a["pointer"] for a in packaged["attached"]]
    assert attached_pointers == [str(doc_file), "artifact2.csv"]

    # Verify content inlined
    assert "Source document content line 1." in packaged["evidence_text"]
    assert "col1,col2" in packaged["evidence_text"]
    assert f"--- Attached artifact: {doc_file}" in packaged["evidence_text"]
    assert "--- Attached artifact: artifact2.csv" in packaged["evidence_text"]


def test_package_evidence_missing_pointer_lands_in_missing_and_trailer(tmp_path: Path) -> None:
    """Missing local pointer lands in missing and MISSING_ARTIFACT trailer in evidence_text."""
    doc_file = tmp_path / "valid.md"
    doc_file.write_text("Valid content.", encoding="utf-8")

    claim = {
        "claim_id": "C03",
        "source_doc": str(doc_file),
        "claim_text": "Cites a missing file.",
        "evidence": "See definitely_nonexistent_table_98765.csv for details.",
    }

    packaged = package_evidence(claim, extra_roots=[tmp_path])

    assert "definitely_nonexistent_table_98765.csv" in packaged["missing"]
    assert "definitely_nonexistent_table_98765.csv" in packaged["evidence_text"]
    assert (
        "--- MISSING_ARTIFACT: the following cited artifacts could not be resolved and were not attached: "
        "definitely_nonexistent_table_98765.csv ---"
    ) in packaged["evidence_text"]


def test_package_evidence_external_pointer_only_in_external(tmp_path: Path) -> None:
    """gs:// and https:// pointers land in external only; not attached and not missing."""
    doc_file = tmp_path / "valid.md"
    doc_file.write_text("Valid content.", encoding="utf-8")

    claim = {
        "claim_id": "C04",
        "source_doc": str(doc_file),
        "claim_text": "Production https://beezy.fyi/tools returns 404.",
        "evidence": "Sink files in gs://concrete-crow-445205-m4-mlb-data/EVSweep/nfl_props/alt_ladder/ day files.",
    }

    packaged = package_evidence(claim, extra_roots=[tmp_path])

    assert "https://beezy.fyi/tools" in packaged["external"]
    assert "gs://concrete-crow-445205-m4-mlb-data/EVSweep/nfl_props/alt_ladder/" in packaged["external"]
    assert packaged["missing"] == []
    # External pointers should not appear in attached list or MISSING_ARTIFACT trailer
    assert not any("beezy.fyi" in a["pointer"] for a in packaged["attached"])
    assert not any("concrete-crow" in a["pointer"] for a in packaged["attached"])
    assert "MISSING_ARTIFACT" not in packaged["evidence_text"]


def test_package_evidence_directory_pointer_attaches_listing(tmp_path: Path) -> None:
    """Directory pointer attaches deterministic listing with file sizes and dir markers."""
    target_dir = tmp_path / "sample_dir"
    target_dir.mkdir()
    (target_dir / "b_file.txt").write_text("12345", encoding="utf-8")  # 5 bytes
    (target_dir / "a_file.csv").write_text("1234567890", encoding="utf-8")  # 10 bytes
    (target_dir / "sub_dir").mkdir()

    claim = {
        "claim_id": "C05",
        "claim_text": "Directory pointer test.",
        "evidence": f"Inspect {target_dir} contents.",
    }

    packaged = package_evidence(claim, extra_roots=[tmp_path])

    assert len(packaged["attached"]) == 1
    att = packaged["attached"][0]
    assert att["pointer"] == str(target_dir)

    ev_text = packaged["evidence_text"]
    assert f"--- Attached artifact: {target_dir} (resolved: {target_dir.resolve()}) ---" in ev_text
    # Deterministic alphabetical ordering: a_file.csv, b_file.txt, sub_dir/
    assert "a_file.csv\t10 bytes\nb_file.txt\t5 bytes\nsub_dir/\t(dir)" in ev_text


def test_package_evidence_tiny_budget_forces_truncation_marker(tmp_path: Path) -> None:
    """Tiny budget_tokens forces truncation marker and sets truncated=True."""
    doc_file = tmp_path / "long_doc.md"
    doc_file.write_text("A" * 1000, encoding="utf-8")

    claim = {
        "claim_id": "C06",
        "source_doc": str(doc_file),
        "claim_text": "Tiny budget test.",
        "evidence": "Baseline evidence prose.",
    }

    # With budget_tokens=150, the 1000-char document cannot fit in full
    packaged = package_evidence(claim, extra_roots=[tmp_path], budget_tokens=150)

    assert len(packaged["attached"]) == 1
    assert packaged["attached"][0]["truncated"] is True
    assert TRUNCATION_MARKER.strip() in packaged["evidence_text"]


# ---------------------------------------------------------------------------
# Route: deterministic missing_artifact blocking
# ---------------------------------------------------------------------------


class ExplodingClient:
    """Stub client that raises AssertionError if called."""

    def call(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Decisions client should NEVER be called when claim is blocked by missing_artifact!")


def test_route_missing_artifact_blocks_and_client_never_called() -> None:
    """A claim referencing a nonexistent local file is blocked with missing_artifact and client is never called."""
    claims = [
        {
            "claim_id": "MISSING_01",
            "claim_text": "Claim citing missing evidence artifact.",
            "evidence": "Evidence citing nonexistent_secondary_artifact_xyz987.csv explicitly.",
        }
    ]

    client = ExplodingClient()
    output = audit_claims(claims, client=client)

    assert output["blocked_ids"] == ["MISSING_01"]
    assert output["clear_ids"] == []
    res = output["results"][0]
    assert res["verdict"] == "missing_artifact"
    assert res["blocked"] is True
    assert any("missing_artifact:nonexistent_secondary_artifact_xyz987.csv" in r for r in res["reasons"])
    assert res["packaging"]["missing"] == ["nonexistent_secondary_artifact_xyz987.csv"]
    assert res["packaging"]["attached"] == []


# ---------------------------------------------------------------------------
# Prep: build_questions factual entailment
# ---------------------------------------------------------------------------


def test_build_questions_supported_instructions_factual_entailment() -> None:
    """build_questions supported question uses factual entailment and not 'exactly as stated'."""
    claim = {
        "claim_id": "N3-T01",
        "claim_text": "Factual entailment claim.",
        "source_doc": "~/doc.md",
    }
    body = build_questions(claim, "Evidence text.")
    supported_instr = body["questions"]["supported"]["instructions"]

    assert "factually entailed" in supported_instr
    assert "exactly as stated" not in supported_instr


# ---------------------------------------------------------------------------
# Review-pass additions: site routes are not filesystem pointers; copy-like
# directories lose bare-filename resolution to the live artifact.
# ---------------------------------------------------------------------------

def test_extract_pointers_skips_site_routes():
    claim = {
        "claim_text": "After the cleanup, production https://beezy.fyi/tools returns 404.",
        "evidence": "Final verification: /tools returns HTTP 404; see /picks/history and /ev/games.",
    }
    toks = extract_pointers(claim)
    assert "/tools" not in toks
    assert "/picks/history" not in toks
    assert "/ev/games" not in toks
    assert "https://beezy.fyi/tools" in toks  # the full URL is an external pointer


def test_extract_pointers_keeps_absolute_file_paths():
    claim = {"claim_text": "See /home/hatch/workspace/beezy/report.md and /tmp/out.csv.", "evidence": ""}
    toks = extract_pointers(claim)
    assert "/home/hatch/workspace/beezy/report.md" in toks
    assert "/tmp/out.csv" in toks


def test_resolve_local_prefers_live_artifact_over_copies(tmp_path):
    live = tmp_path / "evidence" / "ledger.jsonl"
    live.parent.mkdir(parents=True)
    live.write_text("live", encoding="utf-8")
    for sub in ("snapshot", "stage1", "pc-bundle"):
        d = tmp_path / sub
        d.mkdir()
        (d / "ledger.jsonl").write_text("copy", encoding="utf-8")
    path, count = resolve_local("ledger.jsonl", None, [tmp_path])
    assert count == 4
    assert path == live.resolve()
