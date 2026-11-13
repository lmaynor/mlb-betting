"""ev.decisions_prep.n5_correction_sentinel -- prep for the N5 route
(post-settlement correction sentinel, ADOPT shadow-only in Argon's
Nov 13, 2026 Decisions-API expansion ruling).

This module is ONLY the non-API prep the ruling's sequencing section 3(b)
mandates: the question schema, the deterministic game/player matcher that
must run before any model call, and the quarantine gate as pure functions
over a parsed answer mapping. It makes NO API calls and never recomputes
or republishes a graded outcome: the sentinel's only live action (in the
future route) is to quarantine a settled row for human review.

Ruling parameters implemented here:
- Quarantine for review when P(correction) >= 0.80 (noul) OR
  P(no_correction) < 0.25 on a matched postgame item.
- Reinstate automatically under the original graded result if no
  correction materializes within 48 hours of the game's credited
  boxscore; a persisted correction routes to a human.
- Deterministic game/player matching runs FIRST; an unmatched item is
  never sent to the model (``match_settled_row`` returns None).

Question envelope follows the merged Decisions client
(nba/minutes_restriction/decisions_client.py): the request body is
{"model", "input", "questions"} where ``questions`` is a MAP keyed by
question id; a "choice" question is {"type": "choice", "instructions",
"criteria": {option: description}} and a "noul" question is
{"type": "noul", "instructions"} expressing a 0..1 probability.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

MODEL = "gemini-decision-1"

Q_CORRECTION = "correction_occurred"
Q_NO_CORRECTION = "no_correction"
Q_KIND = "correction_kind"

CORRECTION_KINDS: tuple[str, ...] = (
    "none",
    "stat_revision",
    "scoring_change",
    "result_reversal",
)

QUARANTINE_P_CORRECTION = 0.80
QUARANTINE_P_NO_CORRECTION_BELOW = 0.25
REINSTATE_WINDOW_HOURS = 48.0


# --------------------------------------------------------------------------
# deterministic matching (runs before any model call; never a model decision)
# --------------------------------------------------------------------------

def _norm_name(name: Any) -> str:
    return re.sub(r"[^a-z]", "", str(name or "").lower())


def settled_row_key(row: Mapping[str, Any]) -> tuple:
    """Canonical identity of a settled row for matching: game, player, stat.

    Accepts both settled-ledger shapes in the repo: NHL paper-track rows
    (game_key/player_id/stat) and tracker bets (game_date/teams/player/
    bet_type). Player identity prefers the numeric player_id when both
    sides carry one; falls back to the normalized name.
    """
    game = row.get("game_key") or "|".join(
        str(row.get(k) or "") for k in ("game_date", "away_team", "home_team")
    )
    player = row.get("player_id")
    if player is None:
        player = _norm_name(row.get("player"))
    stat = row.get("stat") or str(row.get("bet_type") or "").rsplit("_", 2)[0]
    return (str(game), player, str(stat))


def match_settled_row(
    item: Mapping[str, Any], settled_rows: Sequence[Mapping[str, Any]]
) -> Optional[Mapping[str, Any]]:
    """Match a postgame prose item to exactly one settled row, or None.

    ``item`` must carry the same identity fields as a settled row (game,
    player, stat) -- extracted deterministically upstream (schedule/boxscore
    join), never by the model. Ambiguous (multiple) matches return None:
    the sentinel fails closed rather than guessing which row to flag.
    """
    key = settled_row_key(item)
    matches = [r for r in settled_rows if settled_row_key(r) == key]
    return matches[0] if len(matches) == 1 else None


# --------------------------------------------------------------------------
# question schema
# --------------------------------------------------------------------------

def build_context(settled_row: Mapping[str, Any], prose_item: Mapping[str, Any]) -> str:
    lines = [
        "Settled bet under review (already graded; do not re-grade it):",
        f"Game: {settled_row.get('game_key') or settled_row.get('game_date')}",
        f"Player: {settled_row.get('player')} (id {settled_row.get('player_id')})",
        f"Stat: {settled_row.get('stat') or settled_row.get('bet_type')}, "
        f"line {settled_row.get('line') or settled_row.get('point')}, "
        f"settled actual: {settled_row.get('actual')}, "
        f"settled at {settled_row.get('settled_ts') or settled_row.get('settled_at')}",
        "",
        "Postgame item published after settlement:",
        f"[{prose_item.get('published_ts')}] {prose_item.get('headline')}: "
        f"{prose_item.get('body')}",
    ]
    return "\n".join(lines)


def build_questions(
    settled_row: Mapping[str, Any], prose_item: Mapping[str, Any]
) -> dict:
    """The full request body (minus transport) for one matched item."""
    return {
        "model": MODEL,
        "input": build_context(settled_row, prose_item),
        "questions": {
            Q_CORRECTION: {
                "type": "noul",
                "instructions": (
                    "What is the probability that the postgame item reports a "
                    "league stat correction or scoring change that alters this "
                    "player's settled stat for this game?"
                ),
            },
            Q_NO_CORRECTION: {
                "type": "noul",
                "instructions": (
                    "What is the probability that NO correction affects this "
                    "player's settled stat for this game (the item is routine "
                    "postgame coverage, a correction for a different player or "
                    "game, or a change that does not touch the settled stat)?"
                ),
            },
            Q_KIND: {
                "type": "choice",
                "instructions": (
                    "If a correction is reported, which kind is it? Choose "
                    "'none' if the item reports no correction."
                ),
                "criteria": {
                    "none": "The item reports no stat correction or scoring change.",
                    "stat_revision": "A player's counting stat was revised after the game (e.g. an assist, rebound, shot or save added or removed).",
                    "scoring_change": "An official scorer changed how a play was scored (e.g. goal credited to a different player, error/hit reclassified).",
                    "result_reversal": "The game's recorded result or a graded outcome itself was reversed.",
                },
            },
        },
    }


# --------------------------------------------------------------------------
# gate (pure functions over a parsed answer mapping; no API calls)
# --------------------------------------------------------------------------

def _prob(answer: Mapping[str, Any], qid: str) -> Optional[float]:
    raw = answer.get(qid)
    if isinstance(raw, Mapping):
        raw = raw.get("probability", raw.get("p"))
    if isinstance(raw, (int, float)) and 0.0 <= float(raw) <= 1.0:
        return float(raw)
    return None


def gate(answer: Mapping[str, Any]) -> dict:
    """Apply the ruling's quarantine thresholds to a parsed answer.

    Returns {"action": "quarantine"|"pass", "reasons": [...]}. Missing or
    malformed probabilities quarantine with reason "abstention": the
    sentinel's only action is human review, so the safe side of an
    unparseable answer is review, and the live shadow's false-quarantine
    bar (<= 0.5% of settled rows) counts these like any other flag.
    """
    p_corr = _prob(answer, Q_CORRECTION)
    p_none = _prob(answer, Q_NO_CORRECTION)
    if p_corr is None or p_none is None:
        return {"action": "quarantine", "reasons": ["abstention"]}
    reasons = []
    if p_corr >= QUARANTINE_P_CORRECTION:
        reasons.append(f"p_correction {p_corr:.2f} >= {QUARANTINE_P_CORRECTION}")
    if p_none < QUARANTINE_P_NO_CORRECTION_BELOW:
        reasons.append(f"p_no_correction {p_none:.2f} < {QUARANTINE_P_NO_CORRECTION_BELOW}")
    return {"action": "quarantine" if reasons else "pass", "reasons": reasons}


def reinstate_decision(
    settled_ts_hours_since_boxscore: float, correction_materialized: bool
) -> str:
    """The ruling's 48-hour rule as a pure function.

    ``settled_ts_hours_since_boxscore`` is the age of the credited boxscore
    when the check runs. Returns "reinstate" (original grade stands),
    "route_to_human" (a correction persisted), or "pending" (still inside
    the window with no correction yet).
    """
    if correction_materialized:
        return "route_to_human"
    if settled_ts_hours_since_boxscore >= REINSTATE_WINDOW_HOURS:
        return "reinstate"
    return "pending"
