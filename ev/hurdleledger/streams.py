"""
ev.hurdleledger.streams -- the three source readers. Each returns monthly nets
computed from source records plus plain-language notes explaining any zero.

Conventions shared by all three streams:
  * A month is a calendar month in America/New_York (the repo's bookkeeping
    timezone; AUT-09's audit days are ET days).
  * Profit counts in the month it SETTLES (cash reality), never the month the
    bet was placed.
  * A missing source is a true zero with a note, never an error: pre-launch
    streams are supposed to read $0.

Stream 1 (Kalshi): parses AUT-09's append-only audit files (audit-YYYY-MM-DD.jsonl)
directly from disk -- no import of ev.ownbettor, so the ledger can never disturb
the bettor. Only settlement rows with mode == "live" count toward the hurdle;
every other settlement is paper and is totalled separately, for reporting only.

Stream 3 (subscriptions): rows in subscription-records.jsonl, one cash event per
row: {"date": "YYYY-MM-DD", "amount_usd": 39.00, "kind": "payment"} -- refunds are
rows with a negative amount_usd. The sign carries the direction; the reader just
nets. This is the drop-in format for a Stripe payments export (Stripe is the
site's billing source of truth; nothing in the repo banks revenue rows today).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def et_month(ts_iso: str) -> str | None:
    """YYYY-MM (ET) for an ISO timestamp, or None if unparseable."""
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_ET).strftime("%Y-%m")


def _month_of_day(day: str) -> str | None:
    if isinstance(day, str) and len(day) == 10 and day[4] == "-" and day[7] == "-":
        return day[:7]
    return None


def _read_jsonl(path: str) -> tuple[list, int]:
    """(rows, malformed_count) for a JSONL file that may not exist."""
    rows, bad = [], 0
    if not os.path.exists(path):
        return rows, bad
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                bad += 1
    return rows, bad


@dataclass
class KalshiRead:
    monthly_live: dict = field(default_factory=dict)    # "YYYY-MM" -> net USD (counts)
    monthly_paper: dict = field(default_factory=dict)   # "YYYY-MM" -> net USD (never counts)
    live_count: int = 0
    paper_count: int = 0
    notes: list = field(default_factory=list)


def read_kalshi(audit_dir: str) -> KalshiRead:
    out = KalshiRead()
    if not os.path.isdir(audit_dir):
        out.notes.append(
            f"Kalshi: no audit records found at {audit_dir} — the own-account bettor "
            "has not banked any settlements yet, so this stream reads $0.")
        return out
    files = sorted(n for n in os.listdir(audit_dir)
                   if n.startswith("audit-") and n.endswith(".jsonl"))
    if not files:
        out.notes.append(
            f"Kalshi: the audit directory {audit_dir} holds no audit files yet, "
            "so this stream reads $0.")
        return out
    bad = 0
    for name in files:
        rows, n_bad = _read_jsonl(os.path.join(audit_dir, name))
        bad += n_bad
        for row in rows:
            if row.get("event") != "settlement":
                continue
            month = _month_of_day(row.get("day") or "") or et_month(row.get("ts") or "")
            if month is None:
                bad += 1
                continue
            pnl = float(row.get("pnl_usd") or 0.0)
            if row.get("mode") == "live":
                out.monthly_live[month] = round(out.monthly_live.get(month, 0.0) + pnl, 4)
                out.live_count += 1
            else:
                out.monthly_paper[month] = round(out.monthly_paper.get(month, 0.0) + pnl, 4)
                out.paper_count += 1
    if bad:
        out.notes.append(f"Kalshi: skipped {bad} malformed audit row(s).")
    if out.live_count == 0:
        paper_total = round(sum(out.monthly_paper.values()), 2)
        out.notes.append(
            "Kalshi: no live settlements yet — the bettor is still paper-only, so "
            f"this stream reads $0. (Paper settlements so far: {out.paper_count} "
            f"totalling ${paper_total:,.2f}; paper never counts toward the hurdle.)")
    return out


@dataclass
class SubRead:
    monthly: dict = field(default_factory=dict)         # "YYYY-MM" -> net USD
    row_count: int = 0
    notes: list = field(default_factory=list)


def read_subscriptions(records_path: str) -> SubRead:
    out = SubRead()
    if not os.path.exists(records_path):
        out.notes.append(
            "Subscriptions: no revenue records yet — the product is pre-launch, "
            "so this stream reads $0. (Records land in "
            f"{records_path} once payments exist.)")
        return out
    rows, bad = _read_jsonl(records_path)
    for row in rows:
        month = _month_of_day(row.get("date") or "") or et_month(row.get("ts") or "")
        if month is None or row.get("amount_usd") is None:
            bad += 1
            continue
        out.monthly[month] = round(out.monthly.get(month, 0.0) + float(row["amount_usd"]), 4)
        out.row_count += 1
    if bad:
        out.notes.append(f"Subscriptions: skipped {bad} malformed record row(s).")
    if out.row_count == 0:
        out.notes.append("Subscriptions: the records file is empty, so this stream reads $0.")
    return out


@dataclass
class RecRead:
    monthly: dict = field(default_factory=dict)         # "YYYY-MM" -> net USD (settled)
    bets: dict = field(default_factory=dict)            # bet_id -> bet row
    settlements: dict = field(default_factory=dict)     # bet_id -> winning settlement row
    open_bets: list = field(default_factory=list)       # bet rows with no settlement
    notes: list = field(default_factory=list)


def read_rec(log_path: str) -> RecRead:
    out = RecRead()
    if not os.path.exists(log_path):
        out.notes.append(
            "Recreational bets: none logged yet, so this stream reads $0. "
            f"(The log lives at {log_path}.)")
        return out
    rows, bad = _read_jsonl(log_path)
    settlements = []
    for row in rows:
        if row.get("type") == "bet" and row.get("id"):
            out.bets[row["id"]] = row
        elif row.get("type") == "settlement" and row.get("bet_id"):
            settlements.append(row)
        else:
            bad += 1
    superseded = 0
    for row in settlements:  # file order: the latest settlement for a bet wins
        if row["bet_id"] in out.settlements:
            superseded += 1
        out.settlements[row["bet_id"]] = row
    orphans = 0
    for bet_id, row in out.settlements.items():
        if bet_id not in out.bets:
            orphans += 1
            continue
        month = et_month(row.get("ts_settled") or "")
        if month is None:
            bad += 1
            continue
        out.monthly[month] = round(out.monthly.get(month, 0.0) + float(row.get("pnl_usd") or 0.0), 4)
    out.open_bets = [b for bid, b in out.bets.items() if bid not in out.settlements]
    if bad:
        out.notes.append(f"Recreational bets: skipped {bad} malformed log row(s).")
    if superseded:
        out.notes.append(
            f"Recreational bets: {superseded} settlement(s) were superseded by a "
            "later correction; only the latest counts.")
    if orphans:
        out.notes.append(
            f"Recreational bets: {orphans} settlement(s) name a bet that is not in "
            "the log; excluded until the bet record exists.")
    if not out.bets:
        out.notes.append("Recreational bets: the log is empty, so this stream reads $0.")
    return out
