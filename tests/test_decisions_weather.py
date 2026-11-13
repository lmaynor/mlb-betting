"""Offline hermetic tests for ev.decisions_weather and ev.decisions_evals.eval_weather."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import MagicMock

import pytest

from ev.decisions_client import BatchResult
from ev.decisions_evals.eval_weather import evaluate_weather
from ev.decisions_weather import (
    CONFIDENCE_LEVELS,
    CONTRADICTION_THRESHOLD,
    build_cycle_pairs,
    build_input,
    build_questions,
    classify_cycles,
    fetch_products,
    gate,
    main,
    parse_afd_cycles,
    parse_zfp_cycles,
)


class FakeDecisionsClient:
    """Mock Decisions API client that returns canned answers and tracks calls."""

    def __init__(self, canned_result: BatchResult) -> None:
        self.canned_result = canned_result
        self.call_count = 0
        self.last_input: str | None = None
        self.last_questions: dict[str, Any] | None = None

    def call(self, input_text: str, questions: Mapping[str, Any]) -> BatchResult:
        self.call_count += 1
        self.last_input = input_text
        self.last_questions = dict(questions)
        return self.canned_result


# ---------------------------------------------------------------------------
# Gate Actions Tests
# ---------------------------------------------------------------------------

def test_gate_contradiction_pull_quote() -> None:
    """Contradiction at 0.75 with opposing market_direction produces pull_quote."""
    item = {"item_id": "W001"}
    answers = {
        "guidance_shift": {
            "choice": "warmer",
            "probabilities": {"warmer": 0.75, "cooler": 0.10, "steady": 0.15},
        },
        "forecaster_confidence": {"score": 4.0},
    }
    # Opposing market direction
    out = gate(item, answers, market_direction="cooler")
    assert out["item_id"] == "W001"
    assert out["shift"] == "warmer"
    assert out["action"] == "pull_quote"
    assert out["confidence_score"] == 4.0

    # Also test opposite direction: pick cooler with warmer market direction
    answers_cooler = {
        "guidance_shift": {
            "choice": "cooler",
            "probabilities": {"warmer": 0.10, "cooler": 0.80, "steady": 0.10},
        }
    }
    out_cooler = gate(item, answers_cooler, market_direction="warmer")
    assert out_cooler["shift"] == "cooler"
    assert out_cooler["action"] == "pull_quote"


def test_gate_same_direction_widen_quote() -> None:
    """Same-direction or no-market-direction at >= 0.75 produces widen_quote."""
    item = {"item_id": "W002"}
    answers = {
        "guidance_shift": {
            "choice": "warmer",
            "probabilities": {"warmer": 0.80, "cooler": 0.10, "steady": 0.10},
        }
    }
    # Same market direction
    out_same = gate(item, answers, market_direction="warmer")
    assert out_same["action"] == "widen_quote"
    assert out_same["shift"] == "warmer"

    # None market direction also widens if prob >= 0.75
    out_none = gate(item, answers, market_direction=None)
    assert out_none["action"] == "widen_quote"


def test_gate_low_probs_no_action() -> None:
    """Guidance shift with probabilities below threshold results in no_action."""
    item = {"item_id": "W003"}
    answers = {
        "guidance_shift": {
            "choice": "warmer",
            "probabilities": {"warmer": 0.40, "cooler": 0.30, "steady": 0.30},
        }
    }
    out = gate(item, answers, market_direction="cooler")
    assert out["action"] == "no_action"

    # Steady class pick also results in no_action even with high prob
    answers_steady = {
        "guidance_shift": {
            "choice": "steady",
            "probabilities": {"warmer": 0.05, "cooler": 0.05, "steady": 0.90},
        }
    }
    out_steady = gate(item, answers_steady, market_direction="warmer")
    assert out_steady["action"] == "no_action"


def test_gate_abstention_alert_only() -> None:
    """Abstention (missing or empty answers / no probs) results in alert_only."""
    item = {"item_id": "W004"}
    out_empty = gate(item, {})
    assert out_empty["action"] == "alert_only"
    assert out_empty["shift"] is None
    assert out_empty["p_shift"] is None
    assert out_empty["confidence_score"] is None

    out_no_probs = gate(item, {"guidance_shift": {"choice": "warmer"}})
    assert out_no_probs["action"] == "alert_only"


def test_gate_never_outputs_price() -> None:
    """Doctrine: Decisions outputs NEVER contain a price field."""
    item = {"item_id": "W005", "price": 55, "current_quote": 0.45}
    answers = {
        "guidance_shift": {
            "choice": "cooler",
            "probabilities": {"warmer": 0.10, "cooler": 0.85, "steady": 0.05},
        }
    }
    out = gate(item, answers, market_direction="warmer")
    for key in out:
        assert "price" not in key.lower(), f"Forbidden price field found: {key}"


# ---------------------------------------------------------------------------
# Build Input and Questions Tests
# ---------------------------------------------------------------------------

def test_build_input_does_not_contain_new_forecast_high() -> None:
    """build_input must include previous high but NOT the new forecast high."""
    item = {
        "item_id": "W006",
        "target_date": "2026-09-18",
        "forecast_high_prev": 75,
        "forecast_high_new": 99,  # distinct label value
        "new_cycle_issued": "2026-09-17T01:20:00",
        "afd_short_term": "Cooler air arrives behind the cold front.",
    }
    text = build_input(item)
    assert "75F" in text or "75" in text
    assert "2026-09-18" in text
    assert "2026-09-17T01:20:00" in text
    assert "Cooler air arrives" in text
    assert "99" not in text, "Input must NOT contain the new cycle's numeric forecast high"


def test_build_questions_schema() -> None:
    """build_questions must return valid choice and score questions."""
    q = build_questions()
    assert "guidance_shift" in q
    assert q["guidance_shift"]["type"] == "choice"
    assert set(q["guidance_shift"]["criteria"].keys()) == {"warmer", "cooler", "steady"}

    assert "forecaster_confidence" in q
    assert q["forecaster_confidence"]["type"] == "score"
    assert q["forecaster_confidence"]["criteria"] == CONFIDENCE_LEVELS


# ---------------------------------------------------------------------------
# Classify Cycles Tests
# ---------------------------------------------------------------------------

def test_classify_cycles_makes_one_call_and_sets_mode_and_applied_false(tmp_path: Path) -> None:
    """classify_cycles executes exactly ONE ItemBatch call and tags applied: False."""
    items = [
        {"item_id": f"W{i:03d}", "target_date": "2026-09-17", "forecast_high_prev": 75, "forecast_high_new": 72, "new_cycle_issued": "2026-09-16T01:20:00", "afd_short_term": f"discussion {i}"}
        for i in range(1, 4)
    ]
    canned_answers = {
        "i001_guidance_shift": {"choice": "cooler", "probabilities": {"warmer": 0.1, "cooler": 0.8, "steady": 0.1}},
        "i001_forecaster_confidence": {"score": 3.0},
        "i002_guidance_shift": {"choice": "warmer", "probabilities": {"warmer": 0.8, "cooler": 0.1, "steady": 0.1}},
        "i002_forecaster_confidence": {"score": 4.0},
        "i003_guidance_shift": {"choice": "steady", "probabilities": {"warmer": 0.2, "cooler": 0.2, "steady": 0.6}},
        "i003_forecaster_confidence": {"score": 2.0},
    }
    client = FakeDecisionsClient(BatchResult(answers=canned_answers))
    log_path = tmp_path / "decisions.jsonl"

    results = classify_cycles(items, client=client, mode="live", log_path=log_path)
    assert client.call_count == 1
    assert len(results) == 3

    for r in results:
        assert r["mode"] == "live"
        assert r["applied"] is False
        assert "price" not in r

    assert results[0]["shift"] == "cooler"
    assert results[0]["action"] == "widen_quote"
    assert results[1]["shift"] == "warmer"
    assert results[1]["action"] == "widen_quote"
    assert results[2]["shift"] == "steady"
    assert results[2]["action"] == "no_action"

    # Verify log output was written via append_records
    assert log_path.exists()
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 3
    assert lines[0]["route"] == "weather"
    assert lines[0]["applied"] is False


def test_classify_cycles_abstention_produces_alert_only_applied_false() -> None:
    """When client abstains, all items gate with {} ('alert_only') and applied: False."""
    items = [
        {"item_id": "W001", "target_date": "2026-09-17", "forecast_high_prev": 75, "forecast_high_new": 72, "new_cycle_issued": "2026-09-16T01:20:00", "afd_short_term": "text"}
    ]
    client = FakeDecisionsClient(BatchResult.abstain("timeout"))
    results = classify_cycles(items, client=client, mode="shadow")

    assert client.call_count == 1
    assert len(results) == 1
    assert results[0]["action"] == "alert_only"
    assert results[0]["applied"] is False
    assert results[0]["mode"] == "shadow"


# ---------------------------------------------------------------------------
# Backtest Parsers and Builders Tests
# ---------------------------------------------------------------------------

def test_parse_zfp_cycles_bucket_midpoints() -> None:
    """parse_zfp_cycles on small embedded ZFP sample yields bucket midpoints for ILZ104."""
    sample_text = """
FPUS53 KLOT 160036
ZFPLOT

Zone Forecast Product for Northeast Illinois and Northwest Indiana
National Weather Service Chicago/Romeoville IL
736 PM CDT Tue Sep 15 2026

ILZ014-160900-
Cook-
736 PM CDT Tue Sep 15 2026
.TONIGHT...Lows in the 60s.
$$

ILZ104-160900-
Central Cook-
Including the city of Chicago
736 PM CDT Tue Sep 15 2026

.WEDNESDAY...Partly sunny. Highs in the lower 70s.
.THURSDAY...Mostly sunny. Highs in the mid 70s.
$$

FPUS53 KLOT 160120
ZFPLOT

Zone Forecast Product for Northeast Illinois and Northwest Indiana
National Weather Service Chicago/Romeoville IL
820 PM CDT Tue Sep 15 2026

ILZ104-160900-
Central Cook-
Including the city of Chicago
820 PM CDT Tue Sep 15 2026

.WEDNESDAY...Partly sunny. Highs in the upper 70s.
.THURSDAY...Mostly sunny. Highs near 80.
$$
"""
    cycles = parse_zfp_cycles(sample_text)
    assert len(cycles) == 2

    c1 = cycles[0]
    assert c1["issued"] == "2026-09-16T00:36:00"
    # lower 70s -> 72; mid 70s -> 75
    assert c1["highs"]["2026-09-16"] == 72
    assert c1["highs"]["2026-09-17"] == 75

    c2 = cycles[1]
    assert c2["issued"] == "2026-09-16T01:20:00"
    # upper 70s -> 78; near 80 -> 80
    assert c2["highs"]["2026-09-16"] == 78
    assert c2["highs"]["2026-09-17"] == 80


def test_parse_afd_cycles_extracts_excerpt() -> None:
    """parse_afd_cycles extracts and collapses SHORT TERM / DISCUSSION excerpt up to 2200 chars."""
    sample_afd = """
FXUS63 KLOT 160332
AFDLOT

Area Forecast Discussion
National Weather Service Chicago/Romeoville IL
332 AM CDT Wed Sep 16 2026

.SHORT TERM... /Through Tonight/
Issued at 332 AM CDT Wed Sep 16 2026

A parade of convectively   enhanced mid-level impulses
continues to drive waves of convection.
""" + ("Long discussion " * 200) + """
&&

.LONG TERM...
Thursday through Tuesday.
$$
"""
    afds = parse_afd_cycles(sample_afd)
    assert len(afds) == 1
    afd = afds[0]
    assert afd["issued"] == "2026-09-16T03:32:00"
    excerpt = afd["short_term"]
    assert "convectively enhanced" in excerpt  # whitespace collapsed
    assert len(excerpt) <= 2200


def test_build_cycle_pairs_labels_and_sampling() -> None:
    """build_cycle_pairs labels >= +3 warmer, <= -3 cooler, 0 steady, with seeded sampling."""
    zfps = [
        {"issued": "2026-09-15T10:00:00", "highs": {"2026-09-16": 70, "2026-09-17": 72}},
        {"issued": "2026-09-16T10:00:00", "highs": {"2026-09-17": 75, "2026-09-18": 75}},  # 75 - 72 = +3 -> warmer
        {"issued": "2026-09-17T10:00:00", "highs": {"2026-09-18": 75, "2026-09-19": 80}},  # 75 - 75 = 0 -> steady
        {"issued": "2026-09-18T10:00:00", "highs": {"2026-09-19": 76, "2026-09-20": 65}},  # 76 - 80 = -4 -> cooler
    ]
    afds = [
        {"issued": "2026-09-16T10:30:00", "short_term": "AFD discussion text 1"},
        {"issued": "2026-09-17T10:30:00", "short_term": "AFD discussion text 2"},
        {"issued": "2026-09-18T10:30:00", "short_term": "AFD discussion text 3"},
    ]

    pairs = build_cycle_pairs(zfps, afds, max_steady=10)
    assert len(pairs) == 3

    p_warmer = [p for p in pairs if p["label"] == "warmer"][0]
    assert p_warmer["forecast_high_prev"] == 72
    assert p_warmer["forecast_high_new"] == 75
    assert p_warmer["target_date"] == "2026-09-17"

    p_steady = [p for p in pairs if p["label"] == "steady"][0]
    assert p_steady["forecast_high_prev"] == 75
    assert p_steady["forecast_high_new"] == 75
    assert p_steady["target_date"] == "2026-09-18"

    p_cooler = [p for p in pairs if p["label"] == "cooler"][0]
    assert p_cooler["forecast_high_prev"] == 80
    assert p_cooler["forecast_high_new"] == 76
    assert p_cooler["target_date"] == "2026-09-19"


def test_fetch_products_mock_opener() -> None:
    """fetch_products uses correct URL, params, and User-Agent header."""
    mock_opener = MagicMock()
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"Sample NWS product content"
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None
    mock_opener.open.return_value = mock_resp

    res = fetch_products("ZFPLOT", "2026-09-15", "2026-09-17", opener=mock_opener)
    assert res == "Sample NWS product content"
    assert mock_opener.open.call_count == 1
    req = mock_opener.open.call_args[0][0]
    assert "mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py" in req.full_url
    assert "pil=ZFPLOT" in req.full_url
    assert "sdate=2026-09-15" in req.full_url
    assert req.headers["User-agent"] == "Beezy Research (beezy.fyi; research@beezy.fyi)"


# ---------------------------------------------------------------------------
# CLI Tests
# ---------------------------------------------------------------------------

def test_main_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI runs classify_cycles and writes json report."""
    cycles_file = tmp_path / "cycles.jsonl"
    out_file = tmp_path / "report.json"
    cycles_file.write_text(
        json.dumps({
            "item_id": "W001",
            "target_date": "2026-09-17",
            "forecast_high_prev": 75,
            "forecast_high_new": 72,
            "new_cycle_issued": "2026-09-16T01:20:00",
            "afd_short_term": "Cooler discussion",
            "label": "cooler",
        }) + "\n",
        encoding="utf-8",
    )

    fake_client = FakeDecisionsClient(
        BatchResult(answers={"i001_guidance_shift": {"choice": "cooler", "probabilities": {"warmer": 0.1, "cooler": 0.8, "steady": 0.1}}})
    )
    monkeypatch.setattr(
        "ev.decisions_weather.classify_cycles",
        lambda items, **kwargs: classify_cycles(items, client=fake_client, **kwargs),
    )

    exit_code = main(["--cycles", str(cycles_file), "--out", str(out_file), "--mode", "shadow"])
    assert exit_code == 0
    assert out_file.exists()
    report = json.loads(out_file.read_text(encoding="utf-8"))
    assert len(report) == 1
    assert report[0]["item_id"] == "W001"
    assert report[0]["shift"] == "cooler"
    assert report[0]["mode"] == "shadow"


# ---------------------------------------------------------------------------
# Evaluation Module Hermetic Tests
# ---------------------------------------------------------------------------

def test_eval_raises_when_uncached_items_without_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CRITICAL: without GEMINI_API_KEY and with uncached items, eval raises."""
    cycles_file = tmp_path / "cycles.jsonl"
    answers_file = tmp_path / "answers.jsonl"  # empty/non-existent
    results_file = tmp_path / "eval_results.json"

    cycles_file.write_text(
        json.dumps({
            "item_id": "W001",
            "target_date": "2026-09-17",
            "forecast_high_prev": 75,
            "forecast_high_new": 72,
            "new_cycle_issued": "2026-09-16T01:20:00",
            "afd_short_term": "Cooler text",
            "label": "cooler",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not set"):
        evaluate_weather(
            cycles_path=cycles_file,
            answers_path=answers_file,
            results_path=results_file,
        )


def test_eval_scores_cached_answers_correctly(tmp_path: Path) -> None:
    """eval scores cached answers deterministically and computes required metrics."""
    cycles_file = tmp_path / "cycles.jsonl"
    answers_file = tmp_path / "answers.jsonl"
    results_file = tmp_path / "eval_results.json"

    items = [
        {"item_id": "W001", "target_date": "2026-09-17", "forecast_high_prev": 75, "forecast_high_new": 78, "new_cycle_issued": "2026-09-16T01:20:00", "afd_short_term": "Warmer text", "label": "warmer"},
        {"item_id": "W002", "target_date": "2026-09-18", "forecast_high_prev": 75, "forecast_high_new": 72, "new_cycle_issued": "2026-09-17T01:20:00", "afd_short_term": "Cooler text", "label": "cooler"},
        {"item_id": "W003", "target_date": "2026-09-19", "forecast_high_prev": 75, "forecast_high_new": 75, "new_cycle_issued": "2026-09-18T01:20:00", "afd_short_term": "Steady text", "label": "steady"},
    ]
    with open(cycles_file, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")

    answers = [
        {"item_id": "W001", "answers": {"guidance_shift": {"choice": "warmer", "probabilities": {"warmer": 0.8, "cooler": 0.1, "steady": 0.1}}, "forecaster_confidence": {"score": 4.0}}},
        {"item_id": "W002", "answers": {"guidance_shift": {"choice": "cooler", "probabilities": {"warmer": 0.1, "cooler": 0.7, "steady": 0.2}}, "forecaster_confidence": {"score": 3.0}}},
        {"item_id": "W003", "answers": {"guidance_shift": {"choice": "steady", "probabilities": {"warmer": 0.2, "cooler": 0.2, "steady": 0.6}}, "forecaster_confidence": {"score": 2.0}}},
    ]
    with open(answers_file, "w", encoding="utf-8") as f:
        for a in answers:
            f.write(json.dumps(a) + "\n")

    summary = evaluate_weather(
        cycles_path=cycles_file,
        answers_path=answers_file,
        results_path=results_file,
    )
    assert summary["n_total"] == 3
    assert summary["n_shift"] == 2
    assert summary["n_steady"] == 1
    assert summary["shift_recall"] == 1.0
    assert summary["direction_accuracy"] == 1.0
    assert summary["steady_false_alarm_rate"] == 0.0
    assert summary["per_class_confusion_counts"]["warmer"]["warmer"] == 1
    assert summary["per_class_confusion_counts"]["cooler"]["cooler"] == 1
    assert summary["per_class_confusion_counts"]["steady"]["steady"] == 1
    assert summary["mean_confidence_score_by_label"]["warmer"] == 4.0
    assert summary["mean_confidence_score_by_label"]["cooler"] == 3.0
    assert summary["mean_confidence_score_by_label"]["steady"] == 2.0
    assert results_file.exists()
