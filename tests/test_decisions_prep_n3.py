"""Tests for the N3 decisions-prep workstream (F3 fixture + F7 auditor).

Pure and offline: the fixture is a local JSONL file, the auditor module
(ev/decisions_prep/n3_claim_auditor.py) makes no API calls, and the repo
conftest blocks network in tests regardless.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from ev.decisions_prep.n3_claim_auditor import (
    QUESTION_IDS,
    build_questions,
    check_numbers,
    extract_numeric_claims,
    gate,
)

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "decisions"
    / "n3_claims"
    / "claims.jsonl"
)

FLAW_LABELS = {"contradicted", "unsupported", "stale", "wrong_scope"}


def _load_claims() -> list[dict]:
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# Fixture shape
# ---------------------------------------------------------------------------


def test_fixture_has_exactly_100_claims():
    assert len(_load_claims()) == 100


def test_fixture_class_counts():
    counts = Counter(c["label"] for c in _load_claims())
    assert counts["clean"] == 50
    assert 45 <= counts["clean"] <= 55
    # Kill criterion grades contradicted recall at 100%: keep the class big.
    assert counts["contradicted"] >= 20
    # All four failure classes are present.
    for label in FLAW_LABELS:
        assert counts[label] >= 1, label
    assert sum(counts.values()) == 100


def test_fixture_required_fields_and_unique_ids():
    claims = _load_claims()
    ids = [c["claim_id"] for c in claims]
    assert len(set(ids)) == len(ids)
    for c in claims:
        for field in ("claim_id", "claim_text", "source_doc", "evidence", "label"):
            assert c.get(field), (c.get("claim_id"), field)
        assert c["label"] in FLAW_LABELS | {"clean"}


def test_seeded_claims_carry_provenance():
    for c in _load_claims():
        if c["label"] == "clean":
            continue
        assert c.get("seeded_from"), c["claim_id"]
        assert c.get("mutation"), c["claim_id"]


def test_source_docs_are_clean_resolvable_paths():
    # Lint (join audit, 2026-11-13): a claim's source_doc must be a bare
    # path that resolves to an existing file. Annotations belong in a
    # note field (see N3-S008's source_note), never inside the path:
    # the DEC-03 evidence packaging resolves source_doc literally and
    # fails closed on a path that does not exist.
    for c in _load_claims():
        raw = c["source_doc"]
        assert "(" not in raw and ")" not in raw, (c["claim_id"], raw)
        assert Path(raw).expanduser().is_file(), (c["claim_id"], raw)


# ---------------------------------------------------------------------------
# extract_numeric_claims
# ---------------------------------------------------------------------------


def test_extract_counts_percentages_prices():
    text = (
        "The sink banked 182,386 quotes; Novig covers 80.0% of prop-days; "
        "the plan costs $39/mo and the gate returned +5.1% ROI."
    )
    found = extract_numeric_claims(text)
    values = [v for v, _span in found]
    assert values == [182386.0, 80.0, 39.0, 5.1]
    spans = [span for _v, span in found]
    assert spans[0].startswith("182,386")
    assert spans[1] == "80.0%"
    assert spans[2].startswith("$39")


def test_extract_records_and_decimals():
    found = extract_numeric_claims("Novig was best on 527 of 1,653 bets; median spread 0.445.")
    assert [v for v, _s in found] == [527.0, 1653.0, 0.445]


def test_extract_range_yields_both_endpoints():
    values = [v for v, _s in extract_numeric_claims("market EV 3-10%")]
    assert values == [3.0, 10.0]


def test_extract_empty_and_numberless_text():
    assert extract_numeric_claims("") == []
    assert extract_numeric_claims("no literals here") == []


# ---------------------------------------------------------------------------
# check_numbers
# ---------------------------------------------------------------------------


def test_check_numbers_match_is_clean():
    claim = {
        "claim_id": "T1",
        "claim_text": "Banked 182,386 quotes from 2,020 payloads across 27 days.",
    }
    manifest = {"T1": [182386.0, 2020.0, 27.0]}
    assert check_numbers(claim, manifest) == []


def test_check_numbers_mismatch_reports_both_directions():
    claim = {"claim_id": "T1", "claim_text": "Banked 218,386 quotes from 2,020 payloads."}
    manifest = {"T1": {"expected": [{"value": 182386.0}, {"value": 2020.0}, {"value": 27.0}]}}
    mismatches = check_numbers(claim, manifest)
    kinds = {(m["type"], m["value"]) for m in mismatches}
    assert ("unexpected_literal", 218386.0) in kinds
    assert ("missing_expected", 182386.0) in kinds
    assert ("missing_expected", 27.0) in kinds
    assert all(m["claim_id"] == "T1" for m in mismatches)


def test_check_numbers_no_manifest_entry_no_opinion():
    claim = {"claim_id": "UNKNOWN", "claim_text": "Banked 5 quotes."}
    assert check_numbers(claim, {"OTHER": [5.0]}) == []


def test_check_numbers_against_real_fixture_claim():
    claims = {c["claim_id"]: c for c in _load_claims()}
    manifest = {"N3-C001": [182386.0, 2020.0, 27.0]}
    assert check_numbers(claims["N3-C001"], manifest) == []
    # The contradicted twin of the same seed must mismatch on the total.
    twin = dict(claims["N3-C001"])
    twin["claim_text"] = claims["N3-X001"]["claim_text"]
    kinds = {(m["type"], m["value"]) for m in check_numbers(twin, manifest)}
    assert ("unexpected_literal", 218386.0) in kinds
    assert ("missing_expected", 182386.0) in kinds


# ---------------------------------------------------------------------------
# build_questions
# ---------------------------------------------------------------------------


def test_build_questions_envelope_style():
    claim = {
        "claim_id": "N3-C001",
        "claim_text": "The sink banked 182,386 quotes.",
        "source_doc": "~/workspace/beezy/ladder-banking-2026-11-12.md",
    }
    body = build_questions(claim, "Sink read-back: 182,386 rows.")
    assert body["model"] == "gemini-decision-1"
    assert "182,386" in body["input"]
    assert "Sink read-back" in body["input"]
    # Published envelope: questions is a MAP keyed by question id.
    assert set(body["questions"]) == set(QUESTION_IDS)
    for q in body["questions"].values():
        assert q["type"] == "noul"
        assert q["instructions"]


def test_build_questions_accepts_plain_text_claim():
    body = build_questions("A plain claim.", "Some evidence.")
    assert body["questions"]["supported"]["type"] == "noul"
    assert "A plain claim." in body["input"]


# ---------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------


def test_gate_passes_supported_claim():
    answer = {"supported": 0.97, "contradicted": 0.01, "wrong_scope": 0.05, "stale": 0.05}
    result = gate(answer)
    assert result["decision"] == "pass"
    assert result["blocked"] is False
    assert result["reasons"] == []


def test_gate_blocks_low_supported():
    result = gate({"supported": 0.89, "contradicted": 0.0, "wrong_scope": 0.0, "stale": 0.0})
    assert result["blocked"] is True
    assert any(r.startswith("supported_below_threshold") for r in result["reasons"])


def test_gate_blocks_high_contradicted():
    result = gate({"supported": 0.95, "contradicted": 0.06, "wrong_scope": 0.0, "stale": 0.0})
    assert result["blocked"] is True
    assert any(r.startswith("contradicted_above_threshold") for r in result["reasons"])


def test_gate_blocks_scope_plus_stale_at_threshold():
    # Each alone is under 0.25; the ruling blocks on the SUM at >= 0.25.
    result = gate({"supported": 0.95, "contradicted": 0.0, "wrong_scope": 0.13, "stale": 0.12})
    assert result["blocked"] is True
    assert any(r.startswith("wrong_scope_plus_stale") for r in result["reasons"])


def test_gate_boundary_values_pass():
    # supported exactly 0.90, contradicted exactly 0.05, sum just under 0.25.
    result = gate({"supported": 0.90, "contradicted": 0.05, "wrong_scope": 0.12, "stale": 0.12})
    assert result["blocked"] is False


def test_gate_missing_supported_blocks_and_answer_map_shape():
    # Published response shape: answers keyed by id, each carrying a probability.
    result = gate({"contradicted": {"probability": 0.01}})
    assert result["blocked"] is True
    assert "missing_supported_probability" in result["reasons"]
    ok = gate(
        {
            "supported": {"probability": 0.99},
            "contradicted": {"probability": 0.0},
            "wrong_scope": {"probability": 0.1},
            "stale": {"probability": 0.1},
        }
    )
    assert ok["blocked"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
