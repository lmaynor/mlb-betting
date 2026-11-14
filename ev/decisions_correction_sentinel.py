"""ev.decisions_correction_sentinel -- N5 post-settlement correction sentinel route.

DOCTRINE:
SHADOW-ONLY sentinel. It flags settled rows whose underlying stat was later
corrected, for quarantine review. It NEVER recomputes, regrades, or republishes
graded outcomes; flags only. applied=False always in this build.

The canonical input template is the fuller official-correction announcement framing
produced by ev.decisions_prep.n5_correction_sentinel.build_context (matched
settled ledger/boxscore stat line + full official correction context + explicit
stat-delta fields), pinned by unit tests, per Argon ruling RUL-20261113-dec-gate-design
Q2; the stat-category-only variant is retained in code for historical comparison
only and is not the canonical template.

The sentinel operates downstream of settlement and postgame ingestion:
1. Deterministic pairing: input items (or pairs) identify the settled row and
   the postgame capture/announcement. Unmatched or ambiguous pairs are excluded
   and unflagged (fail closed).
2. Batched Decisions API call: matched items are bundled into a single ItemBatch
   for decision-making. Live {"noul": p} probability answers are normalized to
   bare floats before invoking the prep quarantine gate.
3. Fail closed on abstention: any client construction failure, transport error,
   timeout, token limit breach, or API abstention quarantines all matched rows
   with reason "batch_abstained".
4. Reinstate eligibility: quarantined rows record a 48-hour auto-reinstate
   window (eligible_at = quarantined_at + 48h; decision at `now`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

from ev.decisions_client import (
    BatchResult,
    DecisionsClient,
    ItemBatch,
    noul_value,
)
from ev.decisions_common import (
    MAX_INPUT_TOKENS,
    append_records,
    estimate_tokens,
    make_record,
    route_mode,
)
from ev.decisions_prep import n5_correction_sentinel as n5_prep
from ev.decisions_prep.n5_correction_sentinel import (
    CORRECTION_KINDS,
    MODEL,
    Q_CORRECTION,
    Q_KIND,
    Q_NO_CORRECTION,
    QUARANTINE_P_CORRECTION,
    QUARANTINE_P_NO_CORRECTION_BELOW,
    REINSTATE_WINDOW_HOURS,
    build_context,
    build_questions,
    gate,
    match_settled_row,
    settled_row_key,
)

ENV_MODE_VAR = "DECISIONS_CORRECTION_MODE"
ROUTE_NAME = "n5_correction_sentinel"


def announcement_text(item: Mapping[str, Any]) -> str:
    """Compose the announcement prose deterministically from documented facts.

    f"Correction: the {league} has revised {player}'s {stat} for {game_date} ({team}) from {value_before} to {value_after}."
    plus item["prose_announced"] when present and not already conveying it
    (if prose_announced is a bool, synthesize from the facts only; if it is a string, append it).
    """
    league = str(item.get("league") or "").strip()
    player = str(item.get("player") or "").strip()
    stat = str(item.get("stat") or "").strip()
    game_date = str(item.get("game_date") or "").strip()
    team = str(item.get("team") or "").strip()

    # Resolve settled and corrected values (preferring explicit "settled"/"corrected" keys,
    # falling back to "value_before"/"value_after" if "settled"/"corrected" are not in item).
    if "settled" in item:
        settled_val = item["settled"]
    else:
        settled_val = item.get("value_before")

    if "corrected" in item:
        corrected_val = item["corrected"]
    else:
        corrected_val = item.get("value_after")

    val_before = item.get("value_before")
    if val_before is None and "settled" in item:
        val_before = item["settled"]
    if val_before is None:
        val_before = ""

    val_after = item.get("value_after")
    if val_after is None and "corrected" in item:
        val_after = item["corrected"]
    if val_after is None:
        val_after = ""

    announced = str(item.get("announced_date") or "").strip()
    base = (
        f"Official stat correction issued by the {league} stats authority"
        f"{f' on {announced}' if announced else ''}: the official box score for "
        f"{player} ({team}) for the game on {game_date} has been corrected. "
        f"{player}'s {stat} total for that game is revised from {val_before} to "
        f"{val_after}. The originally published {stat} figure of {val_before} is "
        f"superseded; {val_after} is the official corrected total."
    )

    body = base
    prose = item.get("prose_announced")
    if isinstance(prose, str) and prose.strip():
        prose_clean = prose.strip()
        if prose_clean not in base:
            body = f"{base} {prose_clean}"

    lines = [body]

    # Source context line:
    # f"Source: {source_name} ({source_url})" when both item["source_name"] and item["source_url"] are present;
    # f"Source: {source_name}" when only the name is present; omit entirely when neither is present.
    source_name = item.get("source_name")
    source_url = item.get("source_url")
    s_name_clean = str(source_name).strip() if source_name is not None else ""
    s_url_clean = str(source_url).strip() if source_url is not None else ""

    if s_name_clean and s_url_clean:
        lines.append(f"Source: {s_name_clean} ({s_url_clean})")
    elif s_name_clean:
        lines.append(f"Source: {s_name_clean}")

    # Explicit stat-delta section
    lines.append("Stat delta (deterministic):")

    if stat:
        lines.append(f"stat: {stat}")
    else:
        lines.append("stat: unknown")

    if settled_val is None or settled_val == "":
        lines.append("settled_value: unknown")
    else:
        lines.append(f"settled_value: {settled_val}")

    if corrected_val is None or corrected_val == "":
        lines.append("corrected_value: unknown")
    else:
        lines.append(f"corrected_value: {corrected_val}")

    def _is_num(val: Any) -> bool:
        return isinstance(val, (int, float)) and not isinstance(val, bool)

    if _is_num(settled_val) and _is_num(corrected_val):
        lines.append(f"delta: {corrected_val - settled_val}")
    else:
        lines.append("delta: n/a")

    return "\n".join(lines)


def sentinel_items_from_pairs(
    items_or_pairs: Iterable[Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Build (capture_row, settled_row) pairs from fixture-shaped correction items.

    capture_row = {"league", "game_date", "player", "team", "stat", "value": value_after,
                   "announcement_text": announcement_text(item), "announced_date"}
    settled_row = {"league", "game_date", "player", "team", "stat", "value": value_before}.

    Confirms settled_row_key compatibility between settled_row and capture_row,
    adapting keys to what prep expects.
    """
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for elem in items_or_pairs:
        # Already a (capture_row, settled_row) tuple or list
        if isinstance(elem, (tuple, list)) and len(elem) == 2:
            c, s = elem
            pairs.append((dict(c) if c is not None else None, dict(s) if s is not None else None))
            continue

        # Already a {"capture_row": ..., "settled_row": ...} mapping
        if isinstance(elem, Mapping) and "capture_row" in elem and "settled_row" in elem:
            c = elem["capture_row"]
            s = elem["settled_row"]
            pairs.append((dict(c) if c is not None else None, dict(s) if s is not None else None))
            continue

        # Fixture-shaped correction item
        item = elem
        ann = announcement_text(item)
        val_after = item.get("value_after")
        val_before = item.get("value_before")
        ann_date = item.get("announced_date") or item.get("game_date") or ""

        capture_row = {
            "league": item.get("league"),
            "game_date": item.get("game_date"),
            "player": item.get("player"),
            "team": item.get("team"),
            "stat": item.get("stat"),
            "value": val_after,
            "actual": val_after,
            "announcement_text": ann,
            "announced_date": item.get("announced_date"),
            "published_ts": ann_date,
            "headline": "Official Scoring Correction",
            "body": ann,
        }

        settled_row = {
            "league": item.get("league"),
            "game_date": item.get("game_date"),
            "player": item.get("player"),
            "team": item.get("team"),
            "stat": item.get("stat"),
            "value": val_before,
            "actual": val_before,
        }

        if "game_key" in item:
            capture_row["game_key"] = item["game_key"]
            settled_row["game_key"] = item["game_key"]
        if "player_id" in item:
            capture_row["player_id"] = item["player_id"]
            settled_row["player_id"] = item["player_id"]
        if "line" in item:
            capture_row["line"] = item["line"]
            settled_row["line"] = item["line"]
        if "settled_ts" in item:
            settled_row["settled_ts"] = item["settled_ts"]

        # Confirm settled_row_key compatibility
        s_key = settled_row_key(settled_row)
        c_key = settled_row_key(capture_row)
        if s_key != c_key:
            raise ValueError(f"settled_row_key mismatch: settled {s_key} vs capture {c_key}")

        pairs.append((capture_row, settled_row))

    return pairs


def reinstate_decision(
    record_or_hours: Mapping[str, Any] | float | int,
    now: Optional[Any] = None,
    *,
    correction_materialized: Optional[bool] = None,
) -> str:
    """Evaluate the 48-hour auto-reinstate rule.

    Compatible with both:
    - reinstate_decision(quarantine_record, now=None)
    - reinstate_decision(settled_ts_hours_since_boxscore, correction_materialized)
    """
    if isinstance(record_or_hours, Mapping):
        record = record_or_hours
        now_val = now
        if now_val is None:
            now_dt = datetime.now(timezone.utc)
        elif isinstance(now_val, str):
            now_dt = datetime.fromisoformat(now_val)
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)
        elif isinstance(now_val, datetime):
            now_dt = now_val if now_val.tzinfo is not None else now_val.replace(tzinfo=timezone.utc)
        else:
            now_dt = datetime.now(timezone.utc)

        corr = bool(record.get("correction_materialized", False) if correction_materialized is None else correction_materialized)
        q_at_raw = record.get("quarantined_at") or record.get("ts")
        if not q_at_raw:
            return "pending"

        q_dt = datetime.fromisoformat(str(q_at_raw))
        if q_dt.tzinfo is None:
            q_dt = q_dt.replace(tzinfo=timezone.utc)

        hours_elapsed = (now_dt - q_dt).total_seconds() / 3600.0
        return n5_prep.reinstate_decision(hours_elapsed, corr)

    hours = float(record_or_hours)
    corr = bool(now if correction_materialized is None else correction_materialized)
    return n5_prep.reinstate_decision(hours, corr)


def run_sentinel(
    pairs: Iterable[Any],
    client: Optional[Any] = None,
    *,
    mode: Optional[str] = None,
    log_path: Optional[Union[str, os.PathLike[str]]] = None,
    now: Optional[Union[datetime, str]] = None,
) -> list[dict[str, Any]]:
    """Execute the N5 post-settlement correction sentinel over (capture, settled) pairs.

    For each (capture_row, settled_row_or_None):
    - If settled is None (unmatched/ambiguous) -> record {"status": "unmatched", "quarantined": False}
      and exclude from batch (fail closed, no flag invented).
    - Else batch via ItemBatch using prep build_questions input/questions;
      normalize each noul answer to a bare float (DecisionsClient.noul_value) and choice answers pass through;
      call prep gate;
      record {"row_key": settled_row_key(settled), "status": "quarantined"|"clear", "gate": gate_result,
              "mode": mode, "applied": False, "quarantined_at": now iso}
      plus for quarantined records reinstate fields from reinstate_decision(record, now)
      (eligible_at = quarantined_at + 48h; decision at `now`).
    - Batch abstention -> all matched pairs quarantined with gate reason "batch_abstained" (fail closed).
    - mode = mode or route_mode("DECISIONS_CORRECTION_MODE").
    - estimate_tokens guard against exceeding token limit.
    - client None -> DecisionsClient() with construction failure -> abstention path.
    """
    mode = mode or route_mode(ENV_MODE_VAR)

    if now is None:
        now_dt = datetime.now(timezone.utc)
    elif isinstance(now, str):
        now_dt = datetime.fromisoformat(now)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
    elif isinstance(now, datetime):
        now_dt = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    else:
        now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()

    # Unpack pairs preserving input order
    input_list = list(pairs)
    records: list[Optional[dict[str, Any]]] = [None] * len(input_list)
    matched_indices: list[int] = []
    matched_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for idx, elem in enumerate(input_list):
        if isinstance(elem, (tuple, list)) and len(elem) == 2:
            c_row, s_row = elem
        elif isinstance(elem, Mapping) and "capture_row" in elem and "settled_row" in elem:
            c_row = elem["capture_row"]
            s_row = elem["settled_row"]
        else:
            # Fallback for single item dict
            c_row, s_row = sentinel_items_from_pairs([elem])[0]

        if s_row is None:
            # Unmatched / ambiguous: fail closed, exclude from batch, no flag invented
            rec_key = None
            if c_row:
                try:
                    rec_key = settled_row_key(c_row)
                except Exception:
                    rec_key = None
            records[idx] = {
                "row_key": rec_key,
                "status": "unmatched",
                "quarantined": False,
                "gate": None,
                "mode": mode,
                "applied": False,
                "quarantined_at": None,
            }
        else:
            matched_indices.append(idx)
            matched_pairs.append((dict(c_row) if c_row is not None else {}, dict(s_row)))

    # If no matched pairs exist, finish up
    if not matched_pairs:
        final_records = [r for r in records if r is not None]
        if log_path:
            _log_records(log_path, final_records, mode)
        return final_records

    # Initialize client (with construction failure -> abstention path)
    client_failed_closed = False
    if client is None:
        try:
            client = DecisionsClient()
        except Exception:
            client_failed_closed = True

    # Build ItemBatch
    batch = ItemBatch()
    tag_to_match_idx: dict[str, int] = {}

    for m_idx, (c_row, s_row) in enumerate(matched_pairs):
        prose_item = {
            "published_ts": c_row.get("published_ts") or c_row.get("announced_date") or "",
            "headline": c_row.get("headline") or ("Official announcement" if c_row.get("announcement_text") else ""),
            "body": c_row.get("body") if c_row.get("body") is not None else (c_row.get("announcement_text") or ""),
        }
        settled_dict = dict(s_row)
        if "actual" not in settled_dict and "value" in settled_dict:
            settled_dict["actual"] = settled_dict["value"]

        req = build_questions(settled_dict, prose_item)
        tag = batch.add(req["input"], req["questions"])
        tag_to_match_idx[tag] = m_idx

    combined_input, wire_questions = batch.build()

    # Token budget guard
    token_est = estimate_tokens(combined_input)
    token_budget_exceeded = token_est > (MAX_INPUT_TOKENS * 0.9)

    batch_abstained = client_failed_closed or token_budget_exceeded
    batch_result: Optional[BatchResult] = None

    if not batch_abstained:
        try:
            batch_result = client.call(combined_input, wire_questions)
            if batch_result.abstained:
                batch_abstained = True
        except Exception:
            batch_abstained = True

    # Process matched pairs
    if batch_abstained or batch_result is None:
        # Batch abstention path: all matched pairs quarantined with gate reason "batch_abstained"
        for orig_idx, (_, s_row) in zip(matched_indices, matched_pairs):
            gate_res = gate({})
            gate_res["reasons"] = ["batch_abstained"]
            rec = _build_quarantined_record(s_row, gate_res, mode, now_dt, now_iso)
            records[orig_idx] = rec
    else:
        per_item_answers = batch.split(batch_result)
        for tag, m_idx in tag_to_match_idx.items():
            orig_idx = matched_indices[m_idx]
            _, s_row = matched_pairs[m_idx]
            raw_answers = per_item_answers.get(tag, {})

            # Normalize noul answers to bare float; choice answers pass through
            normalized: dict[str, Any] = {}
            for qid, ans in raw_answers.items():
                nv = noul_value(ans)
                if nv is not None:
                    normalized[qid] = nv
                else:
                    normalized[qid] = ans

            gate_result = gate(normalized)

            if gate_result.get("action") == "quarantine":
                rec = _build_quarantined_record(s_row, gate_result, mode, now_dt, now_iso)
            else:
                rec = {
                    "row_key": settled_row_key(s_row),
                    "status": "clear",
                    "quarantined": False,
                    "gate": gate_result,
                    "mode": mode,
                    "applied": False,
                    "quarantined_at": None,
                }
            records[orig_idx] = rec

    final_records = [r for r in records if r is not None]
    if log_path:
        _log_records(log_path, final_records, mode)
    return final_records


def _build_quarantined_record(
    settled_row: Mapping[str, Any],
    gate_result: dict[str, Any],
    mode: str,
    now_dt: datetime,
    now_iso: str,
) -> dict[str, Any]:
    eligible_dt = now_dt + timedelta(hours=REINSTATE_WINDOW_HOURS)
    rec: dict[str, Any] = {
        "row_key": settled_row_key(settled_row),
        "status": "quarantined",
        "quarantined": True,
        "gate": gate_result,
        "mode": mode,
        "applied": False,
        "quarantined_at": now_iso,
        "eligible_at": eligible_dt.isoformat(),
    }
    rec["reinstate_decision"] = reinstate_decision(rec, now_dt)
    rec["decision"] = rec["reinstate_decision"]
    return rec


def _log_records(
    log_path: Union[str, os.PathLike[str]],
    records: Sequence[Mapping[str, Any]],
    mode: str,
) -> None:
    log_recs = []
    for r in records:
        r_key = r.get("row_key")
        item_id = ":".join(str(x) for x in r_key) if isinstance(r_key, (tuple, list)) else str(r_key or "unmatched")
        log_recs.append(
            make_record(
                route=ROUTE_NAME,
                item_id=item_id,
                decision=r.get("status"),
                mode=mode,
                applied=False,
                quarantined=r.get("quarantined", False),
                gate=r.get("gate"),
                quarantined_at=r.get("quarantined_at"),
                eligible_at=r.get("eligible_at"),
                reinstate_decision=r.get("reinstate_decision"),
            )
        )
    append_records(log_path, log_recs)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="N5 post-settlement correction sentinel route")
    parser.add_argument("--pairs", required=True, help="Path to pairs.jsonl (lines: {'capture_row': ..., 'settled_row': ...})")
    parser.add_argument("--out", required=True, help="Path to output flags.json")
    parser.add_argument("--log", default=None, help="Optional JSONL log path")
    parser.add_argument("--mode", default=None, help="Operating mode ('shadow' or 'live')")

    args = parser.parse_args(argv)

    pairs_path = Path(args.pairs)
    if not pairs_path.exists():
        print(f"Error: pairs file not found: {pairs_path}", file=sys.stderr)
        return 1

    pairs: list[tuple[Any, Any]] = []
    with open(pairs_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if "capture_row" in data and "settled_row" in data:
                pairs.append((data["capture_row"], data["settled_row"]))
            else:
                pairs.append(sentinel_items_from_pairs([data])[0])

    records = run_sentinel(pairs, mode=args.mode, log_path=args.log)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, default=str)

    print(f"Wrote {len(records)} sentinel records to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
