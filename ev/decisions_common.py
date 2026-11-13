"""Shared plumbing for Decisions routes: operating mode and decision logging.

DOCTRINE:
- Routes default to 'shadow' mode to observe and log decisions without taking
  autonomous downstream action.
- Unknown, blank, or malformed mode values fail safe to 'shadow'.
- Decision records capture structured, timestamped evidence (including route,
  item_id, decision, mode, applied state, and custom route fields) into JSONL
  logs for verification, audit, and offline grading.
- Input token budget guarding ensures requests remain well within the 262,144
  token Decisions API context limit.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Union

ROUTE_MODES: tuple[str, ...] = ("shadow", "live")
DEFAULT_MODE: str = "shadow"
MAX_INPUT_TOKENS: int = 240_000


def route_mode(env_var: str = "DECISIONS_ROUTE_MODE") -> str:
    """Read and validate the route operating mode from the environment.

    Strips whitespace and converts to lowercase. If the value is not in
    ROUTE_MODES (including if unset or empty), fails safe to DEFAULT_MODE
    ('shadow').
    """
    raw = os.environ.get(env_var, "")
    mode = raw.strip().lower()
    return mode if mode in ROUTE_MODES else DEFAULT_MODE


def estimate_tokens(text: str) -> int:
    """Conservatively estimate token count for input text.

    Returns 0 for an empty string. For non-empty strings, returns
    max(1, ceil(len(text) / 3.5)).
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 3.5))


def make_record(
    route: str,
    item_id: str,
    decision: Any,
    mode: str,
    applied: bool,
    **fields: Any,
) -> dict[str, Any]:
    """Construct a structured decision record dictionary.

    Format:
        {
            "ts": "<ISO 8601 UTC timestamp>",
            "route": route,
            "item_id": item_id,
            "mode": mode,
            "applied": bool(applied),
            "decision": decision,
            **fields,
        }
    """
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "route": route,
        "item_id": item_id,
        "mode": mode,
        "applied": bool(applied),
        "decision": decision,
    }
    record.update(fields)
    return record


def append_records(
    path: Union[str, os.PathLike[str]],
    records: Iterable[Mapping[str, Any]],
) -> int:
    """Append decision records as JSON Lines to the specified file.

    Creates parent directories if they do not exist. Returns the number
    of records written.
    """
    fpath = os.fspath(path)
    parent = os.path.dirname(fpath)
    if parent:
        os.makedirs(parent, exist_ok=True)
    count = 0
    with open(fpath, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
            count += 1
    return count


__all__ = [
    "ROUTE_MODES",
    "DEFAULT_MODE",
    "MAX_INPUT_TOKENS",
    "route_mode",
    "estimate_tokens",
    "make_record",
    "append_records",
]
