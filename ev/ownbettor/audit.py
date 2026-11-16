"""
ev.ownbettor.audit -- the proof trail: an append-only JSONL audit log + a small state file.

One JSONL file per ET day (audit-YYYY-MM-DD.jsonl) in the audit dir.  Every step of the
pipeline writes a row with a UTC timestamp:
    signal_received   (with the Kelly stake computed for the signal, gate outcome or not)
    rule_decision     (allowed, or the REJ_* reason -- the stake rides along)
    risk_decision     (caps / kill switch outcome for gate-passing signals)
    order_intended    (ticker, side, contracts, limit price, estimated all-in cost)
    order_result      (what the venue reported -- never what we merely intended)
    order_cancelled
    settlement        (realized P&L booked against a signal; feeds the daily loss stop)
    kill_tripped / kill_reset / error_breaker

Daily aggregates (orders placed, filled spend, realized P&L) are DERIVED by replaying
the day's file, the AUT-01 convention: the funnel can never drift from the record.
The kill switch + consecutive-error counter live in state.json (atomic replace), so a
tripped kill survives a crash/restart and is manual-reset-only, the repo's standing
pattern (mlb.execution.kalshi.risk).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
PLACED_STATUSES = ("FILLED", "PARTIAL", "RESTING")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def et_today() -> str:
    return datetime.now(_ET).date().isoformat()


class AuditLog:
    def __init__(self, audit_dir: str, mode: str = "dry_run"):
        self.dir = audit_dir
        self.mode = mode
        os.makedirs(self.dir, exist_ok=True)
        self._lock = threading.Lock()
        self._state_path = os.path.join(self.dir, "state.json")

    # ---- events ----
    def path_for(self, day: str) -> str:
        return os.path.join(self.dir, f"audit-{day}.jsonl")

    def append(self, event: str, day: str, **fields) -> dict:
        row = {"ts": now_iso(), "day": day, "event": event, "mode": self.mode}
        row.update(fields)
        with self._lock:
            with open(self.path_for(day), "a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        return row

    def events(self, day: str) -> list:
        path = self.path_for(day)
        if not os.path.exists(path):
            return []
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    out.append(json.loads(line))
        return out

    def all_events(self) -> list:
        out = []
        for name in sorted(os.listdir(self.dir)):
            if name.startswith("audit-") and name.endswith(".jsonl"):
                out.extend(self.events(name[len("audit-"):-len(".jsonl")]))
        return out

    # ---- derived daily aggregates ----
    def orders_placed(self, day: str) -> int:
        return sum(1 for e in self.events(day)
                   if e["event"] == "order_result" and e.get("status") in PLACED_STATUSES)

    def spent_today(self, day: str) -> float:
        return round(sum(float(e.get("filled_cost") or 0.0) for e in self.events(day)
                         if e["event"] == "order_result" and e.get("status") in PLACED_STATUSES), 4)

    def realized_pnl_today(self, day: str) -> float:
        return round(sum(float(e.get("pnl_usd") or 0.0) for e in self.events(day)
                         if e["event"] == "settlement"), 4)

    def has_intent(self, signal_id: str) -> bool:
        return any(e["event"] == "order_intended" and e.get("signal_id") == signal_id
                   for e in self.all_events())

    # ---- state (kill switch, error counter) ----
    def _load_state(self) -> dict:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: dict) -> None:
        tmp = self._state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, sort_keys=True)
        os.replace(tmp, self._state_path)

    @property
    def kill(self) -> dict | None:
        return self._load_state().get("kill")

    def set_kill(self, reason: str) -> None:
        state = self._load_state()
        if not state.get("kill"):
            state["kill"] = {"reason": reason, "ts": now_iso()}
            self._save_state(state)

    def clear_kill(self) -> None:
        state = self._load_state()
        state.pop("kill", None)
        state["consecutive_errors"] = 0
        self._save_state(state)

    @property
    def consecutive_errors(self) -> int:
        return int(self._load_state().get("consecutive_errors") or 0)

    def set_consecutive_errors(self, n: int) -> None:
        state = self._load_state()
        state["consecutive_errors"] = n
        self._save_state(state)
