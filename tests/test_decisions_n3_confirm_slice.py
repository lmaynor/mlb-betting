"""Offline unit tests for the N3 Stage 2 confirm slice builder.

Deterministic: builds tiny artifacts in tmp_path and checks the window
selection, table expansion, and full-chain fallback rules from
RUL-20261113-dec-03-contradiction.
"""

from __future__ import annotations

from pathlib import Path

from ev.decisions_prep.n3_confirm_slice import build_confirm_slice


def _packaged(tmp_path: Path, files: dict[str, str]) -> dict:
    attached = []
    for pointer, content in files.items():
        p = tmp_path / pointer
        p.write_text(content, encoding="utf-8")
        attached.append(
            {"pointer": pointer, "resolved_path": str(p), "chars": len(content), "truncated": False}
        )
    return {"attached": attached, "evidence_text": "FULL CHAIN FALLBACK TEXT"}


def test_primary_full_and_secondary_windowed(tmp_path: Path) -> None:
    primary = "Primary doc line one.\nThe fee is $0 for pregame.\nLast line."
    secondary = "\n".join(
        [
            "unrelated alpha",
            "unrelated beta",
            "unrelated gamma",
            "unrelated delta",
            "The fee is $0 for pregame.",  # shares the claim's $0 / fee tokens
            "unrelated zeta",
            "unrelated eta",
            "unrelated theta",
            "unrelated iota",
            "far away 999 marker",
        ]
    )
    packaged = _packaged(tmp_path, {"primary.md": primary, "secondary.md": secondary})
    claim = {"claim_id": "T1", "claim_text": "The fee is $0 for pregame.", "evidence": ""}

    out = build_confirm_slice(claim, packaged)

    assert out["fallback_full_chain"] is False
    assert out["primary_pointer"] == "primary.md"
    assert "Primary doc line one." in out["slice_text"]
    assert "Last line." in out["slice_text"]  # primary in full
    assert out["window_counts"] == {"secondary.md": 1}
    # Window is the matching line +/- 3 lines, not the whole document.
    assert "unrelated beta" in out["slice_text"]
    assert "unrelated theta" in out["slice_text"]
    assert "far away 999 marker" not in out["slice_text"]


def test_no_secondary_match_falls_back_to_full_chain(tmp_path: Path) -> None:
    packaged = _packaged(
        tmp_path,
        {"primary.md": "alpha beta", "secondary.md": "nothing shared at all"},
    )
    claim = {"claim_id": "T2", "claim_text": "Zebra xylophone 4242 unique.", "evidence": ""}

    out = build_confirm_slice(claim, packaged)

    assert out["fallback_full_chain"] is True
    assert out["slice_text"] == "FULL CHAIN FALLBACK TEXT"
    assert out["window_counts"] == {}


def test_table_row_match_expands_to_full_table(tmp_path: Path) -> None:
    secondary = "\n".join(
        [
            "intro line",
            "| Stat | All | Contested |",
            "| --- | --- | --- |",
            "| Receptions | 1,475 | 84.3% |",
            "| Passing TDs | 187 | 90.5% |",
            "outro line far below",
            "x",
            "y",
            "z",
            "w",
        ]
    )
    packaged = _packaged(tmp_path, {"primary.md": "p", "table.md": secondary})
    claim = {"claim_id": "T3", "claim_text": "Contested receptions coverage is 84.3%.", "evidence": ""}

    out = build_confirm_slice(claim, packaged)

    assert out["fallback_full_chain"] is False
    # The whole table travels with the matched row, including the sibling row.
    assert "| Passing TDs | 187 | 90.5% |" in out["slice_text"]
    assert "| Stat | All | Contested |" in out["slice_text"]


def test_no_attached_artifacts_falls_back(tmp_path: Path) -> None:
    claim = {"claim_id": "T4", "claim_text": "Anything.", "evidence": ""}
    out = build_confirm_slice(claim, {"attached": [], "evidence_text": "CHAIN"})
    assert out["fallback_full_chain"] is True
    assert out["slice_text"] == "CHAIN"
    assert out["primary_pointer"] is None
