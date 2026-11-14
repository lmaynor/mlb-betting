"""Settlement Rules Auditor (Decisions-API Build #1).

Audits venue house settlement rules against canonical market definitions across
five key divergence dimensions using the Google Decisions API (gemini-decision-1).

DOCTRINE:
- Evaluates settlement text divergence fail-closed: explicit contradictions or
  unresolved answers route to quarantine.
- Paraphrases that settle identically have divergence probability near 0; explicit
  contradictions have divergence probability near 1.
- Operative divergence threshold is 0.20: any dimension with P(divergent) >= 0.20
  flags the entry for quarantine.
- Batched execution: all entries in an audit run are processed in ONE underlying
  DecisionsClient call via ItemBatch.
- Never writes production settlement mappings; generates audit reports and draft
  proposed matrices for human lock-in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from ev.decisions_client import DecisionsClient, ItemBatch, noul_value
from ev.decisions_common import (
    MAX_INPUT_TOKENS,
    append_records,
    estimate_tokens,
    make_record,
    route_mode,
)

DIMENSIONS: list[str] = [
    "overtime_included",
    "dnp_or_starter_rule",
    "stat_source_mismatch",
    "postponement_cutoff_mismatch",
    "tie_push_grading",
]

DIVERGENCE_THRESHOLD: float = 0.20
REJ_CODE: str = "REJ_SETTLEMENT_DIVERGENCE"

DIMENSION_INSTRUCTIONS: dict[str, str] = {
    "overtime_included": (
        "Assess whether the VENUE rule text settles the market DIFFERENTLY from the "
        "CANONICAL rule text regarding whether overtime periods, extra innings, extra "
        "time, or shootouts are included or excluded in settlement. Silence is not divergence: if the venue text does not address this dimension at all, the venue's default settlement follows the canonical treatment, so assign a probability near 0.0 unless the venue text affirmatively states a different treatment. Noul is the "
        "probability (0.0 to 1.0) that the venue rule text settles differently from "
        "the canonical rule text on this dimension, judged strictly from the two texts "
        "in the input block. Paraphrases or differing phrasings that settle identically "
        "(e.g. both including overtime, or both excluding extra time) are NOT divergence "
        "(probability near 0.0). Explicit venue rules contradicting canonical (e.g. "
        "canonical includes overtime while venue excludes it, or canonical excludes extra "
        "time while venue includes it) are divergence (probability near 1.0)."
    ),
    "dnp_or_starter_rule": (
        "Assess whether the VENUE rule text settles the market DIFFERENTLY from the "
        "CANONICAL rule text regarding starter requirements, Did Not Play (DNP) / scratch "
        "void rules, minimum activity thresholds (e.g. must start vs plate appearance "
        "only, minimum outs or pitches recorded), or substitute/replacement player grading. "
        "Silence is not divergence: if the venue text does not address this dimension at all, the venue's default settlement follows the canonical treatment, so assign a probability near 0.0 unless the venue text affirmatively states a different treatment. Noul is the probability (0.0 to 1.0) that the venue rule text settles differently "
        "from the canonical rule text on this dimension, judged strictly from the two texts "
        "in the input block. Paraphrases or differing phrasings that settle identically "
        "(e.g. both requiring starter in lineup plus PA, or both voiding on DNP) are NOT "
        "divergence (probability near 0.0). Explicit venue rules contradicting canonical "
        "(e.g. venue requiring plate appearance only when canonical requires starting, "
        "venue requiring 9 outs when canonical has no minimum, or venue grading replacement "
        "pitchers) are divergence (probability near 1.0)."
    ),
    "stat_source_mismatch": (
        "Assess whether the VENUE rule text settles the market DIFFERENTLY from the "
        "CANONICAL rule text regarding the official statistics source, data provider, "
        "postgame official league corrections, or crediting of shootout goals. Silence is not divergence: if the venue text does not address this dimension at all, the venue's default settlement follows the canonical treatment, so assign a probability near 0.0 unless the venue text affirmatively states a different treatment. Noul is the "
        "probability (0.0 to 1.0) that the venue rule text settles differently from the "
        "canonical rule text on this dimension, judged strictly from the two texts in the "
        "input block. Paraphrases or differing phrasings that settle identically (e.g. "
        "both using official league box score / competition statistics and respecting "
        "corrections) are NOT divergence (probability near 0.0). Explicit venue rules "
        "contradicting canonical (e.g. venue relying on a licensed/proprietary feed over "
        "official box scores, venue freezing stats at final buzzer ignoring postgame corrections, "
        "or crediting shootout-deciding goals when canonical does not) are divergence "
        "(probability near 1.0)."
    ),
    "postponement_cutoff_mismatch": (
        "Assess whether the VENUE rule text settles the market DIFFERENTLY from the "
        "CANONICAL rule text regarding game postponement, cancellation, suspension, or "
        "completion time limits and cutoff rules. Silence is not divergence: if the venue text does not address this dimension at all, the venue's default settlement follows the canonical treatment, so assign a probability near 0.0 unless the venue text affirmatively states a different treatment. Noul is the probability (0.0 to 1.0) that "
        "the venue rule text settles differently from the canonical rule text on this "
        "dimension, judged strictly from the two texts in the input block. Paraphrases or "
        "differing phrasings that settle identically (e.g. both voiding if postponed before "
        "start, or both applying the same completion cutoff) are NOT divergence "
        "(probability near 0.0). Explicit venue rules contradicting canonical (e.g. venue "
        "carrying bets forward to rescheduled date vs canonical voiding, venue allowing 30 days "
        "vs canonical same-week, venue expiring at fair market value instead of grading on "
        "completion, or venue grading at suspension point vs canonical voiding after 24 hours) "
        "are divergence (probability near 1.0)."
    ),
    "tie_push_grading": (
        "Assess whether the VENUE rule text settles the market DIFFERENTLY from the "
        "CANONICAL rule text regarding how exact ties or pushes on whole integer totals, "
        "spreads, or tied games are graded. Silence is not divergence: if the venue text does not address this dimension at all, the venue's default settlement follows the canonical treatment, so assign a probability near 0.0 unless the venue text affirmatively states a different treatment. Noul is the probability (0.0 to 1.0) that the "
        "venue rule text settles differently from the canonical rule text on this dimension, "
        "judged strictly from the two texts in the input block. Paraphrases or differing "
        "phrasings that settle identically (e.g. both refunding exact integer pushes) are "
        "NOT divergence (probability near 0.0). Explicit venue rules contradicting canonical "
        "(e.g. venue grading exact ties as losses, or contracts expiring worthless on a tie, "
        "where canonical refunds/pushes) are divergence (probability near 1.0)."
    ),
}


def build_entry_questions(entry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Build the noul question dictionary for a single settlement rule entry.

    Returns a dict with keys f"{entry_id}__{dim}" for each dim in DIMENSIONS,
    with type "noul" and instruction text explaining divergence probability.
    """
    entry_id = str(entry["entry_id"])
    return {
        f"{entry_id}__{dim}": {
            "type": "noul",
            "instructions": DIMENSION_INSTRUCTIONS[dim],
        }
        for dim in DIMENSIONS
    }


def build_entry_input(entry: Mapping[str, Any]) -> str:
    """Build the context block text for a single settlement rule entry."""
    entry_id = entry.get("entry_id", "")
    venue = entry.get("venue", "")
    venue_type = entry.get("venue_type", "")
    sport = entry.get("sport", "")
    market = entry.get("market", "")
    canonical_text = entry.get("canonical_text", "")
    rule_text = entry.get("rule_text", "")

    return (
        f"Entry ID: {entry_id}\n"
        f"Venue: {venue}\n"
        f"Venue Type: {venue_type}\n"
        f"Sport: {sport}\n"
        f"Market: {market}\n\n"
        f"Canonical settlement rules:\n{canonical_text}\n\n"
        f"Venue house rules:\n{rule_text}"
    )


def gate_entry(
    entry: Mapping[str, Any],
    answers_for_entry: Mapping[str, Any],
) -> dict[str, Any]:
    """Grade an entry against its dimension answers.

    Tolerates answers keyed by bare dimension name or prefixed by f"{entry_id}__".
    Extracts noul probabilities using DecisionsClient.noul_value.

    Returns:
        {
            "entry_id": str,
            "venue": str,
            "market": str,
            "dimensions": {dim: float or None},
            "flags": [dim with p >= DIVERGENCE_THRESHOLD],
            "unresolved": bool (any missing/non-float answer),
            "disposition": "quarantine" if flags or unresolved else "consistent",
        }
    """
    entry_id = str(entry.get("entry_id", ""))
    venue = str(entry.get("venue", ""))
    market = str(entry.get("market", ""))

    dimensions: dict[str, Optional[float]] = {}
    flags: list[str] = []
    unresolved = False

    for dim in DIMENSIONS:
        raw_val = answers_for_entry.get(dim)
        if raw_val is None and entry_id:
            raw_val = answers_for_entry.get(f"{entry_id}__{dim}")

        p = noul_value(raw_val)
        dimensions[dim] = p

        if p is None:
            unresolved = True
        elif p >= DIVERGENCE_THRESHOLD:
            flags.append(dim)

    disposition = "quarantine" if (flags or unresolved) else "consistent"

    return {
        "entry_id": entry_id,
        "venue": venue,
        "market": market,
        "dimensions": dimensions,
        "flags": flags,
        "unresolved": unresolved,
        "disposition": disposition,
    }


def audit_entries(
    entries: Sequence[Mapping[str, Any]],
    client: Optional[Any] = None,
    *,
    mode: Optional[str] = None,
    log_path: Optional[Union[str, os.PathLike[str]]] = None,
) -> dict[str, Any]:
    """Audit settlement rule entries in ONE batched Decisions API call.

    Args:
        entries: Sequence of rule entry dictionaries.
        client: DecisionsClient or compatible client. If None, instantiates
            DecisionsClient(). If constructor raises (e.g. GEMINI_API_KEY unset),
            fails closed: treats as batch abstention.
        mode: Operating mode ('shadow' or 'live'). Defaults to env var
            DECISIONS_SETTLEMENT_MODE via route_mode().
        log_path: Optional file path to append structured decision records.

    Returns:
        Dict containing "results", "divergence_report", "proposed_matrix", and "mode".
    """
    active_mode = mode if mode is not None else route_mode("DECISIONS_SETTLEMENT_MODE")
    applied = (active_mode == "live")

    if not entries:
        return {
            "results": [],
            "divergence_report": {
                "flagged_dimensions": {dim: 0 for dim in DIMENSIONS},
                "quarantined_entries": [],
                "consistent_entries": [],
                "unresolved_entries": [],
            },
            "proposed_matrix": [],
            "mode": active_mode,
        }

    # Setup client; fail closed on construction failure (e.g. missing API key)
    client_failed_closed = False
    if client is None:
        try:
            client = DecisionsClient()
        except Exception:
            client_failed_closed = True

    # Pack all entries into a single ItemBatch
    batch = ItemBatch()
    entry_tags: list[str] = []
    for entry in entries:
        input_block = build_entry_input(entry)
        questions = build_entry_questions(entry)
        tag = batch.add(input_block, questions)
        entry_tags.append(tag)

    input_text, wire_questions = batch.build()

    # Context token guard: fail loudly before calling API
    if estimate_tokens(input_text) > MAX_INPUT_TOKENS * 0.9:
        raise ValueError(
            f"Input text token estimate ({estimate_tokens(input_text)}) exceeds "
            f"90% of MAX_INPUT_TOKENS ({int(MAX_INPUT_TOKENS * 0.9)})"
        )

    # Perform call or handle client construction failure
    batch_abstained = client_failed_closed
    result = None
    if not client_failed_closed:
        try:
            result = client.call(input_text, wire_questions)
            is_ok = getattr(result, "ok", not getattr(result, "abstained", False))
            if not is_ok or getattr(result, "abstained", False):
                batch_abstained = True
        except Exception:
            batch_abstained = True

    # Process results fail closed
    results: list[dict[str, Any]] = []
    if batch_abstained or result is None:
        for entry in entries:
            gate_res = {
                "entry_id": str(entry.get("entry_id", "")),
                "venue": str(entry.get("venue", "")),
                "market": str(entry.get("market", "")),
                "dimensions": {dim: None for dim in DIMENSIONS},
                "flags": [],
                "unresolved": True,
                "disposition": "quarantine",
                "reason": "batch_abstained",
                "mode": active_mode,
                "applied": applied,
            }
            results.append(gate_res)
    else:
        split_answers = batch.split(result)
        for tag, entry in zip(entry_tags, entries):
            entry_id = str(entry.get("entry_id", ""))
            tag_answers = split_answers.get(tag, {})
            prefix = f"{entry_id}__"
            entry_answers = {
                qid[len(prefix):] if qid.startswith(prefix) else qid: ans
                for qid, ans in tag_answers.items()
            }
            gate_res = gate_entry(entry, entry_answers)
            gate_res["mode"] = active_mode
            gate_res["applied"] = applied
            results.append(gate_res)

    # Append decision records if log_path provided
    if log_path is not None:
        records = []
        for entry, res in zip(entries, results):
            extra_fields: dict[str, Any] = {
                "venue": res.get("venue"),
                "sport": entry.get("sport"),
                "market": res.get("market"),
                "dimensions": res.get("dimensions"),
                "flags": res.get("flags"),
                "unresolved": res.get("unresolved"),
            }
            if res.get("reason"):
                extra_fields["reason"] = res["reason"]
            rec = make_record(
                route="settlement_auditor",
                item_id=res["entry_id"],
                decision=res["disposition"],
                mode=res["mode"],
                applied=res["applied"],
                **extra_fields,
            )
            records.append(rec)
        append_records(log_path, records)

    # Compile divergence report
    flagged_dimensions = {dim: 0 for dim in DIMENSIONS}
    quarantined_entries: list[str] = []
    consistent_entries: list[str] = []
    unresolved_entries: list[str] = []

    for res in results:
        eid = res["entry_id"]
        for f in res["flags"]:
            if f in flagged_dimensions:
                flagged_dimensions[f] += 1
        if res["disposition"] == "quarantine":
            quarantined_entries.append(eid)
        else:
            consistent_entries.append(eid)
        if res["unresolved"]:
            unresolved_entries.append(eid)

    divergence_report = {
        "flagged_dimensions": flagged_dimensions,
        "quarantined_entries": quarantined_entries,
        "consistent_entries": consistent_entries,
        "unresolved_entries": unresolved_entries,
    }

    # Draft proposed matrix (never written to production mappings by this module)
    proposed_matrix = []
    for entry, res in zip(entries, results):
        flags_set = set(res["flags"])
        is_unres = res["unresolved"]
        row: dict[str, Any] = {
            "venue": entry.get("venue"),
            "sport": entry.get("sport"),
            "market": entry.get("market"),
        }
        for dim in DIMENSIONS:
            if dim in flags_set:
                row[dim] = "DIVERGENT"
            elif is_unres:
                row[dim] = "UNRESOLVED"
            else:
                row[dim] = "CANONICAL"
        proposed_matrix.append(row)

    return {
        "results": results,
        "divergence_report": divergence_report,
        "proposed_matrix": proposed_matrix,
        "mode": active_mode,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint for Settlement Rules Auditor."""
    parser = argparse.ArgumentParser(
        description="Settlement Rules Auditor (Decisions-API Build #1)"
    )
    parser.add_argument(
        "--rules",
        required=True,
        help="Path to JSONL settlement rule entries file",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Path to write output audit report JSON",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Optional path to append decision records as JSONL",
    )
    parser.add_argument(
        "--mode",
        choices=["shadow", "live"],
        default=None,
        help="Operating mode (defaults to DECISIONS_SETTLEMENT_MODE or 'shadow')",
    )

    args = parser.parse_args(argv)

    rules_path = Path(args.rules)
    if not rules_path.is_file():
        sys.stderr.write(f"Error: Rules file not found: {rules_path}\n")
        return 1

    entries = []
    with open(rules_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    # In CLI mode, attempt live DecisionsClient; audit_entries fails closed if unconfigured
    client = None
    try:
        client = DecisionsClient()
    except Exception:
        client = None

    report = audit_entries(entries, client=client, mode=args.mode, log_path=args.log)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    div_rep = report.get("divergence_report", {})
    quarantined = len(div_rep.get("quarantined_entries", []))
    consistent = len(div_rep.get("consistent_entries", []))
    print(f"Audit completed: {len(entries)} entries evaluated.")
    print(f"Quarantined entries: {quarantined}")
    print(f"Consistent entries:  {consistent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
