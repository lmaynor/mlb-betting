"""Offline unit tests for N3 research-claim evidence auditor route.

Hermetic test suite: zero live Decisions API calls. All tests inject spy/stub
clients or evaluate purely offline behaviors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

from ev.decisions_claim_auditor import audit_claims, main
from ev.decisions_client import BatchResult
from ev.decisions_common import MAX_INPUT_TOKENS


class FakeClient:
    """Spy Decisions client for offline route verification."""

    def __init__(
        self,
        answers: Optional[Mapping[str, Any]] = None,
        abstain: bool = False,
        abstention_reason: str = "simulated_timeout",
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answers = dict(answers or {})
        self.abstain = abstain
        self.abstention_reason = abstention_reason

    def call(self, input_text: str, questions: Mapping[str, Any]) -> BatchResult:
        self.calls.append({
            "input_text": input_text,
            "questions": dict(questions),
        })
        if self.abstain:
            return BatchResult.abstain(self.abstention_reason)

        res_answers: dict[str, Any] = {}
        for wire_key in questions:
            if self.answers is not None and len(self.answers) > 0:
                if wire_key in self.answers:
                    res_answers[wire_key] = self.answers[wire_key]
            else:
                qid = wire_key.rsplit("_", 1)[-1] if "_" in wire_key else wire_key
                if qid == "supported":
                    res_answers[wire_key] = {"probability": 0.96}
                elif qid == "contradicted":
                    res_answers[wire_key] = {"probability": 0.01}
                elif qid in ("wrong_scope", "stale"):
                    res_answers[wire_key] = {"probability": 0.05}
                else:
                    res_answers[wire_key] = {"probability": 0.0}

        return BatchResult(answers=res_answers, abstained=False)


# ---------------------------------------------------------------------------
# Numeric mismatch tests
# ---------------------------------------------------------------------------


def test_numeric_mismatch_blocks_without_client_call_for_that_claim() -> None:
    """A claim with a manifest numeric mismatch blocks immediately and is excluded from batch."""
    claims = [
        {"claim_id": "C1", "claim_text": "Banked 100 quotes.", "evidence": "text"},
        {"claim_id": "C2", "claim_text": "Plain qualitative statement.", "evidence": "text"},
    ]
    manifests = {"C1": [200.0]}  # Claim states 100, manifest expects 200 -> mismatch
    client = FakeClient()

    output = audit_claims(claims, client=client, manifests=manifests)

    # 1. Exactly one client call made for C2 only
    assert len(client.calls) == 1
    call_questions = client.calls[0]["questions"]
    # Wire keys for C2 exist, C1 wire keys must NOT exist in the batch
    assert any("C2" in k for k in call_questions)
    assert not any("C1" in k for k in call_questions)

    # 2. Check result for C1: numeric mismatch blocked
    res_c1 = next(r for r in output["results"] if r["claim_id"] == "C1")
    assert res_c1["verdict"] == "numeric_mismatch"
    assert res_c1["blocked"] is True
    assert len(res_c1["numeric_findings"]) > 0
    assert len(res_c1["reasons"]) > 0

    # 3. Check result for C2: clear
    res_c2 = next(r for r in output["results"] if r["claim_id"] == "C2")
    assert res_c2["verdict"] == "clear"
    assert res_c2["blocked"] is False
    assert res_c2["reasons"] == []

    # 4. Summary lists
    assert output["blocked_ids"] == ["C1"]
    assert output["clear_ids"] == ["C2"]


def test_all_numeric_mismatches_skip_client_completely() -> None:
    """When all claims fail numeric check, zero Decisions API calls are made."""
    claims = [
        {"claim_id": "C1", "claim_text": "States 10.", "evidence": "e1"},
        {"claim_id": "C2", "claim_text": "States 20.", "evidence": "e2"},
    ]
    manifests = {"C1": [15.0], "C2": [25.0]}
    client = FakeClient()

    output = audit_claims(claims, client=client, manifests=manifests)

    assert len(client.calls) == 0
    assert len(output["blocked_ids"]) == 2
    assert output["clear_ids"] == []


# ---------------------------------------------------------------------------
# Clean answers clear
# ---------------------------------------------------------------------------


def test_clean_answers_clear() -> None:
    """High supported and low flaw probabilities yield a clear verdict."""
    claims = [
        {"claim_id": "CLEAN_1", "claim_text": "Valid research claim.", "evidence": "Evidence text."},
    ]
    custom_answers = {
        "CLEAN_1_supported": {"probability": 0.98},
        "CLEAN_1_direct_conflict": {"probability": 0.01},
        "CLEAN_1_missing_support": {"probability": 0.01},
        "CLEAN_1_wrong_scope": {"probability": 0.02},
        "CLEAN_1_stale": {"probability": 0.01},
    }
    client = FakeClient(answers=custom_answers)

    output = audit_claims(claims, client=client)

    assert output["blocked_ids"] == []
    assert output["clear_ids"] == ["CLEAN_1"]
    res = output["results"][0]
    assert res["verdict"] == "clear"
    assert res["blocked"] is False
    assert res["reasons"] == []
    assert res["gate_result"]["decision"] == "clear"
    assert res["stage2"]["routed"] is False


# ---------------------------------------------------------------------------
# Gate threshold answers block
# ---------------------------------------------------------------------------


def test_gate_threshold_contradicted_blocks() -> None:
    """Stage 1 flags P(direct_conflict) > 0.05; Stage 2 confirm > 0.20 blocks as contradicted."""
    claims = [{"claim_id": "CONTRA_1", "claim_text": "Mutated fact.", "evidence": "Truth."}]
    custom_answers = {
        "CONTRA_1_supported": {"probability": 0.95},
        "CONTRA_1_direct_conflict": {"probability": 0.20},
        "CONTRA_1_missing_support": {"probability": 0.0},
        "CONTRA_1_wrong_scope": {"probability": 0.0},
        "CONTRA_1_stale": {"probability": 0.0},
        "CONTRA_1_confirm_direct_conflict": {"probability": 0.85},
    }
    client = FakeClient(answers=custom_answers)

    output = audit_claims(claims, client=client)

    assert output["blocked_ids"] == ["CONTRA_1"]
    assert output["clear_ids"] == []
    res = output["results"][0]
    assert res["verdict"] == "contradicted"
    assert res["blocked"] is True
    assert res["stage2"]["routed"] is True
    assert res["stage2"]["confirm_probability"] == 0.85
    assert any("confirm_direct_conflict_above_threshold" in r for r in res["reasons"])
    # Two calls: the Stage 1 batch and the Stage 2 confirm batch.
    assert len(client.calls) == 2


def test_stage2_confirm_low_clears() -> None:
    """A Stage 1 conflict flag that Stage 2 does not confirm clears."""
    claims = [{"claim_id": "FLAG_1", "claim_text": "Collision-prone fact.", "evidence": "Truth."}]
    custom_answers = {
        "FLAG_1_supported": {"probability": 0.96},
        "FLAG_1_direct_conflict": {"probability": 0.30},
        "FLAG_1_missing_support": {"probability": 0.0},
        "FLAG_1_wrong_scope": {"probability": 0.0},
        "FLAG_1_stale": {"probability": 0.0},
        "FLAG_1_confirm_direct_conflict": {"probability": 0.10},
    }
    client = FakeClient(answers=custom_answers)

    output = audit_claims(claims, client=client)

    assert output["blocked_ids"] == []
    assert output["clear_ids"] == ["FLAG_1"]
    res = output["results"][0]
    assert res["verdict"] == "clear"
    assert res["stage2"]["routed"] is True
    assert res["stage2"]["decision"] == "clear"


def test_gate_threshold_low_supported_blocks() -> None:
    """P(supported) < 0.90 blocks the claim."""
    claims = [{"claim_id": "LOW_SUPP", "claim_text": "Doubtful fact.", "evidence": "Thin."}]
    custom_answers = {
        "LOW_SUPP_supported": {"probability": 0.85},
        "LOW_SUPP_direct_conflict": {"probability": 0.01},
        "LOW_SUPP_missing_support": {"probability": 0.0},
        "LOW_SUPP_wrong_scope": {"probability": 0.0},
        "LOW_SUPP_stale": {"probability": 0.0},
    }
    client = FakeClient(answers=custom_answers)

    output = audit_claims(claims, client=client)

    res = output["results"][0]
    assert res["verdict"] == "blocked"
    assert res["blocked"] is True
    assert any("supported_below_threshold" in r for r in res["reasons"])


def test_gate_threshold_scope_plus_stale_blocks() -> None:
    """P(wrong_scope) + P(stale) >= 0.25 blocks the claim."""
    claims = [{"claim_id": "SCOPE_STALE", "claim_text": "Outdated scope.", "evidence": "New."}]
    custom_answers = {
        "SCOPE_STALE_supported": {"probability": 0.95},
        "SCOPE_STALE_direct_conflict": {"probability": 0.01},
        "SCOPE_STALE_missing_support": {"probability": 0.0},
        "SCOPE_STALE_wrong_scope": {"probability": 0.15},
        "SCOPE_STALE_stale": {"probability": 0.15},
    }
    client = FakeClient(answers=custom_answers)

    output = audit_claims(claims, client=client)

    res = output["results"][0]
    assert res["verdict"] == "blocked"
    assert res["blocked"] is True
    assert any("wrong_scope_plus_stale" in r for r in res["reasons"])


# ---------------------------------------------------------------------------
# Abstained batch handling
# ---------------------------------------------------------------------------


def test_abstained_batch_blocks_all_non_numeric_claims() -> None:
    """When the batched client abstains, all non-numeric claims fail closed as abstained."""
    claims = [
        {"claim_id": "NUM_FAIL", "claim_text": "States 50.", "evidence": "e1"},
        {"claim_id": "CLAIM_A", "claim_text": "Valid statement A.", "evidence": "e2"},
        {"claim_id": "CLAIM_B", "claim_text": "Valid statement B.", "evidence": "e3"},
    ]
    manifests = {"NUM_FAIL": [99.0]}
    client = FakeClient(abstain=True, abstention_reason="simulated_timeout")

    output = audit_claims(claims, client=client, manifests=manifests)

    assert set(output["blocked_ids"]) == {"NUM_FAIL", "CLAIM_A", "CLAIM_B"}
    assert output["clear_ids"] == []

    # NUM_FAIL retained its deterministic numeric verdict
    r_num = next(r for r in output["results"] if r["claim_id"] == "NUM_FAIL")
    assert r_num["verdict"] == "numeric_mismatch"

    # CLAIM_A and CLAIM_B got abstained verdict and batch_abstained reason
    for cid in ("CLAIM_A", "CLAIM_B"):
        r = next(r for r in output["results"] if r["claim_id"] == cid)
        assert r["verdict"] == "abstained"
        assert r["blocked"] is True
        assert "batch_abstained" in r["reasons"]


# ---------------------------------------------------------------------------
# Missing supported probability
# ---------------------------------------------------------------------------


def test_missing_supported_probability_yields_abstained_verdict() -> None:
    """If the supported answer is missing or None, the claim verdict is abstained."""
    claims = [{"claim_id": "NO_SUPP", "claim_text": "Some claim.", "evidence": "e"}]
    # Answers dictionary contains other fields but lacks 'supported'
    custom_answers = {
        "NO_SUPP_direct_conflict": {"probability": 0.01},
        "NO_SUPP_missing_support": {"probability": 0.0},
        "NO_SUPP_wrong_scope": {"probability": 0.01},
        "NO_SUPP_stale": {"probability": 0.01},
    }
    client = FakeClient(answers=custom_answers)

    output = audit_claims(claims, client=client)

    res = output["results"][0]
    assert res["verdict"] == "abstained"
    assert res["blocked"] is True
    assert res["reasons"] == ["supported_missing"]
    assert output["blocked_ids"] == ["NO_SUPP"]


# ---------------------------------------------------------------------------
# Shadow vs Live mode
# ---------------------------------------------------------------------------


def test_shadow_and_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shadow mode sets applied=False; live mode sets applied=True."""
    claim = [{"claim_id": "C_MODE", "claim_text": "Test claim.", "evidence": "e"}]
    client = FakeClient()

    # Explicit shadow
    out_shadow = audit_claims(claim, client=client, mode="shadow")
    assert out_shadow["mode"] == "shadow"
    assert out_shadow["results"][0]["applied"] is False

    # Explicit live
    out_live = audit_claims(claim, client=client, mode="live")
    assert out_live["mode"] == "live"
    assert out_live["results"][0]["applied"] is True

    # Environment variable DECISIONS_CLAIMS_MODE
    monkeypatch.setenv("DECISIONS_CLAIMS_MODE", "live")
    out_env_live = audit_claims(claim, client=client)
    assert out_env_live["mode"] == "live"
    assert out_env_live["results"][0]["applied"] is True

    monkeypatch.setenv("DECISIONS_CLAIMS_MODE", "shadow")
    out_env_shadow = audit_claims(claim, client=client)
    assert out_env_shadow["mode"] == "shadow"
    assert out_env_shadow["results"][0]["applied"] is False


# ---------------------------------------------------------------------------
# Token budget guarding
# ---------------------------------------------------------------------------


def test_input_token_budget_exceeded_raises() -> None:
    """If joined input exceeds 90% of MAX_INPUT_TOKENS, ValueError is raised."""
    # MAX_INPUT_TOKENS * 0.9 = 216,000 tokens ~= 756,000 characters
    giant_text = "x" * int(MAX_INPUT_TOKENS * 0.95 * 3.5)
    claims = [{"claim_id": "GIANT", "claim_text": "Giant claim.", "evidence": giant_text}]
    client = FakeClient()

    with pytest.raises(ValueError, match="exceeding 90% of MAX_INPUT_TOKENS"):
        audit_claims(claims, client=client)


# ---------------------------------------------------------------------------
# Client None with missing API key
# ---------------------------------------------------------------------------


def test_client_none_constructor_failure_abstains(monkeypatch: pytest.MonkeyPatch) -> None:
    """When client is None and GEMINI_API_KEY is unset, claims are marked abstained without raising."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    claims = [{"claim_id": "NO_KEY", "claim_text": "No key claim.", "evidence": "e"}]

    output = audit_claims(claims, client=None)

    assert output["blocked_ids"] == ["NO_KEY"]
    res = output["results"][0]
    assert res["verdict"] == "abstained"
    assert res["blocked"] is True
    assert "batch_abstained" in res["reasons"]


# ---------------------------------------------------------------------------
# Manifest normalization
# ---------------------------------------------------------------------------


def test_manifest_normalization_plain_list_and_dict() -> None:
    """Plain lists of numbers and dicts {"numbers": [...]} normalize identically."""
    claims = [
        {"claim_id": "L1", "claim_text": "Matches 10.0 and 20.0.", "evidence": "e"},
        {"claim_id": "D1", "claim_text": "Matches 10.0 and 20.0.", "evidence": "e"},
    ]
    manifests = {
        "L1": [10.0, 20.0],
        "D1": {"numbers": [10.0, 20.0]},
    }
    client = FakeClient()

    output = audit_claims(claims, client=client, manifests=manifests)

    # Both should pass numeric check and clear gate
    assert output["blocked_ids"] == []
    assert set(output["clear_ids"]) == {"L1", "D1"}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def test_logging_appends_records(tmp_path: Path) -> None:
    """Audit run appends structured decision records to log_path."""
    log_file = tmp_path / "logs" / "audit.jsonl"
    claims = [
        {"claim_id": "LOG_1", "claim_text": "Numeric 5.", "evidence": "e1"},
        {"claim_id": "LOG_2", "claim_text": "Normal.", "evidence": "e2"},
    ]
    manifests = {"LOG_1": [99.0]}
    client = FakeClient()

    output = audit_claims(claims, client=client, manifests=manifests, log_path=log_file)

    assert log_file.is_file()
    lines = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 2
    assert lines[0]["route"] == "n3_claim_auditor"
    assert lines[0]["item_id"] == "LOG_1"
    assert lines[0]["decision"] == "numeric_mismatch"
    assert lines[1]["item_id"] == "LOG_2"
    assert lines[1]["decision"] == "clear"


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_cli_invocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """CLI reads claims, writes verdicts JSON, and prints blocked/clear counts."""
    claims_file = tmp_path / "claims.jsonl"
    claims_file.write_text(
        json.dumps({"claim_id": "CLI_1", "claim_text": "Claim 1.", "evidence": "ev"}) + "\n"
        + json.dumps({"claim_id": "CLI_2", "claim_text": "Bad 10.", "evidence": "ev"}) + "\n",
        encoding="utf-8",
    )
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps({"CLI_2": [50.0]}), encoding="utf-8")
    out_file = tmp_path / "verdicts.json"

    # Avoid live API call in CLI test by keeping GEMINI_API_KEY unset (fails safe to abstained)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    rc = main([
        "--claims", str(claims_file),
        "--manifest", str(manifest_file),
        "--out", str(out_file),
        "--mode", "shadow",
    ])

    assert rc == 0
    assert out_file.is_file()
    verdicts = json.loads(out_file.read_text(encoding="utf-8"))
    assert len(verdicts["results"]) == 2
    assert "CLI_2" in verdicts["blocked_ids"]

    captured = capsys.readouterr()
    assert "Blocked: 2, Clear: 0" in captured.out

