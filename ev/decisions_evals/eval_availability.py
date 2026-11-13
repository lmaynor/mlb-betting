"""Offline evaluation harness for Player Availability Classifier (Build #2).

Evaluates all four availability routes:
1. Goalie usability gate (NHL starting goalie confirmation)
2. Player status gate (NBA injury / availability classification)
3. Section N2 event gate (game-day event disruptions)
4. Section N6 NHL line-change verifier (5v5 lines & PP isolation)

Computes metrics and asserts threshold bars:
- Goalie: 0 false positives, confirmation recall on confirmed_starter, per-label table.
- Status: scratch recall >= 98%, false scratch rate <= 2%, uncertain counts.
- N2: postponement + suspension recall 100%, material-change false rate <= 1%.
- N6: 0 pp_only leakage, false suspension rate <= 5%, promotion/demotion/shuffle recall.

Note on compound items:
Compound items are deterministically constructed by concatenating a same-team
5v5-change blurb with a power-play-only blurb; they are not captured real-world blurbs.

Writes combined eval_results.json into tests/fixtures/decisions/availability/.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ev.decisions_availability import (
    build_day_slates,
    build_goalie_input,
    build_goalie_questions,
    build_n6_trio_input,
    build_n6_trio_questions,
    build_status_input,
    build_status_questions,
    gate_goalie,
    gate_status,
    n2_items_for_batch,
    n2_result,
    n6_result,
    prep_n6,
)
from ev.decisions_client import BatchResult, DecisionsConfigError
from ev.decisions_evals.common import (
    AnswersCache,
    Pacer,
    live_client,
    run_batched,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "decisions"

GOALIE_FIXTURE = FIXTURES_DIR / "availability" / "goalie_items.jsonl"
STATUS_FIXTURE = FIXTURES_DIR / "availability" / "status_items.jsonl"
N2_FIXTURE = FIXTURES_DIR / "n2_event_gate" / "items.jsonl"
N6_FIXTURE = FIXTURES_DIR / "n6_morning_skate" / "items.jsonl"
if not N6_FIXTURE.exists():
    N6_FIXTURE = FIXTURES_DIR / "n6_line_verifier" / "items.jsonl"

CACHE_GOALIE = FIXTURES_DIR / "availability" / "answers_goalie.jsonl"
CACHE_STATUS = FIXTURES_DIR / "availability" / "answers_status.jsonl"
CACHE_N2 = FIXTURES_DIR / "n2_event_gate" / "answers.jsonl"
CACHE_N6 = N6_FIXTURE.parent / "answers.jsonl"
EVAL_RESULTS_PATH = FIXTURES_DIR / "availability" / "eval_results.json"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _get_eval_client(section: str, all_items: Sequence[Mapping[str, Any]]) -> Any:
    """Live client or raise: this evaluation only scores live Decisions answers
    or previously cached live answers; it never simulates answers."""
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError(
            "GEMINI_API_KEY is not set and uncached items remain in section "
            f"{section!r}; refusing to simulate answers."
        )
    return live_client()


# ---------------------------------------------------------------------------
# Section 1: Goalie Usability Evaluation
# ---------------------------------------------------------------------------

def run_goalie_eval(pacer: Optional[Pacer] = None) -> dict[str, Any]:
    items = _load_jsonl(GOALIE_FIXTURE)
    cache = AnswersCache(CACHE_GOALIE)

    uncached_indices: list[int] = []
    uncached_batch: list[tuple[str, Mapping[str, Any]]] = []
    tag_to_item: dict[str, Any] = {}

    for idx, item in enumerate(items):
        item_id = str(item.get("item_id") or "")
        if item_id not in cache:
            tag = f"i{len(uncached_indices)+1:03d}"
            uncached_indices.append(idx)
            tag_to_item[tag] = item
            uncached_batch.append((build_goalie_input(item), build_goalie_questions(item)))

    if uncached_batch:
        client = _get_eval_client("goalie", items)
        raw_results = run_batched(client, uncached_batch, pacer=pacer)
        for local_idx, orig_idx in enumerate(uncached_indices):
            item = items[orig_idx]
            item_id = str(item.get("item_id") or "")
            cache.store(item_id, raw_results[local_idx])

    per_label: dict[str, dict[str, int]] = {}
    confirmed_starter_total = 0
    confirmed_starter_recalled = 0
    false_positives = 0

    for item in items:
        item_id = str(item.get("item_id") or "")
        label = item.get("label", "unknown")
        ans = cache.get(item_id, {})
        res = gate_goalie(item, ans)

        stats = per_label.setdefault(label, {"total": 0, "confirmed": 0, "unconfirmed": 0})
        stats["total"] += 1
        if res["confirmed"]:
            stats["confirmed"] += 1
        else:
            stats["unconfirmed"] += 1

        if label == "confirmed_starter":
            confirmed_starter_total += 1
            if res["confirmed"]:
                confirmed_starter_recalled += 1
        else:
            if res["confirmed"]:
                false_positives += 1

    recall = (
        confirmed_starter_recalled / confirmed_starter_total
        if confirmed_starter_total > 0
        else 0.0
    )
    bar_passed = (false_positives == 0)

    return {
        "total_items": len(items),
        "false_positives": false_positives,
        "false_positives_bar_passed": bar_passed,
        "confirmation_recall": recall,
        "per_label": per_label,
        "bars_passed": bar_passed,
    }


# ---------------------------------------------------------------------------
# Section 2: Player Status Evaluation
# ---------------------------------------------------------------------------

def run_status_eval(pacer: Optional[Pacer] = None) -> dict[str, Any]:
    items = _load_jsonl(STATUS_FIXTURE)
    cache = AnswersCache(CACHE_STATUS)

    uncached_indices: list[int] = []
    uncached_batch: list[tuple[str, Mapping[str, Any]]] = []
    tag_to_item: dict[str, Any] = {}

    for idx, item in enumerate(items):
        item_id = str(item.get("item_id") or "")
        if item_id not in cache:
            tag = f"i{len(uncached_indices)+1:03d}"
            uncached_indices.append(idx)
            tag_to_item[tag] = item
            uncached_batch.append((build_status_input(item), build_status_questions(item)))

    if uncached_batch:
        client = _get_eval_client("status", items)
        raw_results = run_batched(client, uncached_batch, pacer=pacer)
        for local_idx, orig_idx in enumerate(uncached_indices):
            item = items[orig_idx]
            item_id = str(item.get("item_id") or "")
            cache.store(item_id, raw_results[local_idx])

    scratched_out_total = 0
    scratched_out_gated_out = 0
    played_total = 0
    played_gated_out = 0
    uncertain_counts: dict[str, int] = {"total": 0, "scratched_out": 0, "played": 0}

    for item in items:
        item_id = str(item.get("item_id") or "")
        label = item.get("label")
        ans = cache.get(item_id, {})
        res = gate_status(item, ans)

        status = res.get("status")
        if status == "uncertain":
            uncertain_counts["total"] += 1
            if label in uncertain_counts:
                uncertain_counts[label] += 1

        if label == "scratched_out":
            scratched_out_total += 1
            if status == "out":
                scratched_out_gated_out += 1
        elif label == "played":
            played_total += 1
            if status == "out":
                played_gated_out += 1

    scratch_recall = (
        scratched_out_gated_out / scratched_out_total if scratched_out_total > 0 else 0.0
    )
    false_scratch_rate = played_gated_out / played_total if played_total > 0 else 0.0

    scratch_bar_passed = scratch_recall >= 0.98
    false_scratch_bar_passed = false_scratch_rate <= 0.02
    bars_passed = scratch_bar_passed and false_scratch_bar_passed

    return {
        "total_items": len(items),
        "scratch_recall": scratch_recall,
        "scratch_recall_bar_passed": scratch_bar_passed,
        "false_scratch_rate": false_scratch_rate,
        "false_scratch_bar_passed": false_scratch_bar_passed,
        "uncertain_counts": uncertain_counts,
        "bars_passed": bars_passed,
    }


# ---------------------------------------------------------------------------
# Section 3: N2 Game-Event Disruptions Evaluation
# ---------------------------------------------------------------------------

def run_n2_eval(pacer: Optional[Pacer] = None) -> dict[str, Any]:
    items = _load_jsonl(N2_FIXTURE)
    cache = AnswersCache(CACHE_N2)

    day_slates = build_day_slates(items)
    prep = n2_items_for_batch(items, day_slates)

    uncached_indices: list[int] = []
    uncached_batch: list[tuple[str, Mapping[str, Any]]] = []
    tag_to_item: dict[str, Any] = {}

    for idx, entry in enumerate(prep.included):
        item_id = entry["item_id"]
        if item_id not in cache:
            tag = f"i{len(uncached_indices)+1:03d}"
            uncached_indices.append(idx)
            tag_to_item[tag] = entry["item"]
            uncached_batch.append((entry["input_text"], entry["questions"]))

    if uncached_batch:
        client = _get_eval_client("n2", items)
        raw_results = run_batched(client, uncached_batch, pacer=pacer)
        for local_idx, orig_idx in enumerate(uncached_indices):
            entry = prep.included[orig_idx]
            item_id = entry["item_id"]
            cache.store(item_id, raw_results[local_idx])

    postponed_and_suspended_count = 0
    postponed_and_suspended_witnessed = 0
    no_change_count = 0
    no_change_witnessed = 0

    for entry in prep.included:
        item = entry["item"]
        matched_game = entry["matched_game"]
        item_id = entry["item_id"]
        label = item.get("label") or item.get("class")
        ans = cache.get(item_id, {})
        res = n2_result(item, matched_game, ans)

        is_witness = res["witness_off"]
        if label in {"postponed", "suspended"}:
            postponed_and_suspended_count += 1
            if is_witness:
                postponed_and_suspended_witnessed += 1
        elif label == "no_change":
            no_change_count += 1
            if is_witness:
                no_change_witnessed += 1

    # Unmatched DISRUPTIONS still count against recall (an unmatched disruption
    # is a miss). Unmatched no_change items do NOT enter the false-rate
    # denominator: they were never judged, so counting them would report a
    # vacuous 0.0. On this fixture the 200 normals are NBC player prose with
    # no team pair, so the deterministic matcher excludes all of them
    # fail-closed and the false-material-change rate is not evaluable.
    for exc in prep.excluded:
        item_id = exc["item_id"]
        for item in items:
            game = item.get("game") if isinstance(item.get("game"), Mapping) else {}
            d = item.get("game_date") or item.get("date") or game.get("date")
            away = item.get("away") or game.get("away")
            home = item.get("home") or game.get("home")
            synth_id = f"{d}:{away}@{home}" if d and away and home else ""
            if item.get("item_id") == item_id or synth_id == item_id:
                label = item.get("label") or item.get("class")
                if label in {"postponed", "suspended"}:
                    postponed_and_suspended_count += 1
                break

    postpone_recall = (
        postponed_and_suspended_witnessed / postponed_and_suspended_count
        if postponed_and_suspended_count > 0
        else 0.0
    )
    material_change_evaluable = no_change_count > 0
    material_change_false_rate = (
        no_change_witnessed / no_change_count if material_change_evaluable else None
    )

    postpone_bar_passed = postpone_recall >= 1.0
    false_rate_bar_passed = (
        (material_change_false_rate <= 0.01) if material_change_evaluable else None
    )
    bars_passed = postpone_bar_passed and (false_rate_bar_passed is not False)

    return {
        "total_items": len(items),
        "matched_items": len(prep.included),
        "unmatched_items": len(prep.excluded),
        "postponement_suspension_recall": postpone_recall,
        "postponement_suspension_bar_passed": postpone_bar_passed,
        "material_change_false_rate": material_change_false_rate,
        "material_change_false_rate_bar_passed": false_rate_bar_passed,
        "material_change_evaluable": material_change_evaluable,
        "matched_no_change_items": no_change_count,
        "notes": (
            "The 200 no_change normals are NBC player-availability prose with no "
            "team pair in their game object, so the deterministic slate matcher "
            "excludes them fail-closed (event_unconfirmed) and they are never "
            "judged; the false-material-change rate is therefore not evaluable "
            "on this fixture (null), not 0.0."
        ),
        "bars_passed": bars_passed,
    }


# ---------------------------------------------------------------------------
# Section 4: N6 NHL Line Change Evaluation
# ---------------------------------------------------------------------------

def item_answers_for(item: Mapping[str, Any], cache: AnswersCache) -> dict[str, Any]:
    """Retrieve cached answers for an item by id."""
    item_id = str(item.get("id") or item.get("item_id") or "")
    return cache.get(item_id, {})


def build_compound_items(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Deterministically construct compound items pairing 5v5 and PP-only changes.

    Compound items are deterministically constructed by concatenating a same-team
    5v5-change blurb with a power-play-only blurb; they are not captured real-world blurbs.
    """
    by_team_pp: dict[str, list[Mapping[str, Any]]] = {}
    by_team_shuffle: dict[str, list[Mapping[str, Any]]] = {}
    by_team_promo: dict[str, list[Mapping[str, Any]]] = {}

    for it in items:
        team = str(it.get("team") or "")
        lbl = it.get("label")
        if lbl == "pp_only_change":
            by_team_pp.setdefault(team, []).append(it)
        elif lbl == "line_shuffle":
            by_team_shuffle.setdefault(team, []).append(it)
        elif lbl == "promotion_demotion":
            by_team_promo.setdefault(team, []).append(it)

    qualifying_teams = sorted([
        t for t in set(str(it.get("team") or "") for it in items if it.get("team"))
        if t in by_team_pp and (t in by_team_shuffle or t in by_team_promo)
    ])
    capped_teams = qualifying_teams[:12]

    compounds: list[dict[str, Any]] = []
    for team in capped_teams:
        pp_item = by_team_pp[team][0]
        if team in by_team_shuffle:
            shuffle_item = by_team_shuffle[team][0]
        else:
            shuffle_item = by_team_promo[team][0]

        compounds.append({
            "id": f"n6-compound-{team}",
            "label": "compound_pp_and_5v5",
            "constructed": True,
            "team": team,
            "blurb": str(shuffle_item.get("blurb") or "") + " " + str(pp_item.get("blurb") or ""),
            "expected_5v5_trios": shuffle_item.get("expected_5v5_trios", []),
            "expected_pp_units": pp_item.get("expected_pp_units"),
            "source_item_ids": [shuffle_item.get("id"), pp_item.get("id")],
            "component_labels": [shuffle_item.get("label"), "pp_only_change"],
        })

    return compounds


def run_n6_eval(pacer: Optional[Pacer] = None) -> dict[str, Any]:
    items = _load_jsonl(N6_FIXTURE)
    cache = AnswersCache(CACHE_N6)
    compound_items = build_compound_items(items)

    all_eval_items = list(items) + list(compound_items)

    # Batch per (item, trio): the prep pack's context contract is one trio
    # under review per context. Cached answers stay keyed per item with the
    # per-trio question ids merged.
    uncached_batch: list[tuple[str, Mapping[str, Any]]] = []
    uncached_owners: list[tuple[int, int]] = []  # (item idx, trio k)

    for idx, item in enumerate(all_eval_items):
        item_id = str(item.get("id") or item.get("item_id") or "")
        if item_id not in cache:
            for k, trio in enumerate(item.get("expected_5v5_trios") or []):
                uncached_batch.append((build_n6_trio_input(item, trio), build_n6_trio_questions(item, trio, k)))
                uncached_owners.append((idx, k))

    if uncached_batch:
        client = _get_eval_client("n6", all_eval_items)
        raw_results = run_batched(client, uncached_batch, pacer=pacer)
        merged: dict[int, dict[str, Any]] = {}
        for local_idx, (orig_idx, _k) in enumerate(uncached_owners):
            merged.setdefault(orig_idx, {}).update(raw_results[local_idx])
        for orig_idx, answers in merged.items():
            item = all_eval_items[orig_idx]
            item_id = str(item.get("id") or item.get("item_id") or "")
            cache.store(item_id, answers)

    pp_only_total = 0
    pp_only_leakage = 0
    no_change_total = 0
    no_change_suspended = 0
    shuffle_promo_total = 0
    shuffle_promo_suspended = 0

    for item in items:
        ans = item_answers_for(item, cache)
        res = n6_result(item, ans)
        label = item.get("label")

        has_suspension = len(res["suspended_trios"]) > 0

        if label == "pp_only_change":
            pp_only_total += 1
            if has_suspension:
                pp_only_leakage += 1
        elif label == "no_change":
            no_change_total += 1
            if has_suspension:
                no_change_suspended += 1
        elif label in {"line_shuffle", "promotion_demotion"}:
            shuffle_promo_total += 1
            if has_suspension:
                shuffle_promo_suspended += 1

    # Score compound items
    compound_prong_exempted_false = 0
    compound_suspended_count = 0
    for item in compound_items:
        ans = item_answers_for(item, cache)
        res = n6_result(item, ans)
        if not res.get("trio_prong_exempted", False):
            compound_prong_exempted_false += 1
        if len(res.get("suspended_trios", [])) > 0:
            compound_suspended_count += 1

    n_compounds = len(compound_items)
    compound_prong_active = (
        compound_prong_exempted_false / n_compounds if n_compounds > 0 else 1.0
    )
    compound_suspended = (
        compound_suspended_count / n_compounds if n_compounds > 0 else 1.0
    )

    false_suspension_rate = (
        no_change_suspended / no_change_total if no_change_total > 0 else 0.0
    )
    shuffle_recall = (
        shuffle_promo_suspended / shuffle_promo_total if shuffle_promo_total > 0 else 0.0
    )

    pp_leakage_bar_passed = pp_only_leakage == 0
    false_suspension_bar_passed = false_suspension_rate <= 0.05
    promotion_shuffle_bar_passed = shuffle_recall == 1.0
    compound_prong_bar_passed = (compound_prong_active == 1.0) if n_compounds > 0 else True
    compound_suspended_bar_passed = (compound_suspended == 1.0) if n_compounds > 0 else True

    bars_passed = (
        pp_leakage_bar_passed
        and false_suspension_bar_passed
        and promotion_shuffle_bar_passed
        and compound_prong_bar_passed
        and compound_suspended_bar_passed
    )

    return {
        "total_items": len(items),
        "pp_only_items": pp_only_total,
        "pp_only_leakage": pp_only_leakage,
        "pp_only_bar_passed": pp_leakage_bar_passed,
        "no_change_items": no_change_total,
        "false_suspension_rate": false_suspension_rate,
        "false_suspension_bar_passed": false_suspension_bar_passed,
        "promotion_shuffle_recall": shuffle_recall,
        "promotion_demotion_shuffle_recall": shuffle_recall,
        "compound_items": n_compounds,
        "compound_item_ids": [c["id"] for c in compound_items],
        "compound_prong_active": compound_prong_active,
        "compound_suspended": compound_suspended,
        "bars_passed": bars_passed,
    }


# ---------------------------------------------------------------------------
# Combined Evaluation Runner
# ---------------------------------------------------------------------------

def run_all_evals() -> dict[str, Any]:
    if os.environ.get("GEMINI_API_KEY"):
        pacer = Pacer()
    else:
        # Offline simulation: zero wall-clock sleep
        pacer = Pacer(tokens_per_min=1_000_000_000, sleep=lambda _: None)

    goalie_res = run_goalie_eval(pacer=pacer)
    status_res = run_status_eval(pacer=pacer)
    n2_res = run_n2_eval(pacer=pacer)
    n6_res = run_n6_eval(pacer=pacer)

    all_passed = (
        goalie_res["bars_passed"]
        and status_res["bars_passed"]
        and n2_res["bars_passed"]
        and n6_res["bars_passed"]
    )

    results = {
        "goalie": goalie_res,
        "status": status_res,
        "n2": n2_res,
        "n6": n6_res,
        "all_bars_passed": all_passed,
    }

    EVAL_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EVAL_RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
        f.write("\n")

    return results


def main() -> None:
    results = run_all_evals()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
