"""Tests for ev.decisions_correction_sentinel.

Hermetic test suite: zero live Decisions API calls, no network.
Uses mock/spy client implementations to test:
- correction answers (P(correction) 0.9) quarantine
- no_correction 0.9 clears
- missing probabilities quarantine (abstention semantics)
- noul normalization (live {"noul": p} shape reaches prep gate correctly)
- unmatched pair excluded from batch and unflagged
- reinstate eligibility at +48h vs +47h
- applied False in live mode
- abstention quarantines matched pairs
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest

from ev.decisions_client import BatchResult
from ev.decisions_correction_sentinel import (
    announcement_text,
    main,
    reinstate_decision,
    run_sentinel,
    sentinel_items_from_pairs,
)
from ev.decisions_prep.n5_correction_sentinel import (
    Q_CORRECTION,
    Q_KIND,
    Q_NO_CORRECTION,
    settled_row_key,
)

SETTLED_SAMPLE = {
    "game_key": "nhl:2026-11-02:COL@CHI",
    "player_id": 8477476,
    "player": "Artturi Lehkonen",
    "stat": "goals",
    "line": 0.5,
    "actual": 0,
    "settled_ts": "2026-11-03T13:31:05+00:00",
}

CAPTURE_SAMPLE = {
    "game_key": "nhl:2026-11-02:COL@CHI",
    "player_id": 8477476,
    "player": "Artturi Lehkonen",
    "stat": "goals",
    "line": 0.5,
    "actual": 1,
    "value": 1,
    "announcement_text": "Correction: the NHL has revised Artturi Lehkonen's goals for 2026-11-02 from 0 to 1.",
    "announced_date": "2026-11-04T02:00:00Z",
}


class FakeClient:
    """Mock DecisionsClient for offline tests."""

    def __init__(
        self,
        answer_map: Mapping[str, Any] | None = None,
        abstain: bool = False,
        abstention_reason: str = "safety_filter",
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answer_map = dict(answer_map or {})
        self.abstain = abstain
        self.abstention_reason = abstention_reason

    def call(self, input_text: str, questions: Mapping[str, Any]) -> BatchResult:
        self.calls.append({"input_text": input_text, "questions": dict(questions)})
        if self.abstain:
            return BatchResult.abstain(self.abstention_reason)

        answers = {}
        for wire_key in questions:
            if wire_key in self.answer_map:
                answers[wire_key] = self.answer_map[wire_key]
            else:
                qid = wire_key.split("_", 1)[1] if "_" in wire_key else wire_key
                if qid in self.answer_map:
                    answers[wire_key] = self.answer_map[qid]
        return BatchResult(answers=answers, abstained=False)


# ---------------------------------------------------------------------------
# Core Sentinel Tests
# ---------------------------------------------------------------------------

def test_correction_answers_quarantine():
    """P(correction) >= 0.80 causes the sentinel to quarantine."""
    client = FakeClient({
        Q_CORRECTION: {"type": "noul", "noul": 0.90},
        Q_NO_CORRECTION: {"type": "noul", "noul": 0.05},
        Q_KIND: {"type": "choice", "choice": "scoring_change"},
    })
    pairs = [(CAPTURE_SAMPLE, SETTLED_SAMPLE)]
    records = run_sentinel(pairs, client=client)

    assert len(records) == 1
    rec = records[0]
    assert rec["status"] == "quarantined"
    assert rec["quarantined"] is True
    assert rec["applied"] is False
    assert rec["gate"]["action"] == "quarantine"
    assert any("p_correction 0.90 >= 0.8" in r for r in rec["gate"]["reasons"])
    assert rec["quarantined_at"] is not None
    assert rec["eligible_at"] is not None
    assert rec["reinstate_decision"] == "pending"


def test_no_correction_clears():
    """P(no_correction) >= 0.25 and low P(correction) clears the row."""
    client = FakeClient({
        Q_CORRECTION: {"type": "noul", "noul": 0.05},
        Q_NO_CORRECTION: {"type": "noul", "noul": 0.90},
        Q_KIND: {"type": "choice", "choice": "none"},
    })
    pairs = [(CAPTURE_SAMPLE, SETTLED_SAMPLE)]
    records = run_sentinel(pairs, client=client)

    assert len(records) == 1
    rec = records[0]
    assert rec["status"] == "clear"
    assert rec["quarantined"] is False
    assert rec["applied"] is False
    assert rec["gate"]["action"] == "pass"
    assert rec["gate"]["reasons"] == []
    assert rec["quarantined_at"] is None


def test_missing_probabilities_quarantine_abstention_semantics():
    """Empty or missing probabilities trigger quarantine with abstention reasons."""
    # Omit Q_CORRECTION and Q_NO_CORRECTION answers
    client = FakeClient({Q_KIND: {"type": "choice", "choice": "none"}})
    pairs = [(CAPTURE_SAMPLE, SETTLED_SAMPLE)]
    records = run_sentinel(pairs, client=client)

    assert len(records) == 1
    rec = records[0]
    assert rec["status"] == "quarantined"
    assert rec["quarantined"] is True
    assert rec["gate"]["action"] == "quarantine"
    assert rec["gate"]["reasons"] == ["abstention"]


def test_noul_normalization():
    """Live Decisions API answers {"noul": p} are normalized to bare floats for prep gate."""
    # Live API response structure
    client = FakeClient({
        Q_CORRECTION: {"type": "noul", "noul": 0.92, "confidence": 0.95},
        Q_NO_CORRECTION: {"type": "noul", "noul": 0.08, "confidence": 0.95},
        Q_KIND: {"type": "choice", "choice": "stat_revision"},
    })
    pairs = [(CAPTURE_SAMPLE, SETTLED_SAMPLE)]
    records = run_sentinel(pairs, client=client)

    rec = records[0]
    assert rec["status"] == "quarantined"
    # If normalization had failed, reasons would be ["abstention"]
    assert any("p_correction 0.92 >= 0.8" in r for r in rec["gate"]["reasons"])

    # Clear case with live shape
    client_clear = FakeClient({
        Q_CORRECTION: {"type": "noul", "noul": 0.02},
        Q_NO_CORRECTION: {"type": "noul", "noul": 0.98},
    })
    records_clear = run_sentinel(pairs, client=client_clear)
    assert records_clear[0]["status"] == "clear"


def test_unmatched_pair_excluded_and_unflagged():
    """Unmatched settled row (settled=None) is excluded from batch and not quarantined."""
    client = FakeClient({Q_CORRECTION: 0.95, Q_NO_CORRECTION: 0.01})
    pairs = [
        (CAPTURE_SAMPLE, None),  # Unmatched
        (CAPTURE_SAMPLE, SETTLED_SAMPLE),  # Matched
    ]
    records = run_sentinel(pairs, client=client)

    assert len(records) == 2
    # Unmatched record
    unmatched = records[0]
    assert unmatched["status"] == "unmatched"
    assert unmatched["quarantined"] is False
    assert unmatched["gate"] is None

    # Matched record
    matched = records[1]
    assert matched["status"] == "quarantined"
    assert matched["quarantined"] is True

    # Check client call: exactly one item was added to the batch
    assert len(client.calls) == 1
    input_text = client.calls[0]["input_text"]
    assert "[i001]" in input_text
    assert "[i002]" not in input_text


def test_reinstate_eligibility_48h_vs_47h():
    """Check 48-hour auto-reinstate rule at +47h vs +48h."""
    t0 = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    now_47h = t0 + timedelta(hours=47)
    now_48h = t0 + timedelta(hours=48)
    now_49h = t0 + timedelta(hours=49)

    client = FakeClient({Q_CORRECTION: 0.90, Q_NO_CORRECTION: 0.10})
    pairs = [(CAPTURE_SAMPLE, SETTLED_SAMPLE)]

    # Quarantined at t0
    records = run_sentinel(pairs, client=client, now=t0)
    rec = records[0]

    assert rec["quarantined_at"] == t0.isoformat()
    assert rec["eligible_at"] == (t0 + timedelta(hours=48)).isoformat()
    assert rec["reinstate_decision"] == "pending"

    # Evaluated at +47h: still pending
    assert reinstate_decision(rec, now=now_47h) == "pending"

    # Evaluated at +48h: eligible to reinstate
    assert reinstate_decision(rec, now=now_48h) == "reinstate"

    # Evaluated at +49h: eligible to reinstate
    assert reinstate_decision(rec, now=now_49h) == "reinstate"

    # If correction materialized: routes to human
    rec_corr = dict(rec, correction_materialized=True)
    assert reinstate_decision(rec_corr, now=now_48h) == "route_to_human"


def test_applied_false_in_live_mode():
    """In live mode, applied is strictly False (shadow-only sentinel doctrine)."""
    client = FakeClient({Q_CORRECTION: 0.90, Q_NO_CORRECTION: 0.05})
    pairs = [(CAPTURE_SAMPLE, SETTLED_SAMPLE)]
    records = run_sentinel(pairs, client=client, mode="live")

    assert records[0]["mode"] == "live"
    assert records[0]["applied"] is False


def test_abstention_quarantines_matched_pairs():
    """Client abstention or failure quarantines all matched pairs fail-closed."""
    client = FakeClient(abstain=True, abstention_reason="rate_limit")
    pairs = [(CAPTURE_SAMPLE, SETTLED_SAMPLE)]
    records = run_sentinel(pairs, client=client)

    assert len(records) == 1
    assert records[0]["status"] == "quarantined"
    assert records[0]["quarantined"] is True
    assert records[0]["gate"]["reasons"] == ["batch_abstained"]


def test_client_construction_failure_quarantines(monkeypatch):
    """When client is None and GEMINI_API_KEY is unset, client construction fails closed."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    pairs = [(CAPTURE_SAMPLE, SETTLED_SAMPLE)]
    records = run_sentinel(pairs, client=None)

    assert len(records) == 1
    assert records[0]["status"] == "quarantined"
    assert records[0]["quarantined"] is True
    assert records[0]["gate"]["reasons"] == ["batch_abstained"]


# ---------------------------------------------------------------------------
# Announcement and Pairing Helper Tests
# ---------------------------------------------------------------------------

def test_announcement_text():
    item_bool = {
        "league": "NHL",
        "game_date": "2025-10-21",
        "player": "Drake Batherson",
        "team": "Ottawa Senators",
        "stat": "assists",
        "value_before": 0,
        "value_after": 1,
        "prose_announced": True,
    }
    expected = (
        "Official stat correction issued by the NHL stats authority: the official "
        "box score for Drake Batherson (Ottawa Senators) for the game on 2025-10-21 "
        "has been corrected. Drake Batherson's assists total for that game is "
        "revised from 0 to 1. The originally published assists figure of 0 is "
        "superseded; 1 is the official corrected total."
    )
    assert announcement_text(item_bool) == expected

    item_str = dict(item_bool, prose_announced="Official NHL scoring announcement.")
    assert announcement_text(item_str) == f"{expected} Official NHL scoring announcement."


def test_sentinel_items_from_pairs_fixture():
    fixture_dir = Path(__file__).parent / "fixtures" / "decisions" / "n5_corrections"
    items_path = fixture_dir / "items.jsonl"
    if items_path.exists():
        raw_items = [json.loads(line) for line in items_path.read_text().splitlines() if line.strip()]
        pairs = sentinel_items_from_pairs(raw_items)
        assert len(pairs) == 25
        for c, s in pairs:
            assert settled_row_key(c) == settled_row_key(s)
            assert c["announcement_text"] != ""
            assert c["actual"] == c["value"]


# ---------------------------------------------------------------------------
# CLI Test
# ---------------------------------------------------------------------------

def test_cli_execution(tmp_path, monkeypatch):
    pairs_file = tmp_path / "pairs.jsonl"
    out_file = tmp_path / "flags.json"
    log_file = tmp_path / "log.jsonl"

    pairs_content = [
        {"capture_row": CAPTURE_SAMPLE, "settled_row": SETTLED_SAMPLE},
        {"capture_row": CAPTURE_SAMPLE, "settled_row": None},
    ]
    pairs_file.write_text("\n".join(json.dumps(p) for p in pairs_content))

    # Mock client within run_sentinel
    fake_client = FakeClient({Q_CORRECTION: 0.90, Q_NO_CORRECTION: 0.05})
    monkeypatch.setattr(
        "ev.decisions_correction_sentinel.DecisionsClient",
        lambda: fake_client,
    )

    exit_code = main(["--pairs", str(pairs_file), "--out", str(out_file), "--log", str(log_file)])
    assert exit_code == 0
    assert out_file.exists()
    assert log_file.exists()

    output_records = json.loads(out_file.read_text())
    assert len(output_records) == 2
    assert output_records[0]["status"] == "quarantined"
    assert output_records[1]["status"] == "unmatched"


# ---------------------------------------------------------------------------
# Evaluation Harness Tests
# ---------------------------------------------------------------------------

def test_eval_dataset_loading():
    """Verify loading positives (25 items) and controls (12 clean rows)."""
    from ev.decisions_evals.eval_corrections import load_controls, load_positives

    positives = load_positives()
    assert len(positives) == 25
    leagues = {p["league"] for p in positives}
    assert leagues == {"NHL", "NFL", "NBA", "MLB"}

    controls = load_controls()
    assert len(controls) == 12
    for c in controls:
        c_row, s_row = c["pair"]
        assert c_row["announcement_text"] == ""
        assert settled_row_key(c_row) == settled_row_key(s_row)


def test_eval_uncached_without_api_key_raises(monkeypatch, tmp_path):
    """Without GEMINI_API_KEY and with uncached items, evaluation raises RuntimeError."""
    from ev.decisions_evals.eval_corrections import evaluate_corrections

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cache_path = tmp_path / "empty_answers.jsonl"
    out_path = tmp_path / "results.json"

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not set"):
        evaluate_corrections(cache_path=cache_path, out_path=out_path)


def test_eval_scoring_with_cached_answers(tmp_path):
    """Evaluate scoring with simulated cached answers: verify recall, false-quarantine rate, and per-league recall."""
    from ev.decisions_evals.common import AnswersCache
    from ev.decisions_evals.eval_corrections import (
        CachedDecisionsClient,
        evaluate_corrections,
        load_controls,
        load_positives,
    )

    cache_path = tmp_path / "answers.jsonl"
    out_path = tmp_path / "eval_results.json"
    cache = AnswersCache(cache_path)

    positives = load_positives()
    controls = load_controls()

    # Seed answers: 24 of 25 positives flagged (96% recall >= 90% bar)
    # 0 of 12 controls flagged (0% false quarantine rate)
    for idx, pos in enumerate(positives):
        if idx == 0:
            # Plant 1 false negative
            cache.store(pos["key"], {Q_CORRECTION: {"noul": 0.05}, Q_NO_CORRECTION: {"noul": 0.95}})
        else:
            cache.store(pos["key"], {Q_CORRECTION: {"noul": 0.95}, Q_NO_CORRECTION: {"noul": 0.05}})

    for ctrl in controls:
        cache.store(ctrl["key"], {Q_CORRECTION: {"noul": 0.02}, Q_NO_CORRECTION: {"noul": 0.98}})

    results = evaluate_corrections(cache_path=cache_path, out_path=out_path)

    assert out_path.exists()
    assert results["summary"]["positive_items"] == 25
    assert results["summary"]["control_items"] == 12
    assert results["metrics"]["correction_recall"] == round(24 / 25, 4)
    assert results["metrics"]["meets_recall_bar"] is True
    assert results["metrics"]["control_false_quarantine_rate"] == 0.0
    assert "NHL" in results["per_league"]
    assert "NFL" in results["per_league"]
    assert "NBA" in results["per_league"]
    assert "MLB" in results["per_league"]

