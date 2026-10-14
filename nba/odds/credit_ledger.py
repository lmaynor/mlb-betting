"""Per-sport ParlayAPI credit ledger + pace guard for the odds accumulators.

ParlayAPI bills ONE shared monthly pool (20k on the current plan) across every caller. The 2026-08-27 incident
(two never-retired accumulator jobs drained the pool outside the MLB snapshot ledger) is the reason this exists:
every accumulator call goes through allowance() BEFORE spending and record() AFTER, and a hard floor on the
pool's own `x-requests-remaining` header stops everything regardless of what any ledger says.

Ledger format is identical to mlb.runners.snapshot_odds' tally so the two are interchangeable:
  OddsAccum/{sport}/_credits/{YYYY-MM}.json  ->  {"month": "2026-10", "credits": 1234}

Read-modify-write without locking: one scheduler job per (sport, kind) at a time, jobs never overlap
(task-timeout < cadence), so a lost update is not a realistic concern; an unreadable ledger FAILS CLOSED
(spend nothing) rather than open -- the opposite of a cosmetic counter, because it is the spend guard.
"""
import calendar
import json
import logging
import os
from dataclasses import dataclass

from mlb_core import storage
from nba.config import ODDS_BANK_DEFAULT_BUDGET, ODDSACCUM_PREFIX, PARLAY_RESERVE_FLOOR

logger = logging.getLogger(__name__)


def ledger_key(sport: str, month: str) -> str:
    return f"{ODDSACCUM_PREFIX}/{sport}/_credits/{month}.json"


def monthly_budget(sport: str) -> int:
    """Env ODDS_BANK_BUDGET_<SPORTKEY> (e.g. ODDS_BANK_BUDGET_ICEHOCKEY_NHL) wins over the config default."""
    env = os.environ.get(f"ODDS_BANK_BUDGET_{sport.upper()}")
    if env:
        return int(env)
    return int(ODDS_BANK_DEFAULT_BUDGET.get(sport, 0))


def read_spent(sport: str, month: str) -> int:
    """Credits recorded this month. Raises on an unreadable ledger (caller must fail closed)."""
    key = ledger_key(sport, month)
    if not storage.exists(key):
        return 0
    d = json.loads(storage.read_bytes(key))
    return int(d.get("credits", 0)) if d.get("month") == month else 0


def record(sport: str, month: str, n: int) -> None:
    if n <= 0:
        return
    try:
        total = read_spent(sport, month) + int(n)
        storage.write_bytes(json.dumps({"month": month, "credits": total}).encode(), ledger_key(sport, month))
    except Exception as e:                                                                  # noqa: BLE001
        logger.error("credit ledger write FAILED for %s %s (+%s credits not recorded): %r", sport, month, n, e)


def pace_cap(budget: int, day: int, year: int, month: int) -> float:
    """Linear allowance: budget * day / days_in_month, so cumulative spend tracks a straight line to the budget."""
    dim = calendar.monthrange(year, month)[1]
    return budget * min(max(day, 1), dim) / dim


@dataclass(frozen=True)
class Allowance:
    ok: bool
    credits: int          # how many credits this run may spend (0 when not ok)
    reason: str


def allowance(sport: str, want: int, today, remaining_hint=None, run_cap: int = 0) -> Allowance:
    """How many of `want` credits may be spent now. Partial grants are allowed (the caller spends them on the
    soonest-starting events first). Denied when: budget unset, ledger unreadable, pool below the reserve floor,
    or the month is already ahead of the linear pace."""
    month = f"{today.year:04d}-{today.month:02d}"
    budget = monthly_budget(sport)
    if budget <= 0:
        return Allowance(False, 0, f"no budget configured for {sport}")
    try:
        spent = read_spent(sport, month)
    except Exception as e:                                                                  # noqa: BLE001
        return Allowance(False, 0, f"ledger unreadable ({e!r}); failing closed")
    headroom = None
    if remaining_hint is not None:
        try:
            headroom = int(remaining_hint) - PARLAY_RESERVE_FLOOR
        except (TypeError, ValueError):
            headroom = None
    if headroom is not None and headroom <= 0:
        return Allowance(False, 0, f"shared pool {remaining_hint} is at/below floor {PARLAY_RESERVE_FLOOR}")
    cap = pace_cap(budget, today.day, today.year, today.month)
    room = int(cap - spent)
    if room <= 0:
        return Allowance(False, 0, f"over pace: spent {spent} >= {cap:.0f} allowed so far (budget {budget})")
    grant = min(want, room, run_cap) if run_cap and run_cap > 0 else min(want, room)
    if headroom is not None:
        grant = min(grant, headroom)
    if grant <= 0:
        return Allowance(False, 0, "no room after floor/run-cap")
    return Allowance(True, grant, "partial" if grant < want else "ok")
