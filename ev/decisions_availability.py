"""Player Availability Classifier (Decisions-API Build #2).

Merged availability route folding:
- Item 2 Goalie availability (NHL starting goaltender verification)
- Item 3 Status classifier (NBA player injury/availability status)
- Section N2 event gate (game-day event disruptions: postponements, suspensions)
- Section N6 NHL line-change prose verifier (5v5 line shuffle / promotion / demotion)

Into a single batched Decisions API call per run.

DOCTRINE:
- Exactly ONE Decisions API call across all four groups per job run.
- FAIL CLOSED on abstention, timeout, HTTP error, or missing evidence.
- Isolation invariant: N6 NHL line verifier is ALWAYS shadow (applied=False,
  n6_shadow=True) regardless of route mode, and pp_only_change NEVER suspends 5v5 lines.
- Safe token budgeting: enforce 90% ceiling of MAX_INPUT_TOKENS.
- Goalie confirmation rule: confirmed = (rotowire_status == 'Confirmed') AND (noul >= 0.90).
  The noul NEVER modifies any saves projection; it only gates usability.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Optional, Union

from ev.decisions_client import (
    BatchResult,
    DecisionsClient,
    DecisionsConfigError,
    ItemBatch,
    choice_pick,
    noul_value,
    probabilities,
)
from ev.decisions_common import (
    MAX_INPUT_TOKENS,
    append_records,
    estimate_tokens,
    make_record,
    route_mode,
)
from ev.decisions_prep import n2_event_gate as prep_n2
from ev.decisions_prep import n6_line_verifier as prep_n6

# Constants
GOALIE_NOUL_MIN: float = 0.90
STATUS_OUT_P: float = 0.80
STATUS_QUESTIONABLE_P: float = 0.40
STATUS_CHANGED_P: float = 0.35


# ---------------------------------------------------------------------------
# Item 2 Goalie questions & gating
# ---------------------------------------------------------------------------

def build_goalie_questions(item: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Build the goalie Decisions question map."""
    goalie = item.get("goalie") or "the named goalie"
    return {
        "goalie_starts": {
            "type": "noul",
            "instructions": (
                f"Give the probability (noul) that the named goalie ({goalie}) ACTUALLY STARTS "
                "this game for his team, judged from the paired RotoWire status note and the "
                "ESPN blurb in the input block; explicit confirmation language means high; "
                "projected/expected/hedged language means low; any later update ruling the goalie "
                "out, scratching him, or giving the start to another goaltender means near 0."
            ),
        }
    }


def build_goalie_input(item: Mapping[str, Any]) -> str:
    """Build the goalie context input block."""
    lines = [
        f"Goalie: {item.get('goalie', '')}",
        f"Team: {item.get('team', '')}",
        f"Opponent: {item.get('opponent', '')}",
        f"Date: {item.get('game_date') or item.get('date', '')}",
        f"RotoWire status: {item.get('rotowire_status', '')}",
    ]
    if item.get("rotowire_note"):
        lines.append(f"RotoWire note: {item['rotowire_note']}")
    if item.get("espn_blurb"):
        lines.append(f"ESPN blurb: {item['espn_blurb']}")
    return "\n".join(lines)


def gate_goalie(item: Mapping[str, Any], answers: Mapping[str, Any]) -> dict[str, Any]:
    """Gate goalie usability based on RotoWire status and Decisions noul.

    The noul NEVER modifies any saves projection; it only gates usability.
    """
    raw_answer = answers.get("goalie_starts") if isinstance(answers, Mapping) and "goalie_starts" in answers else answers
    p = DecisionsClient.noul_value(raw_answer)
    status = str(item.get("rotowire_status") or "").strip()
    confirmed = (status == "Confirmed") and (p is not None and p >= GOALIE_NOUL_MIN)

    if status != "Confirmed":
        reason = "status_not_confirmed"
    elif p is None:
        reason = "noul_missing"
    elif p < GOALIE_NOUL_MIN:
        reason = "noul_below_threshold"
    else:
        reason = "confirmed"

    return {
        "goalie": item.get("goalie"),
        "team": item.get("team"),
        "confirmed": confirmed,
        "noul": p,
        "fail_closed": not confirmed,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Item 3 Player status questions & gating
# ---------------------------------------------------------------------------

def build_status_questions(item: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Build the status classification question map."""
    player = item.get("player") or "the player"
    return {
        "availability_status": {
            "type": "choice",
            "instructions": (
                f"Classify the player's ({player}) availability for the game described in the "
                "input block into exactly one option."
            ),
            "criteria": {
                "confirmed": "the text confirms the player will play (upgraded to available, will play, will suit up)",
                "questionable": "the text leaves the player a game-time decision with no resolution",
                "scratched_out": "the text rules the player out, scratched, downgraded to out, or otherwise not playing",
                "no_change": "the text reports no availability change from the player's normal status",
            },
        }
    }


def build_status_input(item: Mapping[str, Any]) -> str:
    """Build the player status context input block."""
    lines = [
        f"Player: {item.get('player', '')}",
        f"Team: {item.get('team', '')}",
        f"Matchup: {item.get('matchup', '')}",
        f"Date: {item.get('game_date') or item.get('date', '')}",
        f"Official designation at decision time: {item.get('designation_at_decision', '')}",
    ]
    if item.get("reason"):
        lines.append(f"Reason: {item['reason']}")
    if item.get("blurb_text"):
        lines.append(f"Report: {item['blurb_text']}")
    return "\n".join(lines)


def gate_status(item: Mapping[str, Any], answers: Mapping[str, Any]) -> dict[str, Any]:
    """Gate player availability status based on Decisions probabilities distribution."""
    raw = answers.get("availability_status") if isinstance(answers, Mapping) and "availability_status" in answers else answers
    probs = DecisionsClient.probabilities(raw) or {}
    player = str(item.get("player") or "Player")

    if not probs:
        return {
            "player": player,
            "status": "uncertain",
            "blocked_card": True,
            "reason": "abstained",
        }

    p_scratch = probs.get("scratched_out", 0.0)
    p_quest = probs.get("questionable", 0.0)
    p_no_change = probs.get("no_change", 0.0)

    if p_scratch >= STATUS_OUT_P:
        return {
            "player": player,
            "status": "out",
            "blocked_card": True,
            "suppression": f"{player} OUT (Decisions P(scratched_out)={p_scratch:.2f})",
            "probabilities": probs,
        }
    elif p_quest >= STATUS_QUESTIONABLE_P or (1.0 - p_no_change) >= STATUS_CHANGED_P:
        return {
            "player": player,
            "status": "uncertain",
            "blocked_card": True,
            "reason": "status_uncertain",
            "probabilities": probs,
        }
    else:
        return {
            "player": player,
            "status": "available",
            "blocked_card": False,
            "probabilities": probs,
        }


# ---------------------------------------------------------------------------
# Section N2 adapter: event disruptions
# ---------------------------------------------------------------------------

class N2BatchPrep(NamedTuple):
    included: list[dict[str, Any]]
    excluded: list[dict[str, Any]]


def build_day_slates(items: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group items by game_date, dedupe games by (away, home), game_id = f"{game_date}:{away}@{home}"."""
    slates: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        game = item.get("game") if isinstance(item.get("game"), Mapping) else {}
        away = item.get("away") or game.get("away")
        home = item.get("home") or game.get("home")
        d = item.get("game_date") or item.get("date") or game.get("date")
        norm_d = prep_n2.normalize_date(d)
        if not norm_d or not away or not home:
            continue
        key = (norm_d, away, home)
        if key in seen:
            continue
        seen.add(key)
        game_id = f"{norm_d}:{away}@{home}"
        entry = {
            "game_id": game_id,
            "away": away,
            "home": home,
            "date": norm_d,
            "league": item.get("league", ""),
        }
        slates.setdefault(norm_d, []).append(entry)
    return slates


def n2_input_text(item: Mapping[str, Any], matched_game: Mapping[str, Any]) -> str:
    """Decision context block for an N2 event item."""
    if hasattr(prep_n2, "input_text") and callable(getattr(prep_n2, "input_text")):
        return prep_n2.input_text(item, matched_game)
    game = item.get("game") if isinstance(item.get("game"), Mapping) else {}
    away = matched_game.get("away") or item.get("away") or game.get("away") or "the visiting team"
    home = matched_game.get("home") or item.get("home") or game.get("home") or "the home team"
    d = matched_game.get("date") or item.get("game_date") or item.get("date") or game.get("date") or "its scheduled date"
    league = item.get("league") or matched_game.get("league") or ""
    blurb = str(item.get("blurb_text") or item.get("prose_text") or "").strip()
    matchup = f"{away} at {home} on {d}" + (f" ({league})" if league else "")
    lines = [f"Game: {matchup}."]
    if blurb:
        lines.append(f"Report: {blurb}")
    return "\n".join(lines)


def n2_items_for_batch(
    items: Iterable[Mapping[str, Any]],
    day_slates: Any,
) -> N2BatchPrep:
    """Match N2 items against slates. Unmatched or ambiguous items are excluded (fail closed)."""
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for item in items:
        game = item.get("game") if isinstance(item.get("game"), Mapping) else {}
        away = item.get("away") or game.get("away") or ""
        home = item.get("home") or game.get("home") or ""
        d = item.get("game_date") or item.get("date") or game.get("date") or ""
        norm_d = prep_n2.normalize_date(d)
        item_id = str(item.get("item_id") or item.get("id") or (f"{norm_d}:{away}@{home}" if norm_d and away and home else f"n2_{len(included)+len(excluded)}"))

        # Slate extraction
        if day_slates is None:
            slate = []
        elif isinstance(day_slates, Mapping):
            slate = list(day_slates.get(norm_d, [])) if norm_d else []
            if not slate and day_slates:
                flat: list[dict[str, Any]] = []
                for val in day_slates.values():
                    if isinstance(val, Iterable) and not isinstance(val, (str, Mapping)):
                        flat.extend(val)
                    elif isinstance(val, Mapping):
                        flat.append(val)
                slate = flat
        elif isinstance(day_slates, Iterable):
            slate = list(day_slates)
        else:
            slate = []

        matches = []
        for entry in slate:
            if isinstance(entry, Mapping) and prep_n2.match_event(item, [entry]) is not None:
                matches.append(entry)

        if len(matches) == 0:
            matched = None
        elif len(matches) > 1:
            matched = {"ambiguous": True}
        else:
            matched = matches[0]

        if matched is None or (isinstance(matched, Mapping) and matched.get("ambiguous")):
            excluded.append({
                "item_id": item_id,
                "matched": False,
                "witness_off": None,
                "disposition": "event_unconfirmed",
            })
        else:
            input_block = n2_input_text(item, matched)
            questions = prep_n2.build_questions(item)
            included.append({
                "item": item,
                "matched_game": matched,
                "item_id": item_id,
                "input_text": input_block,
                "questions": questions,
            })

    return N2BatchPrep(included=included, excluded=excluded)


def n2_result(
    item: Mapping[str, Any],
    matched_game: Mapping[str, Any],
    answers: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply prep gate to N2 answer and return structured result."""
    item_id = str(item.get("item_id") or item.get("id") or matched_game.get("game_id") or "")
    ans = dict(answers) if isinstance(answers, Mapping) else {}

    if "witness_off" in ans:
        val = DecisionsClient.noul_value(ans["witness_off"])
        ans["witness_off"] = val
        if "material_change" not in ans:
            ans["material_change"] = val
    if "material_change" in ans and "witness_off" not in ans:
        val = DecisionsClient.noul_value(ans["material_change"])
        ans["witness_off"] = val

    gate_res = prep_n2.gate(ans)
    is_witness = (gate_res.get("action") == "witness_off")
    disposition = "witness_off" if is_witness else "confirmed"

    return {
        "item_id": item_id,
        "matched": True,
        "matched_game_id": matched_game.get("game_id"),
        "witness_off": is_witness,
        "disposition": disposition,
        "reason": gate_res.get("reason"),
        "gate": gate_res,
    }


# ---------------------------------------------------------------------------
# Section N6 adapter: NHL line changes
# ---------------------------------------------------------------------------

def n6_team_context(item: Mapping[str, Any]) -> dict[str, Any]:
    """Build the prep-shaped N6 team context mapping.

    Keys follow ev.decisions_prep.n6_line_verifier.build_context's contract:
    team, game_date, game_id, expected_5v5_trios, blurbs.
    """
    blurb = item.get("blurb", "")
    return {
        "team": item.get("team"),
        "game_id": item.get("game_id"),
        "game_date": item.get("game_date") or item.get("date"),
        "date": item.get("game_date") or item.get("date"),
        "blurb": blurb,
        "blurbs": [blurb] if blurb else [],
        "expected_5v5_trios": item.get("expected_5v5_trios", []),
        "trio_sources": item.get("expected_5v5_trios", []),
        "expected_pp_units": item.get("expected_pp_units"),
        "pp_units": item.get("expected_pp_units"),
    }


def build_n6_trio_input(item: Mapping[str, Any], trio: Any) -> str:
    """Per-trio context via the prep pack's own build_context (its contract:
    one trio under review per context)."""
    ctx = n6_team_context(item)
    trio_names = trio.split(" + ") if isinstance(trio, str) else prep_n6._trio_names(trio)
    return prep_n6.build_context(ctx, trio_names)


def build_n6_trio_questions(item: Mapping[str, Any], trio: Any, k: int) -> dict[str, dict[str, Any]]:
    """Per-trio questions: the prep pack's two questions, renamed per trio index
    so several trios can share one batched call."""
    ctx = n6_team_context(item)
    trio_names = trio.split(" + ") if isinstance(trio, str) else prep_n6._trio_names(trio)
    prep_qs = prep_n6.build_questions(ctx, trio_names)
    return {
        f"n6_trio_intact_{k}": copy.deepcopy(prep_qs[prep_n6.TRIO_QUESTION_ID]),
        f"n6_change_kind_{k}": copy.deepcopy(prep_qs[prep_n6.CLASS_QUESTION_ID]),
    }


def build_n6_input(item: Mapping[str, Any]) -> str:
    """Build context text for an N6 item across all expected trios."""
    ctx = n6_team_context(item)
    team = str(ctx.get("team") or "")
    lines = [f"Team: {team}"]
    if ctx.get("date"):
        lines.append(f"Game date: {ctx['date']}")
    if ctx.get("game_id"):
        lines.append(f"Game id: {ctx['game_id']}")

    trios = item.get("expected_5v5_trios") or []
    if trios:
        lines.append("")
        lines.append("Expected 5v5 forward lines before this game:")
        for idx, t in enumerate(trios, 1):
            t_str = t if isinstance(t, str) else ", ".join(prep_n6._trio_names(t))
            lines.append(f"  F{idx}: {t_str}")

    lines.append("")
    lines.append("Pre-game prose for this team (morning-skate / lineup reports), unedited:")
    blurb = item.get("blurb", "")
    lines.append(blurb if blurb else "(no pre-game prose available)")
    return "\n".join(lines)


def build_n6_questions(item: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Build the N6 question map per expected trio k."""
    team_ctx = n6_team_context(item)
    trios = item.get("expected_5v5_trios") or []
    questions: dict[str, dict[str, Any]] = {}

    for k, trio in enumerate(trios):
        trio_names = trio.split(" + ") if isinstance(trio, str) else prep_n6._trio_names(trio)
        prep_qs = prep_n6.build_questions(team_ctx, trio_names)

        # Trio intact (lineup_claim)
        trio_q = prep_qs.get(prep_n6.TRIO_QUESTION_ID) or prep_qs.get("lineup_claim")
        if trio_q:
            questions[f"n6_trio_intact_{k}"] = copy.deepcopy(trio_q)
        else:
            questions[f"n6_trio_intact_{k}"] = {
                "type": "noul",
                "instructions": f"Probability that trio {trio} skates together as expected.",
            }

        # Line change kind (lineup_change_kind)
        class_q = prep_qs.get(prep_n6.CLASS_QUESTION_ID) or prep_qs.get("lineup_change_kind")
        if class_q:
            questions[f"n6_change_kind_{k}"] = copy.deepcopy(class_q)
        else:
            questions[f"n6_change_kind_{k}"] = {
                "type": "choice",
                "instructions": "Classify reported line change.",
                "criteria": dict(prep_n6._CRITERIA),
            }

        # Shift share plausible
        shift_q = prep_qs.get("shift_share_plausible")
        if shift_q:
            questions[f"n6_shift_plausible_{k}"] = copy.deepcopy(shift_q)
        else:
            questions[f"n6_shift_plausible_{k}"] = {
                "type": "noul",
                "instructions": f"Probability that trio {trio} skates a plausible share of even-strength shifts.",
            }

    return questions


def n6_result(
    item: Mapping[str, Any],
    answers: Mapping[str, Any],
    mode: str = "shadow",
    abstained: bool = False,
) -> dict[str, Any]:
    """Process answers for an N6 item and return shadow-only result."""
    item_id = str(item.get("id") or item.get("item_id") or "")
    trios = item.get("expected_5v5_trios") or []

    if abstained or not answers:
        for trio in trios:
            trio_names = trio.split(" + ") if isinstance(trio, str) else prep_n6._trio_names(trio)
            prep_n6.gate({}, trio_names)
        return {
            "id": item.get("id"),
            "item_id": item_id,
            "team": item.get("team"),
            "game_id": item.get("game_id"),
            "date": item.get("game_date") or item.get("date"),
            "n6_shadow": True,
            "applied": False,
            "mode": mode,
            "suspended_trios": [],
            "disposition": "shadow_abstained",
        }

    suspended_trios: list[str] = []
    for k, trio in enumerate(trios):
        trio_names = trio.split(" + ") if isinstance(trio, str) else prep_n6._trio_names(trio)
        ans_change = answers.get(f"n6_change_kind_{k}")
        ans_trio = answers.get(f"n6_trio_intact_{k}")
        ans_shift = answers.get(f"n6_shift_plausible_{k}")

        trio_ans = {
            prep_n6.CLASS_QUESTION_ID: ans_change,
            prep_n6.TRIO_QUESTION_ID: ans_trio,
            "lineup_change_kind": ans_change,
            "lineup_claim": ans_trio,
            "shift_share_plausible": ans_shift,
        }
        gate_res = prep_n6.gate(trio_ans, trio_names)
        if gate_res.get("suspend"):
            suspended_trios.append(trio if isinstance(trio, str) else " + ".join(trio_names))

    disposition = "shadow_suspend" if suspended_trios else "shadow_ok"
    return {
        "id": item.get("id"),
        "item_id": item_id,
        "team": item.get("team"),
        "game_id": item.get("game_id"),
        "date": item.get("game_date") or item.get("date"),
        "n6_shadow": True,
        "applied": False,
        "mode": mode,
        "suspended_trios": suspended_trios,
        "disposition": disposition,
    }


# ---------------------------------------------------------------------------
# Core Batched Availability Classifier
# ---------------------------------------------------------------------------

def classify_availability(
    *,
    goalies: Sequence[Mapping[str, Any]] = (),
    statuses: Sequence[Mapping[str, Any]] = (),
    n2_items: Sequence[Mapping[str, Any]] = (),
    day_slates: Any = None,
    n6_items: Sequence[Mapping[str, Any]] = (),
    client: Optional[Any] = None,
    mode: Optional[str] = None,
    log_path: Optional[Union[str, os.PathLike[str]]] = None,
) -> dict[str, Any]:
    """Single batched Decisions API call across all availability groups.

    Folds Goalies, Statuses, N2 game-event disruptions, and N6 NHL line verifications
    into ONE batched call. Fail closed on abstentions. N6 is forced shadow.
    """
    op_mode = mode or route_mode("DECISIONS_AVAILABILITY_MODE")
    is_live = (op_mode == "live")

    # Client construction handling
    client_failed = False
    if client is None:
        try:
            client = DecisionsClient()
        except Exception:
            client = None
            client_failed = True

    # Empty payload handling
    if not goalies and not statuses and not n2_items and not n6_items:
        return {
            "goalies": [],
            "statuses": [],
            "events": [],
            "lines": [],
            "mode": op_mode,
        }

    # N2 pre-batch matching and filter
    n2_prep = n2_items_for_batch(n2_items, day_slates)
    excluded_events: list[dict[str, Any]] = []
    for exc in n2_prep.excluded:
        item_copy = dict(exc)
        item_copy["mode"] = op_mode
        item_copy["applied"] = is_live
        excluded_events.append(item_copy)

    # Build the single combined ItemBatch
    batch = ItemBatch()
    goalie_map: list[tuple[str, Mapping[str, Any]]] = []
    status_map: list[tuple[str, Mapping[str, Any]]] = []
    event_map: list[tuple[str, dict[str, Any]]] = []
    n6_map: list[tuple[str, Mapping[str, Any]]] = []

    for idx, g_item in enumerate(goalies):
        tag = f"g{idx:04d}"
        batch.add(build_goalie_input(g_item), build_goalie_questions(g_item), tag=tag)
        goalie_map.append((tag, g_item))

    for idx, s_item in enumerate(statuses):
        tag = f"s{idx:04d}"
        batch.add(build_status_input(s_item), build_status_questions(s_item), tag=tag)
        status_map.append((tag, s_item))

    for idx, n2_entry in enumerate(n2_prep.included):
        tag = f"e{idx:04d}"
        batch.add(n2_entry["input_text"], n2_entry["questions"], tag=tag)
        event_map.append((tag, n2_entry))

    for idx, l_item in enumerate(n6_items):
        trios = l_item.get("expected_5v5_trios") or []
        tags_for_item: list[str] = []
        for k, trio in enumerate(trios):
            tag = f"l{idx:04d}t{k}"
            batch.add(build_n6_trio_input(l_item, trio), build_n6_trio_questions(l_item, trio, k), tag=tag)
            tags_for_item.append(tag)
        n6_map.append((tags_for_item, l_item))

    # Token estimation check
    input_text, wire_questions = batch.build()
    est = estimate_tokens(input_text)
    token_limit = int(MAX_INPUT_TOKENS * 0.9)
    if est > token_limit:
        raise ValueError(
            f"Estimated input tokens ({est}) exceeds 90% of MAX_INPUT_TOKENS ({token_limit})"
        )

    # Execute exactly ONE Decisions API call
    if client_failed:
        batch_res = BatchResult.abstain("client_construction_failed")
    elif batch.question_count > 0:
        batch_res = client.call(input_text, wire_questions)
    else:
        batch_res = BatchResult(answers={}, abstained=False)

    split_answers = batch.split(batch_res)

    # Process goalie results
    goalie_results: list[dict[str, Any]] = []
    for tag, it in goalie_map:
        item_id = str(it.get("item_id") or "")
        if batch_res.abstained:
            res = {
                "goalie": it.get("goalie"),
                "team": it.get("team"),
                "confirmed": False,
                "noul": None,
                "fail_closed": True,
                "reason": "noul_missing",
                "mode": op_mode,
                "applied": is_live,
                "item_id": item_id,
            }
        else:
            ans = split_answers.get(tag, {})
            res = gate_goalie(it, ans)
            res["mode"] = op_mode
            res["applied"] = is_live
            res["item_id"] = item_id
        goalie_results.append(res)

    # Process status results
    status_results: list[dict[str, Any]] = []
    for tag, it in status_map:
        item_id = str(it.get("item_id") or "")
        if batch_res.abstained:
            res = {
                "player": it.get("player"),
                "status": "uncertain",
                "blocked_card": True,
                "reason": "abstained",
                "mode": op_mode,
                "applied": is_live,
                "item_id": item_id,
            }
        else:
            ans = split_answers.get(tag, {})
            res = gate_status(it, ans)
            res["mode"] = op_mode
            res["applied"] = is_live
            res["item_id"] = item_id
        status_results.append(res)

    # Process event results (included + pre-batch excluded)
    event_results: list[dict[str, Any]] = []
    for tag, entry in event_map:
        it = entry["item"]
        matched_game = entry["matched_game"]
        item_id = entry["item_id"]
        if batch_res.abstained:
            res = {
                "item_id": item_id,
                "matched": True,
                "matched_game_id": matched_game.get("game_id"),
                "witness_off": None,
                "disposition": "event_unconfirmed",
                "mode": op_mode,
                "applied": is_live,
            }
        else:
            ans = split_answers.get(tag, {})
            res = n2_result(it, matched_game, ans)
            res["mode"] = op_mode
            res["applied"] = is_live
        event_results.append(res)

    event_results.extend(excluded_events)

    # Process lines (N6) results (forced shadow, applied=False always)
    line_results: list[dict[str, Any]] = []
    for tags_for_item, it in n6_map:
        merged: dict[str, Any] = {}
        for tag in tags_for_item:
            merged.update(split_answers.get(tag, {}))
        res = n6_result(it, merged, mode=op_mode, abstained=batch_res.abstained)
        line_results.append(res)

    # Decision logging
    if log_path is not None:
        records: list[dict[str, Any]] = []
        for g in goalie_results:
            records.append(
                make_record(
                    route="availability",
                    item_id=str(g["item_id"]),
                    decision="confirmed" if g["confirmed"] else "unconfirmed",
                    mode=op_mode,
                    applied=g["applied"],
                    group="goalie",
                    reason=g.get("reason"),
                    noul=g.get("noul"),
                )
            )
        for s in status_results:
            records.append(
                make_record(
                    route="availability",
                    item_id=str(s["item_id"]),
                    decision=s["status"],
                    mode=op_mode,
                    applied=s["applied"],
                    group="status",
                    blocked_card=s.get("blocked_card"),
                )
            )
        for e in event_results:
            records.append(
                make_record(
                    route="availability",
                    item_id=str(e["item_id"]),
                    decision=e["disposition"],
                    mode=op_mode,
                    applied=e["applied"],
                    group="event",
                    witness_off=e.get("witness_off"),
                )
            )
        for l in line_results:
            records.append(
                make_record(
                    route="availability",
                    item_id=str(l["item_id"]),
                    decision=l["disposition"],
                    mode=op_mode,
                    applied=l["applied"],
                    group="line",
                    suspended_trios=l.get("suspended_trios"),
                )
            )
        append_records(log_path, records)

    return {
        "goalies": goalie_results,
        "statuses": status_results,
        "events": event_results,
        "lines": line_results,
        "mode": op_mode,
    }


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def _load_jsonl(path: Union[str, Path]) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Player Availability Classifier (Decisions-API Build #2)"
    )
    parser.add_argument("--goalies", type=str, help="Path to goalie items JSONL")
    parser.add_argument("--statuses", type=str, help="Path to status items JSONL")
    parser.add_argument("--n2", type=str, help="Path to N2 event items JSONL")
    parser.add_argument("--n6", type=str, help="Path to N6 morning skate items JSONL")
    parser.add_argument("--out", type=str, help="Output JSON report file")
    parser.add_argument("--log", type=str, help="Decision records log path")
    parser.add_argument("--mode", type=str, choices=["shadow", "live"], help="Operating mode")
    args = parser.parse_args()

    goalies = _load_jsonl(args.goalies) if args.goalies else []
    statuses = _load_jsonl(args.statuses) if args.statuses else []
    n2_items = _load_jsonl(args.n2) if args.n2 else []
    n6_items = _load_jsonl(args.n6) if args.n6 else []

    day_slates = build_day_slates(n2_items) if n2_items else None

    result = classify_availability(
        goalies=goalies,
        statuses=statuses,
        n2_items=n2_items,
        day_slates=day_slates,
        n6_items=n6_items,
        mode=args.mode,
        log_path=args.log,
    )

    out_json = json.dumps(result, indent=2)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_json, encoding="utf-8")
        print(f"Report written to {args.out}")
    else:
        print(out_json)


if __name__ == "__main__":
    main()
