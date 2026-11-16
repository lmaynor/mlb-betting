"""
ev.hurdleledger.rec -- stream 2's write path: the append-only recreational bet log.

Design (OPS-12): the honest minimal v1 is this log plus the runner's two commands.
One command logs a bet the moment Luc fires it (a few seconds, no prompts); one
command settles it later. The log is append-only JSONL -- a settlement is a new
row, never an edit, and a correction is a later settlement row that supersedes
(the reader takes the latest and says so). Rows carry a `source` field so the
future site capture (a Gambly deeplink click inside the existing /ev bet list)
writes exactly the same rows with source="gambly-deeplink"; v1 sources are
"manual" (Luc at the book) and "chat" (Iggy logging at Luc's word).

Row shapes:
  bet:        {"type": "bet", "id": "rec-20261116-ab12cd34", "ts_placed": "...Z",
               "book": "draftkings", "description": "Tatum over 26.5 points",
               "price_american": -110, "price_decimal": 1.9091,
               "stake_usd": 25.0, "source": "manual"}
  settlement: {"type": "settlement", "bet_id": "rec-...", "ts_settled": "...Z",
               "result": "win|loss|push|void", "pnl_usd": 22.73}
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

RESULTS = ("win", "loss", "push", "void")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def american_to_decimal(american: float) -> float:
    american = float(american)
    if american > 0:
        return 1.0 + american / 100.0
    if american < 0:
        return 1.0 + 100.0 / abs(american)
    raise ValueError("american odds of 0 are not a price")


def decimal_to_american(decimal: float) -> int:
    decimal = float(decimal)
    if decimal <= 1.0:
        raise ValueError("decimal odds must exceed 1.0")
    if decimal >= 2.0:
        return int(round((decimal - 1.0) * 100))
    return int(round(-100.0 / (decimal - 1.0)))


def profit_for_result(stake_usd: float, price_decimal: float, result: str) -> float:
    """Realized P&L of a settled bet from stake + price (the book's arithmetic)."""
    if result == "win":
        return round(float(stake_usd) * (float(price_decimal) - 1.0), 2)
    if result == "loss":
        return round(-float(stake_usd), 2)
    if result in ("push", "void"):
        return 0.0
    raise ValueError(f"result must be one of {RESULTS}, got {result!r}")


def _append(log_path: str, row: dict) -> dict:
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return row


def _existing_ids(log_path: str) -> set:
    ids = set()
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("type") == "bet" and row.get("id"):
                    ids.add(row["id"])
    return ids


def log_bet(log_path: str, book: str, description: str, stake_usd: float,
            price_american=None, price_decimal=None, source: str = "manual") -> dict:
    """Append one bet record; returns the row (with its generated id)."""
    if (price_american is None) == (price_decimal is None):
        raise ValueError("give exactly one price: --price (american) or --decimal")
    if float(stake_usd) <= 0:
        raise ValueError("stake must be positive")
    if price_american is not None:
        dec = american_to_decimal(price_american)
        amer = int(price_american)
    else:
        dec = float(price_decimal)
        amer = decimal_to_american(dec)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    existing = _existing_ids(log_path)
    bet_id = f"rec-{day}-{uuid.uuid4().hex[:8]}"
    while bet_id in existing:
        bet_id = f"rec-{day}-{uuid.uuid4().hex[:8]}"
    return _append(log_path, {
        "type": "bet", "id": bet_id, "ts_placed": now_iso(),
        "book": book, "description": description,
        "price_american": amer, "price_decimal": round(dec, 4),
        "stake_usd": round(float(stake_usd), 2), "source": source,
    })


def settle_bet(log_path: str, bet_id: str, result: str,
               pnl_usd: float | None = None, supersede: bool = False) -> dict:
    """Append the settlement for a logged bet; returns the settlement row.

    P&L is computed from the bet's stake and price unless pnl_usd is given
    explicitly (books occasionally settle at a different figure -- cash-outs,
    partial voids -- and the book's number is the truth when it differs).
    Refuses to settle an unknown bet, and refuses a second settlement unless
    supersede=True (the new row then replaces the old in every read).
    """
    if result not in RESULTS:
        raise ValueError(f"result must be one of {RESULTS}, got {result!r}")
    bet, settled = None, False
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("type") == "bet" and row.get("id") == bet_id:
                    bet = row
                elif row.get("type") == "settlement" and row.get("bet_id") == bet_id:
                    settled = True
    if bet is None:
        raise ValueError(f"no bet with id {bet_id} in {log_path}")
    if settled and not supersede:
        raise ValueError(
            f"{bet_id} is already settled; pass --supersede to replace its settlement")
    if pnl_usd is None:
        pnl_usd = profit_for_result(bet["stake_usd"], bet["price_decimal"], result)
    return _append(log_path, {
        "type": "settlement", "bet_id": bet_id, "ts_settled": now_iso(),
        "result": result, "pnl_usd": round(float(pnl_usd), 2),
    })
