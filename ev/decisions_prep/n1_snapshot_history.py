"""ev.decisions_prep.n1_snapshot_history -- Deterministic, strictly causal
Snapshot-History Block and temporal short-circuit for N1 ladder identity.

Argon ruling RUL-20261113-dec-gate-design Q4:
Ambiguous ladder quotes are judged with causal historical evidence extracted
from banked raw payload snapshots.

CAUSALITY RULE:
All snapshot history features are computed strictly from raw payload snapshots
with scrape_ts <= eval_ts. No future snapshots (ts > eval_ts) may ever enter
feature calculations.

FOUR CONTENT GROUPS (RUL-20261113-dec-gate-design Q4):
1. Temporal span & snapshot counts:
   snapshot_count, first_seen_ts, last_seen_ts, elapsed_span_minutes,
   event_snapshot_count, eval_ts.
2. Side history & price moves:
   two_sided_count, over_only_count, under_only_count, yes_only_count,
   no_only_count, one_sided_snapshot_ratio, ever_two_sided,
   opposite_side_ever_appeared, price_move_count.
3. Ladder history across points:
   distinct_rungs, min_rung, max_rung, period_token_ever.
4. Sibling collisions in the eval snapshot:
   sibling_full_game, sibling_period_scoped, same_book_distinct_full_game_market.

LABEL INDEPENDENCE:
This module NEVER reads or depends upon fixture label fields (`label`,
`grounding_detail`, `ambiguity_class`). Features are computed purely from raw
payloads and raw entry fields (book, event_id, player_norm, raw_market_key,
raw_point, raw_name).
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Optional, Sequence, Union

from ev.decisions_prep.n1_ladder_identity import deterministic_rung
from ev.props import norm_name, split_player, stat_of

# Regex matching period/segment tokens in a market key or outcome name
_PERIOD_TOKENS = (
    r"1q|2q|3q|4q|1h|2h|q1|q2|q3|q4|h1|h2|"
    r"first[\s_]+quarter|second[\s_]+quarter|third[\s_]+quarter|fourth[\s_]+quarter|"
    r"first[\s_]+half|second[\s_]+half|"
    r"1st[\s_]+quarter|2nd[\s_]+quarter|3rd[\s_]+quarter|4th[\s_]+quarter|"
    r"inning|period"
)
PERIOD_TOKEN_RE = re.compile(rf"(?<![a-zA-Z0-9])(?:{_PERIOD_TOKENS})(?![a-zA-Z0-9])", re.IGNORECASE)

_FEATURE_KEYS = (
    "snapshot_count",
    "first_seen_ts",
    "last_seen_ts",
    "elapsed_span_minutes",
    "two_sided_count",
    "over_only_count",
    "under_only_count",
    "yes_only_count",
    "no_only_count",
    "one_sided_snapshot_ratio",
    "ever_two_sided",
    "opposite_side_ever_appeared",
    "price_move_count",
    "distinct_rungs",
    "min_rung",
    "max_rung",
    "period_token_ever",
    "sibling_full_game",
    "sibling_period_scoped",
    "same_book_distinct_full_game_market",
    "event_snapshot_count",
    "eval_ts",
)


def scrape_ts_from_filename(day: str, filename: str) -> str:
    """Parse trailing _HHMMSS from snapshot filename -> f"{day}T{HH}:{MM}:{SS}"."""
    name = Path(filename).name
    m = re.search(r"_(\d{2})(\d{2})(\d{2})(?:\.[^.]+)?$", name)
    if not m:
        m = re.search(r"(\d{2})(\d{2})(\d{2})(?:\.[^.]+)?$", name)
    if not m:
        raise ValueError(f"Cannot parse scrape timestamp from filename: {filename}")
    return f"{day}T{m.group(1)}:{m.group(2)}:{m.group(3)}"


def _outcome_side(name: str) -> Optional[str]:
    """Map outcome name to side ('over', 'under', 'yes', 'no') or None."""
    low = name.strip().lower()
    if low.startswith("over"):
        return "over"
    if low.startswith("under"):
        return "under"
    if low == "yes":
        return "yes"
    if low == "no":
        return "no"
    return None


def extract_quotes(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract quote groups from a raw ParlayAPI/Odds-API payload.

    Groups outcomes by (book, market_key, description, point).
    Returns list of dicts with:
      book, player, player_norm, raw_market_key, raw_point,
      sides_present (sorted list), prices ({side: price}), names (sorted list).
    """
    groups: dict[tuple[str, str, str, Any], dict[str, Any]] = {}
    for bookmaker in payload.get("bookmakers", []):
        book = str(bookmaker.get("key") or "")
        for market in bookmaker.get("markets", []):
            mkey = str(market.get("key") or "")
            for outcome in market.get("outcomes", []):
                desc = str(outcome.get("description") or "")
                raw_pt = outcome.get("point")
                norm_pt = (
                    float(raw_pt)
                    if isinstance(raw_pt, (int, float)) and not isinstance(raw_pt, bool)
                    else raw_pt
                )
                grp_key = (book, mkey, desc, norm_pt)
                if grp_key not in groups:
                    clean_player = split_player(desc)[0] if desc else ""
                    groups[grp_key] = {
                        "book": book,
                        "player": desc,
                        "player_norm": norm_name(clean_player),
                        "raw_market_key": mkey,
                        "raw_point": norm_pt,
                        "sides_present": set(),
                        "prices": {},
                        "names": set(),
                    }

                name = str(outcome.get("name") or "").strip()
                if name:
                    groups[grp_key]["names"].add(name)
                    side = _outcome_side(name)
                    if side is not None:
                        groups[grp_key]["sides_present"].add(side)
                        if "price" in outcome and outcome["price"] is not None:
                            groups[grp_key]["prices"][side] = outcome["price"]

    result = []
    for g in groups.values():
        result.append({
            "book": g["book"],
            "player": g["player"],
            "player_norm": g["player_norm"],
            "raw_market_key": g["raw_market_key"],
            "raw_point": g["raw_point"],
            "sides_present": sorted(g["sides_present"]),
            "prices": dict(sorted(g["prices"].items())),
            "names": sorted(g["names"]),
        })
    return result


def entry_side(entry: Mapping[str, Any]) -> Optional[str]:
    """Extract side for an entry: from deterministic_rung side or raw_name prefix."""
    det = deterministic_rung(entry)
    if det is not None:
        return det[1]
    raw_name = str(entry.get("raw_name") or "").strip().lower()
    if raw_name.startswith("over"):
        return "over"
    if raw_name.startswith("under"):
        return "under"
    if raw_name.startswith("yes"):
        return "yes"
    if raw_name.startswith("no"):
        return "no"
    return None


def compute_history(
    entry: Mapping[str, Any],
    snapshots: Sequence[tuple[str, Mapping[str, Any]]],
    eval_ts: str,
) -> dict[str, Any]:
    """Compute deterministic snapshot-history features for entry across snapshots.

    Args:
        entry: Quote entry dictionary (must provide book, player_norm/player,
            raw_market_key, raw_point, raw_name).
        snapshots: List of (scrape_ts, payload) sorted ascending by scrape_ts,
            pre-filtered to scrape_ts <= eval_ts.
        eval_ts: Scrape timestamp of the evaluated snapshot.

    Returns:
        Dict with 22 ruled feature keys.
    """
    entry_book = str(entry.get("book") or "")
    pn = entry.get("player_norm")
    if not pn and entry.get("player"):
        pn = norm_name(split_player(str(entry["player"]))[0])
    entry_pn = str(pn or "")
    entry_mkey = str(entry.get("raw_market_key") or "")
    entry_pt = entry.get("raw_point")
    entry_point = (
        float(entry_pt)
        if isinstance(entry_pt, (int, float)) and not isinstance(entry_pt, bool)
        else entry_pt
    )

    def _matches_tuple(q: dict[str, Any]) -> bool:
        if q["book"] != entry_book or q["player_norm"] != entry_pn or q["raw_market_key"] != entry_mkey:
            return False
        q_pt = q["raw_point"]
        if isinstance(q_pt, (int, float)) and isinstance(entry_point, (int, float)):
            return float(q_pt) == float(entry_point)
        return q_pt == entry_point

    # Cache extracted quotes per snapshot
    snapshot_quotes: list[tuple[str, list[dict[str, Any]]]] = [
        (ts, extract_quotes(payload)) for ts, payload in snapshots
    ]

    present_snapshots: list[tuple[str, dict[str, Any]]] = []
    for ts, quotes in snapshot_quotes:
        for q in quotes:
            if _matches_tuple(q):
                present_snapshots.append((ts, q))
                break

    snapshot_count = len(present_snapshots)
    first_seen_ts = present_snapshots[0][0] if snapshot_count > 0 else None
    last_seen_ts = present_snapshots[-1][0] if snapshot_count > 0 else None

    if snapshot_count <= 1 or first_seen_ts is None or last_seen_ts is None:
        elapsed_span_minutes = 0.0
    else:
        dt_first = datetime.fromisoformat(first_seen_ts)
        dt_last = datetime.fromisoformat(last_seen_ts)
        elapsed_span_minutes = round((dt_last - dt_first).total_seconds() / 60.0, 1)

    two_sided_count = 0
    over_only_count = 0
    under_only_count = 0
    yes_only_count = 0
    no_only_count = 0
    one_sided_other_count = 0
    all_sides_present: set[str] = set()

    for ts, q in present_snapshots:
        sides = set(q.get("sides_present") or ())
        all_sides_present.update(sides)
        has_over_under = "over" in sides and "under" in sides
        has_yes_no = "yes" in sides and "no" in sides
        if has_over_under or has_yes_no:
            two_sided_count += 1
        elif sides == {"over"}:
            over_only_count += 1
        elif sides == {"under"}:
            under_only_count += 1
        elif sides == {"yes"}:
            yes_only_count += 1
        elif sides == {"no"}:
            no_only_count += 1
        else:
            one_sided_other_count += 1

    if snapshot_count > 0:
        one_sided_sum = (
            over_only_count
            + under_only_count
            + yes_only_count
            + no_only_count
            + one_sided_other_count
        )
        one_sided_snapshot_ratio = round(one_sided_sum / snapshot_count, 4)
    else:
        one_sided_snapshot_ratio = 0.0

    ever_two_sided = two_sided_count > 0

    e_side = entry_side(entry)
    opposite_map = {"over": "under", "under": "over", "yes": "no", "no": "yes"}
    if e_side in opposite_map:
        opposite_side_ever_appeared = opposite_map[e_side] in all_sides_present
    else:
        opposite_side_ever_appeared = False

    price_move_count = 0
    if e_side is not None:
        prices_series = []
        for ts, q in present_snapshots:
            q_prices = q.get("prices") or {}
            if e_side in q_prices and q_prices[e_side] is not None:
                prices_series.append(q_prices[e_side])
        for i in range(len(prices_series) - 1):
            if prices_series[i] != prices_series[i + 1]:
                price_move_count += 1

    # Ladder history across all snapshots for this (book, raw_market_key, player_norm)
    all_rungs: set[float] = set()
    for ts, quotes in snapshot_quotes:
        for q in quotes:
            if (
                q["book"] == entry_book
                and q["player_norm"] == entry_pn
                and q["raw_market_key"] == entry_mkey
            ):
                pt = q["raw_point"]
                if isinstance(pt, (int, float)) and not isinstance(pt, bool):
                    all_rungs.add(float(pt))

    distinct_rungs = sorted(all_rungs)
    min_rung = min(distinct_rungs) if distinct_rungs else None
    max_rung = max(distinct_rungs) if distinct_rungs else None

    # period_token_ever across all snapshots for this player_norm (any book, any market)
    period_token_ever = False
    for ts, quotes in snapshot_quotes:
        for q in quotes:
            if q["player_norm"] == entry_pn:
                if PERIOD_TOKEN_RE.search(q["raw_market_key"]):
                    period_token_ever = True
                    break
                if any(PERIOD_TOKEN_RE.search(name) for name in q.get("names", [])):
                    period_token_ever = True
                    break
        if period_token_ever:
            break

    # Sibling collisions in EVAL snapshot only
    sibling_full_game = False
    sibling_period_scoped = False
    same_book_distinct_full_game_market = False

    eval_quotes: Optional[list[dict[str, Any]]] = None
    for ts, quotes in snapshot_quotes:
        if ts == eval_ts:
            eval_quotes = quotes
            break

    if eval_quotes is not None:
        matched_stat = stat_of(entry_mkey)[0] if entry_mkey else None

        for q in eval_quotes:
            if q["player_norm"] != entry_pn:
                continue

            q_key = q["raw_market_key"]
            q_has_period_in_key = bool(PERIOD_TOKEN_RE.search(q_key))
            q_has_period_in_names = any(PERIOD_TOKEN_RE.search(name) for name in q.get("names", []))
            q_has_period = q_has_period_in_key or q_has_period_in_names

            if q_has_period:
                sibling_period_scoped = True

            if not q_has_period_in_key:
                q_stat = stat_of(q_key)[0] if q_key else None
                if q["book"] != entry_book:
                    if matched_stat is not None:
                        if q_stat == matched_stat:
                            sibling_full_game = True
                    else:
                        if q_stat is not None:
                            sibling_full_game = True
                else:
                    if q_key != entry_mkey:
                        if matched_stat is not None:
                            if q_stat == matched_stat:
                                same_book_distinct_full_game_market = True
                        else:
                            if q_stat is not None:
                                same_book_distinct_full_game_market = True

    return {
        "snapshot_count": snapshot_count,
        "first_seen_ts": first_seen_ts,
        "last_seen_ts": last_seen_ts,
        "elapsed_span_minutes": elapsed_span_minutes,
        "two_sided_count": two_sided_count,
        "over_only_count": over_only_count,
        "under_only_count": under_only_count,
        "yes_only_count": yes_only_count,
        "no_only_count": no_only_count,
        "one_sided_snapshot_ratio": one_sided_snapshot_ratio,
        "ever_two_sided": ever_two_sided,
        "opposite_side_ever_appeared": opposite_side_ever_appeared,
        "price_move_count": price_move_count,
        "distinct_rungs": distinct_rungs,
        "min_rung": min_rung,
        "max_rung": max_rung,
        "period_token_ever": period_token_ever,
        "sibling_full_game": sibling_full_game,
        "sibling_period_scoped": sibling_period_scoped,
        "same_book_distinct_full_game_market": same_book_distinct_full_game_market,
        "event_snapshot_count": len(snapshots),
        "eval_ts": eval_ts,
    }


def insufficient_history(features: Mapping[str, Any]) -> bool:
    """True iff the entry has insufficient temporal history and no collisions."""
    sc = features.get("snapshot_count", 0)
    has_collision = (
        bool(features.get("sibling_full_game"))
        or bool(features.get("sibling_period_scoped"))
        or bool(features.get("same_book_distinct_full_game_market"))
    )
    return sc < 3 and not has_collision


def render_history_block(features: Mapping[str, Any]) -> str:
    """Render deterministic multi-line snapshot history block for Decisions prompt."""
    lines = [
        "Snapshot history (deterministic, causal: only snapshots at or before the evaluated snapshot):"
    ]
    for k in _FEATURE_KEYS:
        val = features.get(k)
        if val is None:
            v_str = "none"
        elif isinstance(val, bool):
            v_str = "yes" if val else "no"
        else:
            v_str = str(val)
        label = "Evaluated snapshot scrape time" if k == "eval_ts" else k
        lines.append(f"{label}: {v_str}")
    return "\n".join(lines)


def build_sidecar(
    labels_path: Union[str, os.PathLike[str]],
    raw_dir: Union[str, os.PathLike[str]],
    out_path: Union[str, os.PathLike[str]],
) -> dict[str, Any]:
    """Build snapshot history sidecar JSONL for fixture labels from raw payload snapshots."""
    from ev.decisions_ladder_adjudicator import make_entry_key

    raw_base = Path(raw_dir)
    labels_file = Path(labels_path)

    label_rows = []
    with open(labels_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                label_rows.append(json.loads(line))

    warnings_count = 0
    tuple_absent_count = 0
    distinct_events: set[str] = set()
    out_rows: list[Optional[dict[str, Any]]] = [None] * len(label_rows)

    # Group row indices by (source_day, event_id) so each event's payloads
    # are loaded once and released before the next event (bounded memory);
    # output rows are written in the original label order regardless.
    groups: dict[tuple[str, str], list[int]] = {}
    for idx, row in enumerate(label_rows):
        key = (str(row.get("source_day") or ""), str(row.get("event_id") or ""))
        groups.setdefault(key, []).append(idx)
        distinct_events.add(key[1])

    for (source_day, event_id), indices in groups.items():
        day_dir = raw_base / source_day
        pattern = f"{event_id}_*.json"
        all_event_files = sorted(day_dir.glob(pattern))

        # The latest eval_ts in this group bounds the snapshots to load.
        group_eval_ts = max(
            scrape_ts_from_filename(
                source_day, Path(str(label_rows[i].get("source_object") or "")).name
            )
            for i in indices
        )
        payload_cache: dict[Path, dict[str, Any]] = {}
        group_snapshots: list[tuple[str, Mapping[str, Any]]] = []
        for fpath in all_event_files:
            try:
                ts = scrape_ts_from_filename(source_day, fpath.name)
            except ValueError:
                continue
            if ts <= group_eval_ts:
                with open(fpath, "r", encoding="utf-8") as pf:
                    payload_cache[fpath] = json.load(pf)
                group_snapshots.append((ts, payload_cache[fpath]))
        group_snapshots.sort(key=lambda x: x[0])

        for idx in indices:
            row = label_rows[idx]
            source_object = str(row.get("source_object") or "")
            entry_key = row.get("entry_key") or make_entry_key(row)
            eval_filename = Path(source_object).name
            eval_ts = scrape_ts_from_filename(source_day, eval_filename)

            snapshots = [(ts, p) for ts, p in group_snapshots if ts <= eval_ts]
            if not any(ts == eval_ts for ts, _ in snapshots):
                warnings_count += 1

            features = compute_history(row, snapshots, eval_ts)

            # Verify entry's own tuple is present in eval snapshot
            eval_payload = None
            for ts, p in snapshots:
                if ts == eval_ts:
                    eval_payload = p
                    break

            tuple_present = False
            if eval_payload is not None:
                eqs = extract_quotes(eval_payload)
                row_book = str(row.get("book") or "")
                row_pn = str(row.get("player_norm") or norm_name(split_player(str(row.get("player") or ""))[0]))
                row_mkey = str(row.get("raw_market_key") or "")
                row_pt = row.get("raw_point")
                for eq in eqs:
                    if eq["book"] == row_book and eq["player_norm"] == row_pn and eq["raw_market_key"] == row_mkey:
                        eq_pt = eq["raw_point"]
                        if isinstance(eq_pt, (int, float)) and isinstance(row_pt, (int, float)):
                            if float(eq_pt) == float(row_pt):
                                tuple_present = True
                                break
                        elif eq_pt == row_pt:
                            tuple_present = True
                            break

            if not tuple_present:
                tuple_absent_count += 1

            out_rows[idx] = {
                "entry_key": entry_key,
                "eval_ts": eval_ts,
                "features": features,
            }

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as out_f:
        for r in out_rows:
            out_f.write(json.dumps(r) + "\n")

    return {
        "entries": len(out_rows),
        "events": len(distinct_events),
        "warnings": warnings_count,
        "tuple_absent_in_eval_snapshot": tuple_absent_count,
    }


def main() -> None:
    """CLI runner: build snapshot history sidecar."""
    parser = argparse.ArgumentParser(description="Build N1 snapshot history sidecar.")
    parser.add_argument("--labels", required=True, help="Path to fixture labels JSONL.")
    parser.add_argument("--raw-dir", required=True, help="Path to raw payloads directory.")
    parser.add_argument("--out", required=True, help="Path to output sidecar JSONL.")
    args = parser.parse_args()

    summary = build_sidecar(args.labels, args.raw_dir, args.out)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
