"""DEC-02 availability fixture integrity (join audit, 2026-11-13).

The status fixture's ``designation_at_decision`` values are derived, not
hand-entered: scripts/derive_status_designations.py re-derives each one
from the banked official injury reports with the repo's decision-time
rule (select_designation, tip - 60) and writes status_derivation.jsonl
beside the fixture. This test pins the fixture to that record offline,
so a fixture edit that changes a designation without re-running the
derivation fails the suite; the script itself fails the fixture build
when run against the source data (exit 1 on any mismatch).

The goalie fixture's starter flags were verified exact against the
boxscore master by the join audit's sample; no derivation record is
kept for it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "decisions" / "availability"
STATUS_FIXTURE = FIXTURE_DIR / "status_items.jsonl"
DERIVATION_RECORD = FIXTURE_DIR / "status_derivation.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_derivation_record_covers_every_status_item():
    items = _load_jsonl(STATUS_FIXTURE)
    records = _load_jsonl(DERIVATION_RECORD)
    assert len(items) == 200
    assert [r["item_id"] for r in records] == [i["item_id"] for i in items]


def test_fixture_designations_match_derivation():
    items = {i["item_id"]: i for i in _load_jsonl(STATUS_FIXTURE)}
    for rec in _load_jsonl(DERIVATION_RECORD):
        item = items[rec["item_id"]]
        assert item["designation_at_decision"] == rec["derived_designation"], rec["item_id"]
        assert item["player"] == rec["player"]
        assert item["game_date"] == rec["game_date"]


def test_derivation_respects_tip_minus_60():
    # The honesty core: the report a designation was read from must be
    # stamped at or before tip - 60 minutes, for every item.
    for rec in _load_jsonl(DERIVATION_RECORD):
        tip = datetime.fromisoformat(rec["tip_ts"])
        report = datetime.fromisoformat(rec["decision_report_ts"])
        assert report <= tip - timedelta(minutes=60), rec["item_id"]
        assert rec["derived_designation"], rec["item_id"]
