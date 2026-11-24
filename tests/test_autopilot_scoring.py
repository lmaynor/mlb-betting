"""
tests.test_autopilot_scoring -- unit tests for ev.autopilot.scoring_job.
Hermetic tests using tmp_path and shadow_bank DirStore.
"""

from __future__ import annotations

import gzip
import json
import pytest

from ev.autopilot.config import load_config
from ev.autopilot.scoring_job import run_scoring
from ev.novig import shadow_bank as sb


def _make_row(day: str, system: str, sig: str, *, status: str = "FILLED",
              t0_prob: float = 0.55, drift_pp: float = -0.30,
              cons_net: float = 2.60, sb_net: float = 1.00, entry_disloc: float = 5.00,
              ts: str | None = None, disp: float = 0.010, n_books: int = 5,
              med_age: float = 8.0, nov_age: float = 2.0, mins: float = 300.0,
              close_prob="auto") -> dict:
    return {
        "status": status,
        "system": system,
        "game_day": day,
        "signal_id": sig,
        "event_id": "ev1",
        "player_norm": "some player",
        "stat": "sog",
        "side": "yes",
        "line": 2.5,
        "commence": "2026-11-24T00:30:00+00:00",
        "quote_observed_at": ts or f"{day}T17:00:00+00:00",
        "gates_snapshot": {
            "consensus_fair_prob": t0_prob,
            "target_quote_age_min": nov_age,
            "n_books": n_books,
            "median_book_age_min": med_age,
            "mins_to_start": mins,
            "disp_mad": disp,
        },
        "closing_fair_prob": (t0_prob + drift_pp / 100.0) if close_prob == "auto" else close_prob,
        "consensus_confirm_clv_net_pct": cons_net,
        "consensus_confirm_clv_pct": cons_net,
        "same_book_clv_net_pct": sb_net,
        "same_book_clv_pct": sb_net,
        "consensus_drift_pp": drift_pp,
        "entry_dislocation_pct": entry_disloc,
        "moved_quote": False,
        "novig_t15_prob": 0.55,
    }


def _sample_rows() -> list:
    return [
        _make_row("2026-11-16", "NHL_SOG", "sig-1", status="FILLED"),
        _make_row("2026-11-16", "NHL_SOG", "sig-2", status="REJ_EV_LOW"),
        _make_row("2026-11-17", "NHL_SOG", "sig-3", status="FILLED"),
        _make_row("2026-11-17", "NHL_SOG", "sig-4", status="REJ_CONS_WIDE"),
    ]


def _write_ledger(root, rows: list) -> None:
    target = root / "Autopilot/shadow_ledger/latest.jsonl.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows)
    target.write_bytes(gzip.compress(text.encode("utf-8")))


def test_scoring_partial_banks_report_and_json(tmp_path):
    root = tmp_path / "bank"
    rows = _sample_rows()
    _write_ledger(root, rows)
    store = sb.make_store("dir:" + str(root))
    out_dir = tmp_path / "out"

    summary = run_scoring(
        store,
        load_config(),
        from_day="2026-11-16",
        stem="dry-run-test",
        partial=True,
        out_dir=str(out_dir),
    )

    # Local files exist
    assert (out_dir / "dry-run-test.md").exists()
    assert (out_dir / "dry-run-test.json").exists()

    # Banked report starts with PARTIAL banner line
    banked_md = root / "Autopilot/scoring/dry-run-test.md"
    assert banked_md.exists()
    report_text = banked_md.read_text(encoding="utf-8")
    assert report_text.startswith("PARTIAL WINDOW -- DRY RUN -- NOT THE DEC 1 VERDICT")
    assert (
        "This run scores a partial window banked so far; bars that depend on "
        "window span/volume are expected to fail. Do not cite as the verdict."
    ) in report_text
    assert "FINAL SCORING" not in report_text

    # Banked json parses and has the pooled evaluation key (groups["ALL"])
    banked_json = root / "Autopilot/scoring/dry-run-test.json"
    assert banked_json.exists()
    res = json.loads(banked_json.read_text(encoding="utf-8"))
    assert "groups" in res
    assert "ALL" in res["groups"]

    # Summary dict assertions
    assert summary["partial"] is True
    assert summary["ledger_rows"] == len(rows)
    assert summary["filled"] == 2
    assert summary["stem"] == "dry-run-test"
    assert summary["from_day"] == "2026-11-16"
    assert summary["job"] == "autopilot-scoring"
    assert summary["game_days"] == ["2026-11-16", "2026-11-17"]

    # Audit run summary is banked
    run_file = root / f"Autopilot/scoring/runs/{summary['run_id']}.json"
    assert run_file.exists()


def test_scoring_final_label(tmp_path):
    root = tmp_path / "bank"
    rows = _sample_rows()
    _write_ledger(root, rows)
    store = sb.make_store("dir:" + str(root))
    out_dir = tmp_path / "out"

    summary = run_scoring(
        store,
        load_config(),
        from_day="2026-11-16",
        stem="verdict-test",
        partial=False,
        out_dir=str(out_dir),
    )

    banked_md = root / "Autopilot/scoring/verdict-test.md"
    assert banked_md.exists()
    report_text = banked_md.read_text(encoding="utf-8")
    assert "NOT THE DEC 1 VERDICT" not in report_text
    assert "FINAL SCORING" in report_text
    assert "FINAL SCORING of the 2026-11-16..2026-11-30 live window." in report_text
    assert summary["partial"] is False


def test_scoring_missing_ledger_raises(tmp_path):
    root = tmp_path / "bank"
    root.mkdir()
    store = sb.make_store("dir:" + str(root))
    with pytest.raises(Exception):
        run_scoring(
            store,
            load_config(),
            from_day="2026-11-16",
            stem="fail-test",
            partial=True,
            out_dir=str(tmp_path / "out"),
        )
