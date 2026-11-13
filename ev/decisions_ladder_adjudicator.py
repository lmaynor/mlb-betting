"""ev.decisions_ladder_adjudicator -- N1 ladder market-identity adjudicator route.

DOCTRINE:
Deterministic parse FIRST; Decisions is consulted ONLY for the ambiguity set;
acceptance is the prep gate (noul >= 0.95 scope confidence + exact stat agreement
+ P(unknown) <= 0.10); LIVE CANONICALIZATION IS OFF in this build - the route emits
shadow decisions and quarantine lists only, applied=False always, until the 660-label
fixture gate passes (false canonicalization <= 0.5%, scope disagreement <= 1.0%,
quarantine-rate drop >= 30% vs parser-only) and a human enables it.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Mapping, Optional, Sequence, Union

from ev.decisions_client import BatchResult, DecisionsClient, ItemBatch, MAX_QUESTIONS_PER_CALL
from ev.decisions_common import (
    MAX_INPUT_TOKENS,
    append_records,
    estimate_tokens,
    make_record,
    route_mode,
)
from ev.decisions_prep.n1_ladder_identity import (
    adjudicate,
    build_questions,
    build_request_input,
    deterministic_rung,
    is_ambiguous,
    select_ambiguity_set,
)
from ev.props import norm_name, stat_of

ROUTE_NAME = "ladder_adjudicator"
ENV_MODE_VAR = "DECISIONS_LADDER_MODE"


def make_entry_key(entry: Mapping[str, Any]) -> str:
    """Build the canonical key for one quote entry.

    Format: f"{book}|{player_norm}|{raw_market_key}|{raw_point}|{raw_name}".
    """
    if "entry_key" in entry and entry["entry_key"]:
        return str(entry["entry_key"])
    book = str(entry.get("book") or "")
    pn = entry.get("player_norm")
    if not pn and entry.get("player"):
        pn = norm_name(str(entry["player"]))
    player_norm = str(pn or "")
    raw_market_key = str(entry.get("raw_market_key") or "")
    raw_point = "" if entry.get("raw_point") is None else str(entry["raw_point"])
    raw_name = str(entry.get("raw_name") or "")
    return f"{book}|{player_norm}|{raw_market_key}|{raw_point}|{raw_name}"


def group_key(entry: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return the grouping tuple for an entry: (book, player_norm or player, event_id)."""
    book = str(entry.get("book") or "")
    player = str(entry.get("player_norm") or entry.get("player") or "")
    event_id = str(entry.get("event_id") or "")
    return (book, player, event_id)


def build_parser_view(
    entry: Mapping[str, Any],
    group_entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Construct the parser view dictionary for an entry given its peer group entries.

    Returns:
        candidate_stat: entry's parser_candidate_stat if present, else stat_of(raw_market_key)[0].
        points_for_book_player: sorted distinct raw_point across group_entries.
        two_sided: True iff any group entry's raw_price_side_info.sides_present covers both
            "over" and "under" (also honors a two_sided key if present).
        cross_book_main_point: entry's cross_book_main_point.
    """
    cand = entry.get("parser_candidate_stat")
    if cand is None:
        mkey = entry.get("raw_market_key")
        if mkey:
            cand, _ = stat_of(str(mkey))

    pts: set[float] = set()
    for e in group_entries:
        pt = e.get("raw_point")
        if isinstance(pt, (int, float)) and not isinstance(pt, bool):
            pts.add(float(pt))

    # Honor explicit points_for_book_player if attached to entry
    if "points_for_book_player" in entry and entry["points_for_book_player"]:
        for p in entry["points_for_book_player"]:
            if isinstance(p, (int, float)) and not isinstance(p, bool):
                pts.add(float(p))
    elif len(pts) < 2 and entry.get("ambiguity_class") in ("onesided_ladder_rung", "twosided_offmain_rung"):
        if entry.get("cross_book_main_point") is not None:
            pts.add(float(entry["cross_book_main_point"]))

    if not pts:
        raw_pt = entry.get("raw_point")
        if isinstance(raw_pt, (int, float)) and not isinstance(raw_pt, bool):
            pts.add(float(raw_pt))

    two_sided = False
    for e in group_entries:
        if bool(e.get("two_sided")):
            two_sided = True
            break
        info = e.get("raw_price_side_info")
        if isinstance(info, Mapping):
            sides = set(info.get("sides_present") or ())
            if "over" in sides and "under" in sides:
                two_sided = True
                break

    if not two_sided and bool(entry.get("two_sided")):
        two_sided = True
    if not two_sided:
        info = entry.get("raw_price_side_info")
        if isinstance(info, Mapping):
            sides = set(info.get("sides_present") or ())
            if "over" in sides and "under" in sides:
                two_sided = True

    return {
        "candidate_stat": cand,
        "points_for_book_player": sorted(pts),
        "two_sided": two_sided,
        "cross_book_main_point": entry.get("cross_book_main_point"),
    }


def candidate_stats_for(
    entry: Mapping[str, Any],
    group_entries: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Collect sorted distinct non-null candidate stats across the group plus the entry.

    Never empty: falls back to ["unknown"].
    """
    candidates: set[str] = set()

    def _extract_stats(e: Mapping[str, Any]) -> None:
        cs_list = e.get("candidate_stats")
        if isinstance(cs_list, (list, tuple, set)):
            for s in cs_list:
                if s is not None and str(s).strip() and str(s) != "unknown":
                    candidates.add(str(s))
        for key in ("parser_candidate_stat", "candidate_stat"):
            val = e.get(key)
            if val is not None and str(val).strip() and str(val) != "unknown":
                candidates.add(str(val))
        mkey = e.get("raw_market_key")
        if mkey:
            stat, _ = stat_of(str(mkey))
            if stat is not None and str(stat).strip() and str(stat) != "unknown":
                candidates.add(str(stat))

    _extract_stats(entry)
    for ge in group_entries:
        _extract_stats(ge)

    res = sorted(candidates)
    if res:
        return res
    # No parser candidate at all (unmapped key): the candidate set is the full
    # canonical stat vocabulary. Falling back to ["unknown"] alone would make
    # the prep choice question single-option, which the Decisions API rejects
    # (choice criteria need 2-255 options) - and semantically the model must
    # be able to name the stat the parser missed, or choose unknown.
    from ev.decisions_prep.n1_ladder_identity import _STAT_DESCRIPTIONS

    return sorted(s for s in _STAT_DESCRIPTIONS if s != "unknown")


def cross_book_structure(entry: Mapping[str, Any], all_entries: Sequence[Mapping[str, Any]]) -> str:
    """Deterministic cross-book structure facts for this player + market.

    The milestone-vs-alt-rung scope question turns on the payload's whole
    structure for the market, not one book's quote: whether ANY book posts a
    two-sided market, how many books post it, and the union of points. These
    are payload facts computed from the entries themselves (never from any
    label or grounding text).
    """
    player = entry.get("player_norm") or entry.get("player")
    mkey = entry.get("raw_market_key")
    event_id = entry.get("event_id")
    books: set[str] = set()
    points: set[float] = set()
    any_two_sided = False
    n_quotes = 0
    for e in all_entries:
        if (e.get("player_norm") or e.get("player")) != player:
            continue
        if e.get("raw_market_key") != mkey or e.get("event_id") != event_id:
            continue
        n_quotes += 1
        if e.get("book"):
            books.add(str(e["book"]))
        rp = e.get("raw_point")
        if isinstance(rp, (int, float)):
            points.add(float(rp))
        info = e.get("raw_price_side_info") or {}
        sides = set(info.get("sides_present") or ())
        if "over" in sides and "under" in sides:
            any_two_sided = True
    return (
        "Cross-book structure for this player and market (whole payload):\n"
        f"Books posting this market: {sorted(books)}\n"
        f"Quotes across all books: {n_quotes}\n"
        f"Any book posting a two-sided (over and under) market: {'yes' if any_two_sided else 'no'}\n"
        f"Distinct points across all books: {sorted(points)}"
    )


def batch_item_for(
    entry: Mapping[str, Any],
    view: Mapping[str, Any],
    group_entries: Sequence[Mapping[str, Any]],
    all_entries: Sequence[Mapping[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """The (input, questions) pair for one ambiguous entry: the prep pack's
    request input plus the deterministic cross-book structure block."""
    input_block = build_request_input(entry, view) + "\n" + cross_book_structure(entry, all_entries)
    return input_block, build_questions(entry, candidate_stats_for(entry, group_entries))



def adjudicate_entries(
    entries: Sequence[Mapping[str, Any]],
    client: Any = None,
    *,
    mode: Optional[str] = None,
    log_path: Optional[Union[str, os.PathLike[str]]] = None,
) -> dict[str, Any]:
    """Adjudicate quote entries through deterministic matching and Decisions ambiguity resolution.

    Args:
        entries: Raw payload-shaped quote dictionaries.
        client: Optional DecisionsClient or fake client. If None, instantiates DecisionsClient().
            If construction fails, takes the abstention path.
        mode: Operating mode ('shadow' or 'live'). Defaults to DECISIONS_LADDER_MODE env var or 'shadow'.
        log_path: Optional file path to append decision records JSONL.

    Returns:
        dict with 'decisions' list and 'summary' dictionary.
    """
    mode = mode or route_mode(ENV_MODE_VAR)

    # 1. Group entries and build parser views
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for entry in entries:
        groups.setdefault(group_key(entry), []).append(entry)

    views = [build_parser_view(entry, groups[group_key(entry)]) for entry in entries]
    is_ambig_list = [is_ambiguous(entry, view) for entry, view in zip(entries, views)]

    decisions: list[dict[str, Any]] = [{} for _ in entries]

    # 2. Non-ambiguous entries
    for idx, (entry, view, is_ambig) in enumerate(zip(entries, views, is_ambig_list)):
        if is_ambig:
            continue
        entry_key = make_entry_key(entry)
        det = deterministic_rung(entry)
        raw_point = entry.get("raw_point")
        if det is None:
            decisions[idx] = {
                "entry_key": entry_key,
                "status": "quarantined",
                "identity": None,
                "reasons": ["no_deterministic_rung_parse"],
                "applied": False,
                "mode": mode,
            }
        else:
            rung, side = det
            if not isinstance(raw_point, (int, float)) or float(raw_point) != rung:
                decisions[idx] = {
                    "entry_key": entry_key,
                    "status": "quarantined",
                    "identity": None,
                    "reasons": [f"rung_mismatch:parsed={rung}!=raw_point={raw_point}"],
                    "applied": False,
                    "mode": mode,
                }
            else:
                main_point = view.get("cross_book_main_point")
                if main_point is not None and rung is not None and float(main_point) == rung:
                    scope = "main"
                elif len(view.get("points_for_book_player") or ()) <= 1:
                    scope = "main"
                else:
                    scope = "alternate"
                decisions[idx] = {
                    "entry_key": entry_key,
                    "status": "deterministic",
                    "identity": {
                        "stat": view.get("candidate_stat"),
                        "rung": rung,
                        "side": side,
                        "scope": scope,
                    },
                    "reasons": [],
                    "applied": False,
                    "mode": mode,
                }

    # 3. Ambiguous entries
    ambig_indices = [idx for idx, is_ambig in enumerate(is_ambig_list) if is_ambig]
    if ambig_indices:
        active_client = client
        if active_client is None:
            try:
                active_client = DecisionsClient()
            except Exception:
                active_client = None

        # Chunk into ItemBatch batches respecting MAX_QUESTIONS_PER_CALL (1000)
        batches: list[ItemBatch] = []
        batch_indices: list[list[int]] = []
        curr_batch = ItemBatch()
        curr_indices: list[int] = []

        for idx in ambig_indices:
            entry = entries[idx]
            view = views[idx]
            input_block, q_map = batch_item_for(entry, view, groups[group_key(entry)], entries)

            if curr_batch.question_count + len(q_map) > MAX_QUESTIONS_PER_CALL and curr_batch.question_count > 0:
                batches.append(curr_batch)
                batch_indices.append(curr_indices)
                curr_batch = ItemBatch()
                curr_indices = []

            curr_batch.add(input_block, q_map)
            curr_indices.append(idx)

        if curr_batch.question_count > 0:
            batches.append(curr_batch)
            batch_indices.append(curr_indices)

        for batch, chunk_indices in zip(batches, batch_indices):
            input_text, wire_questions = batch.build()
            est_tokens = estimate_tokens(input_text)
            if est_tokens > 0.9 * MAX_INPUT_TOKENS:
                raise ValueError(
                    f"Estimated input tokens ({est_tokens}) exceeds 90% of MAX_INPUT_TOKENS "
                    f"({int(0.9 * MAX_INPUT_TOKENS)})"
                )

            if active_client is None:
                batch_abstained = True
                split_results: dict[str, dict[str, Any]] = {}
            else:
                try:
                    result = active_client.call(input_text, wire_questions)
                    batch_abstained = result.abstained
                    split_results = batch.split(result) if not result.abstained else {}
                except Exception:
                    batch_abstained = True
                    split_results = {}

            if batch_abstained:
                for idx in chunk_indices:
                    entry = entries[idx]
                    decisions[idx] = {
                        "entry_key": make_entry_key(entry),
                        "status": "quarantined",
                        "identity": None,
                        "reasons": ["batch_abstained"],
                        "applied": False,
                        "mode": mode,
                    }
            else:
                for local_pos, idx in enumerate(chunk_indices):
                    entry = entries[idx]
                    view = views[idx]
                    tag = f"i{local_pos + 1:03d}"
                    answers = split_results.get(tag, {})
                    adj = adjudicate(entry, answers, view)
                    accepted = bool(adj.get("accepted"))
                    if accepted:
                        status = "accepted_shadow"
                        identity = {
                            "stat": adj.get("stat"),
                            "rung": adj.get("rung"),
                            "side": adj.get("side"),
                            "scope": adj.get("scope"),
                        }
                    else:
                        status = "quarantined"
                        identity = None
                    decisions[idx] = {
                        "entry_key": make_entry_key(entry),
                        "status": status,
                        "identity": identity,
                        "reasons": list(adj.get("reasons", [])),
                        "applied": False,
                        "mode": mode,
                    }

    # Logging
    if log_path is not None:
        records = [
            make_record(
                route=ROUTE_NAME,
                item_id=d["entry_key"],
                decision=d,
                mode=mode,
                applied=False,
            )
            for d in decisions
        ]
        append_records(log_path, records)

    # Summary
    summary = {
        "total": len(decisions),
        "deterministic": sum(1 for d in decisions if d.get("status") == "deterministic"),
        "ambiguous": len(ambig_indices),
        "accepted_shadow": sum(1 for d in decisions if d.get("status") == "accepted_shadow"),
        "quarantined": sum(1 for d in decisions if d.get("status") == "quarantined"),
    }

    return {
        "decisions": decisions,
        "summary": summary,
    }


def main() -> None:
    """CLI runner: adjudicate quote entries from a JSONL file."""
    parser = argparse.ArgumentParser(description="N1 ladder identity adjudicator route.")
    parser.add_argument("--entries", required=True, help="Path to JSONL file containing quote entries.")
    parser.add_argument("--out", required=True, help="Path to write JSON adjudication results.")
    parser.add_argument("--log", default=None, help="Optional path to append decision records JSONL.")
    parser.add_argument("--mode", default=None, help="Operating mode ('shadow' or 'live').")
    args = parser.parse_args()

    entries = []
    with open(args.entries, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    result = adjudicate_entries(entries, mode=args.mode, log_path=args.log)

    out_path = os.fspath(args.out)
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
