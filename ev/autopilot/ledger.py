"""
ev.autopilot.ledger -- the Phase 0 shadow execution ledger: append-only JSONL files.

One row per evaluated candidate side (the decision record), in the design ruling s7
field shape, shadow-adapted: simulated fills carry status FILLED with "simulated": true,
venue_order_id is always null, and no row can exist for a real order because this package
has no order path.  Gate rejects are rows too (status REJ_* / SKIPPED_* / ANNEX_*), so the
gate funnel is derivable from the ledger itself instead of from a separate counter that
could drift from the record.

Thin-anchor annex: exactly-4-book signals whose fair range exceeds the CLV ruling's
5.0pp guardrail are written to a sibling annex file and excluded from the eligible set
and from every bar (ruling Q2).
"""

from __future__ import annotations

import json
import os


def append_rows(path: str, rows: list) -> int:
    """Append rows as JSONL (created if missing).  Returns the number written."""
    if not rows:
        return 0
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="ascii") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True, default=str) + "\n")
    return len(rows)


def read_rows(path: str) -> list:
    """All rows of a ledger file ([] if it does not exist).  A corrupt line raises:
    a silently dropped row would silently change a bar verdict."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="ascii") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def annex_path_for(ledger_path: str) -> str:
    root, _ext = os.path.splitext(ledger_path)
    return root + ".thin_anchor_annex.jsonl"
