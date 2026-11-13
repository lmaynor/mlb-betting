"""N2 event gate: question pack + deterministic slate matcher + gate rule.

Prep work for the Decisions-API expansion (Argon ruling, 2026-11-13,
section N2). The N2 route watches game-day prose for *event-state* changes
(a game being postponed, suspended, moved, reformatted, or ended early) as
opposed to player-availability news, which is Build #2 territory.

This module is deliberately free of any API client: it only builds the
Decisions questions payload, matches fixture/slate items deterministically,
and applies the ruling's gate rule to a parsed answer mapping. The merged
Decisions client's published envelope style (see
``nba/minutes_restriction/decisions_client.py``) is: a request carrying
``input`` context text plus a ``questions`` MAP keyed by question id, where
a choice question is ``{"type": "choice", "instructions": str,
"criteria": {option: description}}`` and a "noul" question is
``{"type": "noul", "instructions": str}`` asking for a 0..1 probability.
Answers come back as a map keyed by the same question ids.

Gate rule (the ruling, verbatim logic): witness/off iff

* the ``material_change`` noul probability is >= 0.75, OR
* P(no_change) < 0.30 AND the picked choice is a concrete disruption class
  (one of :data:`DISRUPTION_CLASSES`).

Anything else passes. ``gate`` is a pure function over the parsed answer
mapping; it never calls a model or the network. The pipeline fails closed
one level up: an item that cannot be matched to the slate
(:func:`match_event` returns ``None``) must not be bet, and ``gate`` only
fires on an affirmatively-met trigger, so a malformed or partial answer
passes rather than witnesses.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional

__all__ = [
    "ALL_CLASSES",
    "CHOICE_QUESTION_ID",
    "DISRUPTION_CLASSES",
    "NOUL_QUESTION_ID",
    "NOUL_THRESHOLD",
    "P_NO_CHANGE_CEILING",
    "build_questions",
    "canonical_team",
    "gate",
    "match_event",
    "normalize_date",
]

CHOICE_QUESTION_ID = "event_state"
NOUL_QUESTION_ID = "material_change"

NO_CHANGE = "no_change"
DISRUPTION_CLASSES = (
    "postponed",
    "suspended",
    "venue_change",
    "format_change",
    "completed_early",
)
ALL_CLASSES = (NO_CHANGE,) + DISRUPTION_CLASSES

NOUL_THRESHOLD = 0.75
P_NO_CHANGE_CEILING = 0.30

_CLASS_CRITERIA = {
    NO_CHANGE: (
        "The game is played as scheduled: same date, same venue, full "
        "regulation length, no interruption that changes the event itself. "
        "Player injuries, lineup news, weather that does not change the "
        "event, and ordinary game coverage are no_change."
    ),
    "postponed": (
        "The game is not played on its scheduled date and is moved to a "
        "different date (weather, wildfire smoke/fire, travel, safety, "
        "facility, or league decision)."
    ),
    "suspended": (
        "The game started but was halted before completion and is (or was) "
        "set to resume at a later time, or never resumed and was settled "
        "under suspended-game rules."
    ),
    "venue_change": (
        "The game is played at a different venue than originally scheduled "
        "(relocation to another stadium/arena or city), including a "
        "franchise's announced move of its home games to a substitute venue."
    ),
    "format_change": (
        "The game is still played on its scheduled date at its scheduled "
        "venue, but its format changed materially: start time shifted by "
        "hours, moved to a doubleheader on another day of the series, "
        "shortened playing format, or similar."
    ),
    "completed_early": (
        "The game started and was ended before its full scheduled length "
        "with the partial result standing as final (e.g. called off in the "
        "late innings/quarters and not resumed)."
    ),
}

# ---------------------------------------------------------------------------
# Deterministic team-name normalization
# ---------------------------------------------------------------------------

_ALIASES = {
    "a s": "athletics",
    "as": "athletics",
    "athletics": "athletics",
    "oakland athletics": "athletics",
    "chi cubs": "chicago cubs",
    "chi white sox": "chicago white sox",
    "kc royals": "kansas city royals",
    "la angels": "los angeles angels",
    "la clippers": "los angeles clippers",
    "la dodgers": "los angeles dodgers",
    "la lakers": "los angeles lakers",
    "la rams": "los angeles rams",
    "ny giants": "new york giants",
    "ny jets": "new york jets",
    "ny knicks": "new york knicks",
    "ny mets": "new york mets",
    "ny rangers": "new york rangers",
    "ny yankees": "new york yankees",
    "sd padres": "san diego padres",
    "sf 49ers": "san francisco 49ers",
    "sf giants": "san francisco giants",
    "tb buccaneers": "tampa bay buccaneers",
    "tb lightning": "tampa bay lightning",
    "tb rays": "tampa bay rays",
    "st louis blues": "st louis blues",
    "st louis cardinals": "st louis cardinals",
}


def canonical_team(name: Any) -> str:
    """Canonicalize a team name for deterministic comparison.

    Lowercases, drops apostrophes/punctuation, collapses whitespace, strips
    a leading "the", normalizes "St." to "st", and maps common short forms
    (e.g. "NY Yankees", "A's") onto the full name. Pure string work; never
    a model call.
    """
    if name is None:
        return ""
    text = str(name).lower().replace("'", "").replace("\u2019", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith("the "):
        text = text[4:]
    return _ALIASES.get(text, text)


def normalize_date(value: Any) -> Optional[str]:
    """Normalize a date-like value to an ISO ``YYYY-MM-DD`` string.

    Accepts ``date``/``datetime`` objects and strings whose first 10 chars
    are an ISO date (so full timestamps work). Returns ``None`` when the
    value cannot be parsed, which makes matching fail closed.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10]).isoformat()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Slate matching
# ---------------------------------------------------------------------------

def _game_view(record: Mapping[str, Any]) -> Mapping[str, Any]:
    game = record.get("game")
    return game if isinstance(game, Mapping) else record


def _first(view: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in view and view[key] is not None:
            return view[key]
    return None


def _teams_and_date(record: Mapping[str, Any]) -> Optional[tuple[frozenset, str]]:
    view = _game_view(record)
    away = _first(view, "away", "away_team", "visitor", "visiting_team")
    home = _first(view, "home", "home_team")
    when = _first(view, "date", "game_date", "scheduled_date", "start_date")
    if away is None or home is None:
        # A free-text matchup ("Team A at Team B") is not parsed: matching
        # stays deterministic on structured fields only.
        return None
    day = normalize_date(when)
    if day is None:
        return None
    return frozenset((canonical_team(away), canonical_team(home))), day


def match_event(item: Mapping[str, Any], slate: Iterable[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    """Return the slate entry for ``item``'s game, or ``None``.

    Deterministic matching ONLY: league (when both sides state one),
    scheduled date, and the unordered pair of canonicalized team names must
    all agree. No fuzzy scoring and no model call. ``None`` means unmatched,
    and the gate fails closed on unmatched items (the caller must not bet
    them).
    """
    target = _teams_and_date(item)
    if target is None:
        return None
    teams, day = target
    item_league = str(item.get("league", "")).strip().lower() or None
    for entry in slate:
        if not isinstance(entry, Mapping):
            continue
        entry_league = str(entry.get("league", "")).strip().lower() or None
        if item_league and entry_league and item_league != entry_league:
            continue
        candidate = _teams_and_date(entry)
        if candidate is None:
            continue
        if candidate == (teams, day):
            return entry
    return None


# ---------------------------------------------------------------------------
# Question pack
# ---------------------------------------------------------------------------

def build_questions(item: Mapping[str, Any]) -> dict:
    """Build the Decisions ``questions`` map for one game-day item.

    Mirrors the merged client's published envelope style: a MAP keyed by
    question id with one ``choice`` question over the event-state classes
    and one ``noul`` question asking for the probability of a material
    event change.
    """
    view = _game_view(item)
    away = _first(view, "away", "away_team") or "the visiting team"
    home = _first(view, "home", "home_team") or "the home team"
    day = normalize_date(_first(view, "date", "game_date")) or "its scheduled date"
    league = str(item.get("league", "")).strip()
    blurb = str(item.get("blurb_text", "")).strip()
    matchup = f"{away} at {home} on {day}" + (f" ({league})" if league else "")
    context = f"Game: {matchup}."
    if blurb:
        context += f" Report: {blurb}"
    return {
        CHOICE_QUESTION_ID: {
            "type": "choice",
            "instructions": (
                f"{context} Classify the state of this scheduled game based "
                "only on what the report says about the event itself (not "
                "player availability). Pick exactly one class."
            ),
            "criteria": dict(_CLASS_CRITERIA),
        },
        NOUL_QUESTION_ID: {
            "type": "noul",
            "instructions": (
                f"{context} Give the probability, from 0 to 1, that this "
                "game undergoes a material event change: it is postponed to "
                "another date, suspended mid-game, moved to a different "
                "venue, materially reformatted (start time or format), or "
                "ended early. Ordinary injury or lineup news is NOT a "
                "material event change."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _noul_value(entry: Any) -> Optional[float]:
    direct = _as_float(entry)
    if direct is not None:
        return direct
    if isinstance(entry, Mapping):
        for key in ("probability", "value", "noul", "p"):
            parsed = _as_float(entry.get(key))
            if parsed is not None:
                return parsed
    return None


def _choice_pick(entry: Any) -> Optional[str]:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, Mapping):
        for key in ("choice", "answer", "value", "pick", "selected"):
            raw = entry.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return None


def _probabilities(entry: Any) -> Mapping[str, Any]:
    if isinstance(entry, Mapping):
        probs = entry.get("probabilities")
        if isinstance(probs, Mapping):
            return probs
    return {}


def gate(answer: Mapping[str, Any]) -> dict:
    """Apply the N2 gate rule to a parsed Decisions answer mapping.

    ``answer`` is the answers map from a Decisions response (a full response
    dict with an ``"answers"`` key is also accepted). Returns an action
    dict: ``{"action": "witness_off", ...}`` when the ruling's trigger
    fires, else ``{"action": "pass", ...}``. Pure function; no API calls.
    A trigger must be affirmatively established from the answer, so a
    missing or malformed field yields ``pass`` with the gap named in
    ``reason``.
    """
    answers: Mapping[str, Any] = answer if isinstance(answer, Mapping) else {}
    nested = answers.get("answers")
    if isinstance(nested, Mapping):
        answers = nested

    noul = _noul_value(answers.get(NOUL_QUESTION_ID))
    choice_entry = answers.get(CHOICE_QUESTION_ID)
    picked = _choice_pick(choice_entry)
    probs = _probabilities(choice_entry)
    p_no_change = _as_float(probs.get(NO_CHANGE)) if probs else None

    result = {
        "action": "pass",
        "reason": "no gate trigger met",
        "noul": noul,
        "picked_class": picked,
        "p_no_change": p_no_change,
    }

    if noul is not None and noul >= NOUL_THRESHOLD:
        result["action"] = "witness_off"
        result["reason"] = (
            f"material_change noul {noul:.2f} >= {NOUL_THRESHOLD:.2f}"
        )
        return result

    if (
        picked in DISRUPTION_CLASSES
        and p_no_change is not None
        and p_no_change < P_NO_CHANGE_CEILING
    ):
        result["action"] = "witness_off"
        result["reason"] = (
            f"P(no_change) {p_no_change:.2f} < {P_NO_CHANGE_CEILING:.2f} "
            f"with concrete disruption class picked: {picked}"
        )
        return result

    gaps = []
    if noul is None:
        gaps.append("no noul probability")
    if picked is None:
        gaps.append("no class picked")
    if picked in DISRUPTION_CLASSES and p_no_change is None:
        gaps.append("no P(no_change)")
    if gaps:
        result["reason"] = "pass (insufficient answer: " + ", ".join(gaps) + ")"
    return result
