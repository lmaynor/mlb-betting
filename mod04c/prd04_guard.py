"""PRD-04 HARD guardrail for NBA assists scoring.

Eligibility rule (`evaluate_guard`):
  A row is eligible (`"scored"`) ONLY if:
    1. A pre-game injury snapshot timestamp exists (`snapshot_ts_utc`),
    2. `snapshot_ts_utc <= lock_utc`,
    3. `(lock_utc - snapshot_ts_utc) <= max_age_minutes` (default 60 min), AND
    4. `unresolved_statuses` is empty (zero unresolved Questionable/Doubtful
       statuses on the player or any teammate).

Excluded rows are stamped with `guard_status="excluded"` and `guard_reason`, and
kept in audit output while NEVER carrying live/shadow probabilities (`p_model` /
`p_served` are nulled out by `scrub_excluded_probabilities` and `shadow.py`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import pandas as pd

UNRESOLVED_STATUS_TOKENS = frozenset({"questionable", "doubtful", "day-to-day", "gtd"})


@dataclass(frozen=True)
class GuardResult:
    eligible: bool
    reason: str


def _parse_utc(ts) -> Optional[pd.Timestamp]:
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return None
    if isinstance(ts, str) and not ts.strip():
        return None
    try:
        parsed = pd.Timestamp(ts)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def is_unresolved_status(status: object) -> bool:
    """Return True if an injury status string represents an unresolved designation."""
    if status is None or (isinstance(status, float) and pd.isna(status)):
        return False
    s = str(status).strip().lower()
    if not s:
        return False
    return s in UNRESOLVED_STATUS_TOKENS or s.startswith("questionable") or s.startswith("doubtful")


def evaluate_guard(
    snapshot_ts_utc,
    lock_utc,
    unresolved_statuses: Optional[Iterable[object]],
    max_age_minutes: float = 60.0,
) -> GuardResult:
    """Evaluate PRD-04 eligibility for a single scoring row."""
    snap = _parse_utc(snapshot_ts_utc)
    if snap is None:
        return GuardResult(eligible=False, reason="missing_snapshot")

    lock = _parse_utc(lock_utc)
    if lock is None:
        return GuardResult(eligible=False, reason="missing_lock_utc")

    if snap > lock:
        return GuardResult(eligible=False, reason="snapshot_after_lock")

    age_min = (lock - snap).total_seconds() / 60.0
    if age_min > float(max_age_minutes):
        return GuardResult(
            eligible=False,
            reason=f"stale_snapshot ({age_min:.1f}m > {float(max_age_minutes):.0f}m)",
        )

    if unresolved_statuses is None:
        return GuardResult(eligible=False, reason="unresolved_statuses_unknown")

    unresolved = [s for s in unresolved_statuses if s is not None and str(s).strip()]
    if unresolved:
        return GuardResult(
            eligible=False,
            reason=f"unresolved_status ({len(unresolved)}: {unresolved[0]})",
        )

    return GuardResult(eligible=True, reason="ok")


def apply_guard(
    rows,
    *,
    snapshot_ts_utc=None,
    lock_utc=None,
    unresolved_statuses: Optional[Iterable[object]] = None,
    max_age_minutes: float = 60.0,
    scrub_probs: bool = True,
):
    """Stamp each row dict or DataFrame with `guard_status` and `guard_reason`.

    Per-row keys/columns (`snapshot_ts_utc`, `lock_utc`, `unresolved_statuses`)
    override the function-level defaults when present. When `scrub_probs=True`,
    excluded rows have `p_model` and `p_served` cleared to None/NaN so they are
    never silently scored while staying visible in audit output.
    """
    is_df = isinstance(rows, pd.DataFrame)
    records = rows.to_dict("records") if is_df else [dict(r) for r in rows]

    stamped = []
    for rec in records:
        snap = rec.get("snapshot_ts_utc", snapshot_ts_utc)
        lock = rec.get("lock_utc", lock_utc)
        unres = rec.get("unresolved_statuses", unresolved_statuses)
        res = evaluate_guard(snap, lock, unres, max_age_minutes=max_age_minutes)
        rec["guard_status"] = "scored" if res.eligible else "excluded"
        rec["guard_reason"] = res.reason
        if scrub_probs and not res.eligible:
            for col in ("p_model", "p_served"):
                if col in rec:
                    rec[col] = None
        stamped.append(rec)

    if is_df:
        if not stamped:
            out = rows.copy()
            out["guard_status"] = pd.Series(dtype="object")
            out["guard_reason"] = pd.Series(dtype="object")
            return out
        return pd.DataFrame(stamped, index=rows.index)
    return stamped
