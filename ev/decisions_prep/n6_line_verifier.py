"""N6 question pack + suspension gate (Argon ruling, section 2 / N6).

Ruling: ~/workspace/beezy/profitability/argon-decisions-api-expansion-VERBATIM-2026-11-13.md

N6 (NHL late line-change prose verifier) is ADOPTED as a merge into
Build #2's single batched Decisions call, shadow-only until the SGP 5v5
lines artifact lands. This module is the non-API prep for that merge:
the question pack (in the merged client's published envelope style --
``questions`` MAP keyed by id, see
``nba/minutes_restriction/decisions_client.py``) and the deterministic
gate that consumes an answer. It makes NO API calls.

Gate (per the ruling, fail closed on line-mate eligibility):

    suspend line-mate eligibility iff
        P(line_shuffle) >= 0.80
        OR P(promotion_demotion) >= 0.80
        OR trio noul (P(trio intact)) < 0.80

    with the conditional class-prong exemption of Argon ruling
    RUL-20261113-dec-gate-design Q3: the trio-noul trigger is waived for
    a trio iff P(pp_only_change) >= 0.80 AND P(line_shuffle) < 0.20 AND
    P(promotion_demotion) < 0.20; otherwise the trio prong stays fully
    active and fails closed, compound blurbs included.

Isolation invariant: a ``pp_only_change`` outcome NEVER suspends 5v5
eligibility. ``P(pp_only_change)`` is never a suspension reason in
``gate`` -- power-play changes do not suppress even-strength line-mate
eligibility, even once (the ruling's kill criterion treats a single
leak as fatal).
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

LABELS: tuple[str, ...] = (
    "no_change",
    "line_shuffle",
    "promotion_demotion",
    "pp_only_change",
)

CLASS_QUESTION_ID = "n6_line_change_class"
TRIO_QUESTION_ID = "n6_trio_intact"

SUSPEND_THRESHOLD = 0.80        # P(line_shuffle) / P(promotion_demotion)
TRIO_INTACT_MIN = 0.80          # suspend when trio noul < this

_CRITERIA: dict[str, str] = {
    "no_change": (
        "The prose reports no change: the team's expected 5v5 forward "
        "lines skate as expected and no line or power-play change is "
        "reported."
    ),
    "line_shuffle": (
        "The prose reports a 5v5 forward line shuffle: even-strength "
        "trio membership changes (players re-grouped across lines) "
        "without a clear promotion or demotion between line ranks."
    ),
    "promotion_demotion": (
        "The prose reports a promotion or demotion: a forward moves "
        "between line ranks at 5v5 (for example third line to first "
        "line, or first line to fourth line), or enters/leaves the "
        "lineup in a top-six role."
    ),
    "pp_only_change": (
        "The prose reports a change confined to the power-play units: "
        "PP1/PP2 membership changes while the 5v5 forward lines are "
        "unchanged."
    ),
}


def _trio_names(trio: Any) -> list[str]:
    """Trio as a name list. Accepts a capture-trio dict ({"players": [...]}),
    a mapping with "names", or a plain sequence of names / player dicts."""
    if isinstance(trio, Mapping):
        for key in ("players", "names", "player_names"):
            val = trio.get(key)
            if val:
                return _trio_names(list(val))
        return []
    names: list[str] = []
    for item in trio or []:
        if isinstance(item, Mapping):
            names.append(str(item.get("name") or ""))
        else:
            names.append(str(item))
    return [n for n in names if n]


def build_context(team_context: Mapping[str, Any], trio: Any) -> str:
    """Decision-time context text (the envelope's shared ``input``).

    ``team_context`` keys (all optional except ``team``): game_date,
    game_id, opponent, expected_5v5_trios (capture shape), blurbs (a
    string or a sequence of strings / {"headline","body"} mappings,
    already cutoff-filtered to decision time by the caller, rendered
    unedited in order).
    """
    team = str(team_context.get("team") or "")
    names = _trio_names(trio)
    lines = [f"Team: {team}"]
    if team_context.get("game_date"):
        lines.append(f"Game date: {team_context['game_date']}")
    if team_context.get("game_id"):
        lines.append(f"Game id: {team_context['game_id']}")
    if team_context.get("opponent"):
        lines.append(f"Opponent: {team_context['opponent']}")
    expected = team_context.get("expected_5v5_trios")
    if expected:
        lines.append("")
        lines.append("Expected 5v5 forward lines before this game:")
        for t in expected:
            rank = t.get("rank") if isinstance(t, Mapping) else None
            label = f"F{rank}" if rank else "F"
            lines.append(f"  {label}: {', '.join(_trio_names(t))}")
    lines.append("")
    lines.append(
        "Trio under review (expected 5v5 line-mates): "
        + (", ".join(names) if names else "(unspecified)")
    )
    lines.append("")
    lines.append("Pre-game prose for this team (morning-skate / lineup reports), unedited:")
    blurbs = team_context.get("blurbs")
    if isinstance(blurbs, str):
        blurbs = [blurbs] if blurbs.strip() else []
    if blurbs:
        for b in blurbs:
            if isinstance(b, Mapping):
                head = str(b.get("headline") or "").strip()
                body = str(b.get("body") or "").strip()
                lines.append(f"{head}: {body}" if head else body)
            else:
                lines.append(str(b))
    else:
        lines.append("(no pre-game prose available)")
    return "\n".join(lines)


def build_questions(team_context: Mapping[str, Any], trio: Any) -> dict:
    """The N6 Decisions questions MAP for one team-game + trio.

    Envelope style per the merged client: a MAP keyed by question id; a
    ``choice`` question carries ``type`` / ``instructions`` / ``criteria``
    (option id -> description); a ``noul`` question asks for a 0..1
    probability. The shared context rides in the envelope's ``input``
    field (see ``build_context``); this function returns only the
    questions map so the caller can merge it into Build #2's single
    batched call.
    """
    team = str(team_context.get("team") or "the team")
    names = _trio_names(trio)
    trio_label = ", ".join(names) if names else "the expected trio"
    return {
        CLASS_QUESTION_ID: {
            "type": "choice",
            "instructions": (
                f"Based only on the pre-game prose in the input, which "
                f"single class best describes the reported change to "
                f"{team}'s forward lines for this game? Judge 5v5 "
                f"even-strength lines separately from power-play units: "
                f"a change confined to the power play is pp_only_change, "
                f"not a line shuffle."
            ),
            "criteria": dict(_CRITERIA),
        },
        TRIO_QUESTION_ID: {
            "type": "noul",
            "instructions": (
                f"Return the probability, from 0 to 1, that the trio "
                f"{trio_label} skates together as a 5v5 even-strength "
                f"line in this game exactly as expected (all three "
                f"players together, no member replaced, promoted, "
                f"demoted or shuffled to another line). Power-play unit "
                f"changes alone do not break this trio."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def _as_float(val: Any) -> Optional[float]:
    try:
        if val is None or isinstance(val, bool):
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _extract(answer: Any) -> tuple[dict[str, float], Optional[float]]:
    """(class probabilities, trio noul) from a Decisions-style answer.

    Accepts the shapes the merged client produces or a flat test shape:
    ``{"probabilities": {...}, "trio_intact": x}``,
    ``{"answers": {CLASS_QUESTION_ID: {"probabilities": {...}},
                    TRIO_QUESTION_ID: {"noul"|"value"|"probability": x}}}``,
    or the same nesting without the outer ``answers`` wrapper.
    """
    probs: dict[str, float] = {}
    noul: Optional[float] = None
    if not isinstance(answer, Mapping):
        return probs, noul
    container: Mapping[str, Any] = answer
    answers = answer.get("answers")
    if isinstance(answers, Mapping):
        container = answers

    class_ans = container.get(CLASS_QUESTION_ID)
    if isinstance(class_ans, Mapping) and isinstance(class_ans.get("probabilities"), Mapping):
        for k, v in class_ans["probabilities"].items():
            fv = _as_float(v)
            if fv is not None:
                probs[str(k)] = fv
    if not probs and isinstance(answer.get("probabilities"), Mapping):
        for k, v in answer["probabilities"].items():
            fv = _as_float(v)
            if fv is not None:
                probs[str(k)] = fv
    # A bare picked class with no distribution still carries information:
    # treat an explicit choice as P(chosen) = 1.0 for gate purposes.
    if not probs:
        choice = None
        if isinstance(class_ans, Mapping):
            choice = class_ans.get("choice")
        if choice is None:
            choice = answer.get("choice")
        if isinstance(choice, str) and choice in LABELS:
            probs = {choice: 1.0}

    trio_ans = container.get(TRIO_QUESTION_ID)
    if isinstance(trio_ans, Mapping):
        for key in ("noul", "value", "probability", "trio_intact"):
            noul = _as_float(trio_ans.get(key))
            if noul is not None:
                break
    if noul is None:
        for key in ("trio_intact", "trio_noul", "noul"):
            noul = _as_float(answer.get(key))
            if noul is not None:
                break
    return probs, noul


def gate(answer: Any, trio: Any) -> dict:
    """Suspension decision for one trio from a Decisions answer.

    Returns {"suspend": bool, "reasons": [str], "probabilities": {...},
    "trio_intact": float|None, "trio": [names], "abstain": bool}.

    Suspends iff P(line_shuffle) >= 0.80 OR P(promotion_demotion) >=
    0.80 OR trio noul < 0.80 (ruling thresholds). P(pp_only_change)
    NEVER appears in ``reasons`` and never suspends on its own -- the
    isolation invariant. An answer with no usable probabilities and no
    noul is an abstention (``abstain`` True, ``suspend`` False): the
    gate invents no suspension from no evidence.

    Conditional exemption (RUL-20261113-dec-gate-design Q3): the trio
    prong is waived for this trio when the class distribution is
    confidently power-play-only (see ``trio_prong_exempted`` below);
    the class prongs above are never waived.
    """
    probs, noul = _extract(answer)
    reasons: list[str] = []
    p_shuffle = probs.get("line_shuffle")
    p_promo = probs.get("promotion_demotion")
    if p_shuffle is not None and p_shuffle >= SUSPEND_THRESHOLD:
        reasons.append(f"P(line_shuffle)={p_shuffle:.2f} >= {SUSPEND_THRESHOLD:.2f}")
    if p_promo is not None and p_promo >= SUSPEND_THRESHOLD:
        reasons.append(f"P(promotion_demotion)={p_promo:.2f} >= {SUSPEND_THRESHOLD:.2f}")
    # Conditional class-prong exemption (Argon ruling RUL-20261113-dec-gate-design
    # Q3): this trio is exempt from the trio_intact suspension trigger iff the
    # class distribution is confidently power-play-only -- P(pp_only_change)
    # >= 0.80 AND P(line_shuffle) < 0.20 AND P(promotion_demotion) < 0.20
    # (missing keys in a present distribution count as 0.0; a bare choice
    # fallback reads as P(chosen) = 1.0 via _extract). Otherwise the trio
    # prong stays fully active and fails closed, compound blurbs included.
    trio_prong_exempted = (
        probs.get("pp_only_change", 0.0) >= 0.80
        and probs.get("line_shuffle", 0.0) < 0.20
        and probs.get("promotion_demotion", 0.0) < 0.20
    )
    if noul is not None and noul < TRIO_INTACT_MIN and not trio_prong_exempted:
        reasons.append(f"trio noul={noul:.2f} < {TRIO_INTACT_MIN:.2f}")
    abstain = not probs and noul is None
    if abstain:
        reasons = ["no_answer: abstention (no probabilities, no trio noul)"]
    return {
        "suspend": bool(reasons) and not abstain,
        "reasons": reasons,
        "probabilities": probs,
        "trio_intact": noul,
        "trio": _trio_names(trio),
        "abstain": abstain,
        "trio_prong_exempted": trio_prong_exempted and not abstain,
    }
