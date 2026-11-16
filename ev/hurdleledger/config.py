"""
ev.hurdleledger.config -- ledger parameters (env-overridable, HURDLE_ prefix, the
repo's standing convention).

Defaults are the hurdle exactly as adopted in MISSION.md (Nov 16, 2026):
$500/month combined net, 2 consecutive months, inside a 90-day window that opened
Nov 16, 2026. Source locations default to where the records actually live:
AUT-09's audit dir (~/.ownbettor, or OWNBETTOR_AUDIT_DIR) and this ledger's own
data dir (~/.hurdle-ledger) holding rec-bets.jsonl + subscription-records.jsonl.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields

ENV_PREFIX = "HURDLE_"


def _coerce(raw: str, default):
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


@dataclass(frozen=True)
class LedgerConfig:
    # --- the hurdle (MISSION.md, adopted Nov 16, 2026) --------------------
    target_usd: float = 500.0              # combined net per month that clears
    consecutive_months: int = 2            # months in a row that must clear
    window_start: str = "2026-11-16"       # the day the 90-day window opened
    window_days: int = 90

    # --- sources ------------------------------------------------------------
    kalshi_audit_dir: str = ""             # empty = OWNBETTOR_AUDIT_DIR or ~/.ownbettor
    data_dir: str = ""                     # empty = ~/.hurdle-ledger
    rec_log_path: str = ""                 # empty = <data_dir>/rec-bets.jsonl
    sub_records_path: str = ""             # empty = <data_dir>/subscription-records.jsonl

    @property
    def kalshi_audit_path(self) -> str:
        if self.kalshi_audit_dir:
            return self.kalshi_audit_dir
        ownbettor = os.environ.get("OWNBETTOR_AUDIT_DIR")
        if ownbettor:
            return ownbettor
        return os.path.expanduser("~/.ownbettor")

    @property
    def data_path(self) -> str:
        return self.data_dir or os.path.expanduser("~/.hurdle-ledger")

    @property
    def rec_log(self) -> str:
        return self.rec_log_path or os.path.join(self.data_path, "rec-bets.jsonl")

    @property
    def sub_records(self) -> str:
        return self.sub_records_path or os.path.join(self.data_path, "subscription-records.jsonl")


def load_config(env=None) -> LedgerConfig:
    """LedgerConfig from HURDLE_<FIELD> env vars (e.g. HURDLE_TARGET_USD=500)."""
    env = os.environ if env is None else env
    defaults, kwargs = LedgerConfig(), {}
    for f in fields(LedgerConfig):
        raw = env.get(f"{ENV_PREFIX}{f.name.upper()}")
        if raw not in (None, ""):
            kwargs[f.name] = _coerce(raw, getattr(defaults, f.name))
    return LedgerConfig(**kwargs)
