"""
ev.novig.maker_bank -- durable, append-only study bank for the Novig maker falsification study (NOV-03).

Chunks are banked under:
    Novig/maker_study/{ET day}/{run_id}_{sha8}.jsonl.gz

Kinds:
  * "quote_state": evolving record per order; loader keeps LAST occurrence per order_id.
  * "run": summary record per runner firing.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from ev.novig.shadow_bank import et_day

log = logging.getLogger("ev.novig.maker_bank")

PREFIX = "Novig/maker_study"
SCHEMA = 1
_CHUNK_RE = re.compile(rf"^{re.escape(PREFIX)}/(\d{{4}}-\d{{2}}-\d{{2}})/[^/]+\.jsonl\.gz$")
_seq_counter = 0


def new_run_id(now: datetime | None = None) -> str:
    global _seq_counter
    _seq_counter += 1
    base = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
    return f"{base}-{_seq_counter:04d}"

DEFAULT_QUOTE_STATE: dict[str, Any] = {
    "order_id": None,
    "client_id": None,
    "intent_id": None,
    "game_key": None,
    "game_day": None,
    "sport": None,
    "stat": None,
    "player_norm": None,
    "player": None,
    "line": None,
    "side": None,
    "t_post": None,
    "t_end": None,
    "quote_price": None,
    "contracts_units": None,
    "qty": None,
    "novig_bid_at_post": None,
    "novig_ask_at_post": None,
    "novig_spread_at_post": None,
    "p_cons_at_post": None,
    "p_blend_at_post": None,
    "p_cons_close": None,
    "terminal_status": "RESTING",
    "cancel_reason": None,
    "fill_qty_units": 0.0,
    "p_fill": None,
    "t_fill": None,
    "p_cons_fill_plus_5m": None,
    "p_cons_fill_plus_30m": None,
    "p_novig_close": None,
    "in_news_window": False,
    "fair_floor": None,
    "delta": None,
    "commence": None,
    "sharp_at_post": {},
}


def new_quote_state(**kw) -> dict[str, Any]:
    """Create a new quote state dict with all defaults populated."""
    rec = dict(DEFAULT_QUOTE_STATE)
    rec.update(kw)
    return rec


def _encode_chunk(run_id: str, day: str, rows: list[tuple[str, dict]]) -> tuple[str, bytes]:
    """-> (object_key, gzip_bytes) for rows grouped into one chunk."""
    lines = [json.dumps({"hdr": {"schema": SCHEMA, "run_id": run_id, "day": day, "rows": len(rows)}}, sort_keys=True)]
    lines += [json.dumps({"t": kind, "r": rec}, sort_keys=True, default=str) for kind, rec in rows]
    raw = ("\n".join(lines) + "\n").encode("ascii", "backslashreplace")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(raw)
    sha8 = hashlib.sha1(raw).hexdigest()[:8]
    key = f"{PREFIX}/{day}/{run_id}_{sha8}.jsonl.gz"
    return key, buf.getvalue()


def bank_rows(store, rows: list[tuple[str, dict]], run_id: str | None = None, now: datetime | None = None) -> dict | None:
    """Write rows into a single create-only chunk under Novig/maker_study.

    Returns {'key', 'rows', 'created'} or None when rows is empty.
    """
    if not rows:
        return None
    run_id = run_id or new_run_id(now)
    day = et_day(now)
    key, blob = _encode_chunk(run_id, day, rows)
    created = store.write_new(key, blob)
    return {"key": key, "rows": len(rows), "created": created}


def chunk_keys(store) -> list[str]:
    """All chunk object keys under PREFIX, in key order."""
    all_keys = store.list(f"{PREFIX}/")
    matched = [k for k in all_keys if _CHUNK_RE.match(k)]
    return sorted(matched, key=lambda k: (k.split("/")[-2], k.split("/")[-1]))


def read_chunk(store, key: str) -> list[tuple[str, dict]]:
    """[(kind, record)] of one chunk (header skipped)."""
    raw = gzip.decompress(store.read(key)).decode("ascii", "backslashreplace")
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if "hdr" in obj:
            continue
        out.append((obj["t"], obj["r"]))
    return out


def load_quote_states(store) -> dict[str, dict]:
    """Read all chunks under PREFIX in key order; apply quote_state rows in order (last wins).

    Returns {order_id: record}. If order_id is missing, falls back to client_id.
    """
    states: dict[str, dict] = {}
    for k in chunk_keys(store):
        for kind, rec in read_chunk(store, k):
            if kind == "quote_state":
                oid = rec.get("order_id") or rec.get("client_id")
                if oid:
                    states[oid] = rec
    return states


def load_run_summaries(store) -> list[dict]:
    """List of run records in key order across all chunks."""
    runs: list[dict] = []
    for k in chunk_keys(store):
        for kind, rec in read_chunk(store, k):
            if kind == "run":
                runs.append(rec)
    return runs
