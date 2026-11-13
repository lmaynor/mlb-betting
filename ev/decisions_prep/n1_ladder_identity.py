"""ev.decisions_prep.n1_ladder_identity -- N1 ladder market-identity
adjudicator: question schema, deterministic pre-filter, and acceptance gate.

Route N1 of Argon's Decisions-API expansion ruling (Nov 13, 2026,
~/workspace/beezy/profitability/argon-decisions-api-expansion-VERBATIM-2026-11-13.md,
section N1 and sequencing ruling 3(b)):

- ADOPT (standalone), hourly ambiguity-set batch. Exact deterministic matches
  never touch the API; only the parser's ambiguity set -- raw-payload ladder
  quotes whose canonical market identity the deterministic parser
  (ev/props.py: ``parse_event_props`` / ``stat_of``) cannot settle, which
  would otherwise sink unresolved into ``EVSweep/nfl_props/raw/{day}/`` --
  is adjudicated.
- Tightened gate (the ruling's words, operationalized in ``adjudicate``):
  deterministic Python parses and matches the numeric rung/side BEFORE any
  Decisions call (a row lacking an exact numeric rung match stays
  quarantined regardless of the model); acceptance requires
  ``noul >= 0.95``; the choice stat must exactly equal the parser's
  candidate stat; any scope-sensitive row where the scope choice is
  unknown or ``P(unknown) > 0.10`` is quarantined.
- Kill criterion is evaluated offline on the hand-labeled fixture in
  ``tests/fixtures/decisions/n1_ladder_identity/`` (>= 500 historical
  ambiguous ladder labels across books; kill if false canonicalization
  > 0.5%, scope disagreement > 1.0%, or quarantine-rate drop < 30% vs
  parser-only). This module builds no part of that evaluation; it only
  defines the schema and gate the evaluation scores.

This is PREP work under sequencing ruling 3(b): question schemas,
deterministic join/ambiguity filters, and gates may be built immediately,
but live Decisions route code waits for PR #242/#243 to merge so calls run
on the canonical client. This module therefore makes NO API calls; it only
constructs question dicts and scores fabricated/returned answer mappings
as pure functions.

Entry / parser_view shapes (plain mappings, as produced from a raw
ParlayAPI payload row and from ``ev.props.stat_of``):

- ``entry``: one raw market quote. Keys used here: ``book``, ``player``,
  ``raw_market_key``, ``raw_description`` (the outcome description, i.e.
  the player string as printed by the book), ``raw_name`` (the outcome
  name, e.g. ``"Over <player>"`` / ``"Yes"``), ``raw_point`` (the feed's
  numeric point for the outcome), ``raw_price_side_info`` (a mapping with
  ``sides_present`` -- the subset of ``{"over", "under", "yes", "no"}``
  the book posts for this player/market/point).
- ``parser_view``: the deterministic parser's view of the same quote:
  ``candidate_stat`` (canonical stat from the key table, or None when the
  market key is unmapped / nonstandard), ``candidate_stats`` (optional
  list of plausible canonical stats), ``is_alternate_key`` (key carries
  the ``_alternate`` suffix), ``two_sided`` (the parser folded both an
  Over and an Under price for this quote), ``points_for_book_player``
  (every distinct point the SAME book posts for this player + market key
  in the payload), ``cross_book_main_point`` (the modal point other books
  quote two-sided for this player + candidate stat in the same payload,
  or None when no cross-book reference exists).

Ambiguity definition (operational; identical to the one the F1 fixture
was labeled under -- see the fixture README): a quote is ambiguous iff
(a) the key table yields no candidate stat (nonstandard / unmapped key:
the stat itself is unsettled), or (b) it is a one-sided rung of a
multi-point ladder posted inside a plain main market key (milestone
threshold vs alternate-rung semantics are unsettled), or (c) it is a
two-sided rung of a multi-point ladder whose point is not the cross-book
main point -- or whose main point cannot be established at all -- so its
scope (main vs alternate) is unsettled. A plain two-sided quote at the
cross-book main point under a mapped key is an exact deterministic match
and is NEVER selected.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

MODEL = "gemini-decision-1"

# Question ids (keys of the questions map / answers map).
QUESTION_STAT = "canonical_stat"
QUESTION_MILESTONE = "scope_is_milestone"
QUESTION_UNKNOWN = "scope_unknown"
QUESTION_IDS: tuple[str, ...] = (QUESTION_STAT, QUESTION_MILESTONE, QUESTION_UNKNOWN)

# The ruling's tightened acceptance thresholds (N1).
NOUL_MIN = 0.95
UNKNOWN_MAX = 0.10

# Choice option that means "the stat cannot be determined". The ruling
# quarantines any scope-sensitive row whose choice is unknown; the stat
# choice carries the same fail-closed option.
UNKNOWN_OPTION = "unknown"

SCOPES: tuple[str, ...] = ("main", "alternate", "milestone")

# ---------------------------------------------------------------------------
# Deterministic rung / side parsing
# ---------------------------------------------------------------------------

_NUM = r"(\d+(?:\.\d+)?)"
_LINE_RE = re.compile(rf"^(over|under)\s+{_NUM}(?:\s+(.*))?$")
_MILESTONE_RE = re.compile(rf"^{_NUM}\s*\+$")
# Trailing phrases allowed after a rung number: exactly one known unit /
# stat phrase. Anything else (a range, prose, a second number) rejects.
_PHRASE_TAILS = frozenset({
    "", "yds", "yards", "receiving yds", "receiving yards", "rec yds",
    "rush yds", "rushing yds", "rushing yards",
    "pass yds", "passing yds", "passing yards",
    "receptions", "recs", "td", "tds", "touchdown", "touchdowns", "points",
})


def parse_rung_side(text: Any) -> Optional[tuple[float, str]]:
    """Parse a rung/side string into ``(rung, side)`` or return None.

    Pure and strict: the WHOLE string must be exactly one of

    - ``"Over 49.5"`` / ``"Under 6.5"`` -- an over/under line; the rung is
      the quoted line itself (``49.5`` / ``6.5``), side ``"over"`` /
      ``"under"``;
    - ``"Over 49.5 <stat phrase>"`` -- the numbered outcome-name form
      (``"Over Ja'Marr Chase"`` carries no number and does NOT parse; the
      numbered form ``"Over 49.5 Receiving Yards"`` does; a trailing
      phrase outside the known unit/stat phrases, e.g. a range
      ``"Over 49.5 to 59.5"``, does NOT parse);
    - ``"50+"`` / ``"50+ Yds"`` / ``"50+ Receiving Yds"`` -- a milestone
      threshold; the rung is the equivalent over line, ``threshold - 0.5``
      (``50+`` means "at least 50", i.e. Over 49.5), side ``"yes"``;
    - ``"Yes"`` / ``"No"`` alone carry no rung and return None, as does
      anything else (ranges, words like "fifty", signed numbers, extra
      tokens in the over/under line form). Case-insensitive; surrounding
      whitespace is ignored.

    The milestone conversion is the deterministic half of the ruling's
    warning: ``"50+ Yds"`` vs ``"Over 49.5 Yds"`` must be reconciled in
    Python, never left to the model.
    """
    if not isinstance(text, str):
        return None
    s = " ".join(text.strip().lower().split())
    if not s:
        return None
    m = _LINE_RE.match(s)
    if m and (m.group(3) or "") in _PHRASE_TAILS:
        return float(m.group(2)), m.group(1)
    # Milestone: "<N>+" optionally followed by exactly one known unit /
    # stat phrase ("50+", "50+ Yds", "50+ Receiving Yds", "1+ TD").
    head, _, tail = s.partition(" ")
    m = _MILESTONE_RE.match(head)
    if m and tail in _PHRASE_TAILS:
        threshold = float(m.group(1))
        if threshold == int(threshold) and threshold >= 1:
            return threshold - 0.5, "yes"
    return None


def deterministic_rung(entry: Mapping[str, Any]) -> Optional[tuple[float, str]]:
    """The rung/side Python can settle for one entry without the model.

    Order: the outcome name if it carries a number, then a milestone
    phrase in the raw description, then the feed's numeric ``raw_point``
    paired with the side implied by the outcome name (``Over ...`` /
    ``Under ...`` / ``Yes`` / ``No``). Returns None when no exact numeric
    rung can be established -- the ruling quarantines such rows regardless
    of any model score.
    """
    name = entry.get("raw_name")
    parsed = parse_rung_side(name)
    if parsed is not None:
        return parsed
    parsed = parse_rung_side(entry.get("raw_description"))
    if parsed is not None:
        return parsed
    point = entry.get("raw_point")
    if isinstance(point, (int, float)) and not isinstance(point, bool):
        low = str(name or "").strip().lower()
        if low.startswith("over"):
            return float(point), "over"
        if low.startswith("under"):
            return float(point), "under"
        if low == "yes":
            return float(point), "yes"
        if low == "no":
            return float(point), "no"
    return None


# ---------------------------------------------------------------------------
# Deterministic pre-filter: the ambiguity set
# ---------------------------------------------------------------------------


def is_ambiguous(entry: Mapping[str, Any], parser_view: Mapping[str, Any]) -> bool:
    """True iff this quote belongs to the N1 ambiguity set.

    Implements the fixture's ambiguity definition (see module docstring):
    unmapped/nonstandard key, one-sided ladder rung, or off-main (or
    main-unverifiable) ladder rung. Exact deterministic matches -- mapped
    key, two-sided, at the cross-book main point, or a standalone
    single-point quote -- return False and never reach the API.
    """
    candidate = parser_view.get("candidate_stat")
    if candidate is None:
        return True  # (a) stat unsettled: key not in the parser's table

    points = parser_view.get("points_for_book_player") or ()
    distinct_points = {float(p) for p in points}
    point = entry.get("raw_point")
    point_f = float(point) if isinstance(point, (int, float)) else None

    info = entry.get("raw_price_side_info") or {}
    sides = set(info.get("sides_present") or ())
    two_sided = bool(parser_view.get("two_sided"))
    if not two_sided and sides:
        two_sided = "over" in sides and "under" in sides
    one_sided = not two_sided

    if len(distinct_points) >= 2:
        if one_sided:
            return True  # (b) milestone-vs-alt-rung semantics unsettled
        main_point = parser_view.get("cross_book_main_point")
        if main_point is None:
            return True  # (c) ladder rung, main line unverifiable
        if point_f is not None and float(main_point) != point_f:
            return True  # (c) off-main ladder rung: scope unsettled
    return False


def select_ambiguity_set(
    entries: Sequence[Mapping[str, Any]],
    parser_view: Any,
) -> list[Mapping[str, Any]]:
    """Filter ``entries`` to the ambiguity set.

    ``parser_view`` is either a mapping applied to every entry, a callable
    ``entry -> parser_view mapping``, or a sequence of parser views
    parallel to ``entries``. Order is preserved; exact deterministic
    matches are never selected.
    """
    if callable(parser_view):
        views = [parser_view(e) for e in entries]
    elif isinstance(parser_view, Mapping):
        views = [parser_view] * len(entries)
    else:
        views = list(parser_view)
    return [e for e, v in zip(entries, views) if is_ambiguous(e, v)]


# ---------------------------------------------------------------------------
# Decisions question pack (envelope style of the merged client)
# ---------------------------------------------------------------------------

_STAT_DESCRIPTIONS: dict[str, str] = {
    "pass_yds": "Passing yards by the player (quarterback passing yardage).",
    "rush_yds": "Rushing yards by the player.",
    "rec_yds": "Receiving yards by the player.",
    "receptions": "Receptions (catches) by the player.",
    "pass_tds": "Passing touchdowns thrown by the player.",
    "anytime_td": "Anytime touchdown scorer: the player scores at least one touchdown of any kind.",
    "pass_attempts": "Pass attempts by the player.",
    "pass_completions": "Completed passes by the player.",
    "rush_attempts": "Rushing attempts (carries) by the player.",
    "interceptions": "Interceptions thrown by the player.",
    UNKNOWN_OPTION: "The canonical stat cannot be determined from this quote.",
}


def build_questions(entry: Mapping[str, Any], candidate_stats: Sequence[str]) -> dict[str, Any]:
    """Build the Decisions questions map for one ambiguous ladder quote.

    Published envelope style (merged client, nba/minutes_restriction/
    decisions_client.py): the request body is ``{"model", "input",
    "questions"}`` and ``questions`` is a MAP keyed by question id; this
    function returns that map (the caller supplies ``model`` / ``input``).
    A ``choice`` question carries ``instructions`` plus ``criteria``
    mapping each option id to its description; a ``noul`` question
    expresses a 0..1 probability for a yes/no proposition and carries
    ``instructions`` only. This function only constructs the dict; it
    makes no API call.

    Questions:
    - ``canonical_stat`` (choice over ``candidate_stats`` plus an
      explicit ``unknown`` option): which canonical stat this raw market
      quote prices.
    - ``scope_is_milestone`` (noul): probability the quote is a milestone
      threshold quote ("reach at least N", one-sided Yes semantics)
      rather than a two-sided alternate over/under rung.
    - ``scope_unknown`` (noul): probability the quote's scope (main /
      alternate / milestone) cannot be determined at all; the gate
      quarantines above 0.10 per the ruling.
    """
    options = [str(s) for s in candidate_stats if str(s) != UNKNOWN_OPTION]
    if UNKNOWN_OPTION not in options:
        options.append(UNKNOWN_OPTION)
    criteria = {
        opt: _STAT_DESCRIPTIONS.get(opt, f"Canonical stat '{opt}'.") for opt in options
    }
    book = entry.get("book")
    player = entry.get("player") or entry.get("raw_description")
    key = entry.get("raw_market_key")
    return {
        QUESTION_STAT: {
            "type": "choice",
            "instructions": (
                f"A sportsbook ({book}) lists a player-prop market with raw "
                f"market key '{key}' for player {player}. Which canonical "
                "stat does this quote price? Choose 'unknown' if the stat "
                "cannot be determined from the quote."
            ),
            "criteria": criteria,
        },
        QUESTION_MILESTONE: {
            "type": "noul",
            "instructions": (
                "What is the probability that this quote is a milestone "
                "threshold quote -- a one-sided 'reach at least N' price "
                "(Yes semantics) -- rather than one rung of a two-sided "
                "alternate over/under ladder?"
            ),
        },
        QUESTION_UNKNOWN: {
            "type": "noul",
            "instructions": (
                "What is the probability that this quote's scope -- main "
                "line, alternate rung, or milestone threshold -- cannot be "
                "determined from the information available?"
            ),
        },
    }


def build_request_input(entry: Mapping[str, Any], parser_view: Mapping[str, Any]) -> str:
    """The shared ``input`` context text for one entry's request body.

    Renders only deterministic facts (the raw quote fields and the
    parser's candidate view); no label or ground truth is included.
    """
    info = entry.get("raw_price_side_info") or {}
    lines = [
        f"Book: {entry.get('book')}",
        f"Player: {entry.get('player') or entry.get('raw_description')}",
        f"Raw market key: {entry.get('raw_market_key')}",
        f"Outcome name: {entry.get('raw_name')}",
        f"Outcome description: {entry.get('raw_description')}",
        f"Raw point: {entry.get('raw_point')}",
        f"Sides posted for this quote: {sorted(info.get('sides_present') or ())}",
        f"Parser candidate stat: {parser_view.get('candidate_stat')}",
        "Points this book posts for this player and market: "
        f"{sorted(parser_view.get('points_for_book_player') or ())}",
        f"Cross-book main point: {parser_view.get('cross_book_main_point')}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The acceptance gate
# ---------------------------------------------------------------------------


def _choice_pick(answer: Mapping[str, Any]) -> Optional[str]:
    """Read the choice question's picked option from an answers mapping.

    Accepts a flat ``{"canonical_stat": "pass_tds"}`` or the published
    response shape ``{"canonical_stat": {"choice": ...}}`` (``picked`` /
    ``option`` also accepted). Anything else reads as missing.
    """
    raw = answer.get(QUESTION_STAT)
    if isinstance(raw, str):
        return raw
    if isinstance(raw, Mapping):
        for key in ("choice", "picked", "option"):
            val = raw.get(key)
            if isinstance(val, str):
                return val
    return None


def _noul_probability(answer: Mapping[str, Any], qid: str) -> Optional[float]:
    """Read one noul question's 0..1 probability (see _choice_pick shapes).

    For the milestone question the probability is P(milestone); the gate's
    headline ``noul`` is ``max(P(milestone), 1 - P(milestone))`` -- the
    model's confidence in its scope call, whichever way it lands. A
    ``{"probabilities": {"yes": p, "no": q}}`` sub-mapping is also read.
    """
    raw = answer.get(qid)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, Mapping):
        for key in ("probability", "p", "noul"):
            val = raw.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return float(val)
        probs = raw.get("probabilities")
        if isinstance(probs, Mapping):
            val = probs.get("yes", probs.get("true"))
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return float(val)
    return None


def adjudicate(
    entry: Mapping[str, Any],
    answer: Mapping[str, Any],
    parser_view: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Apply the ruling's N1 acceptance gate to one entry + answer mapping.

    ``answer`` is the Decisions answers mapping for this entry (flat or
    published per-question shapes; see ``_choice_pick`` /
    ``_noul_probability``). ``parser_view`` supplies the deterministic
    candidate stat; when omitted it is read from the entry itself
    (``parser_candidate_stat`` / ``candidate_stat`` keys).

    ACCEPT iff ALL of the following hold (any failure quarantines, with
    the reason recorded -- the ruling quarantines on any doubt rather
    than inventing a mapping):

    1. deterministic rung parse: ``deterministic_rung(entry)`` succeeds
       and its rung equals the entry's raw point exactly;
    2. the choice stat is present, is not ``unknown``, and exactly equals
       the parser's candidate stat;
    3. ``noul >= 0.95`` where noul is the model's confidence in its scope
       call: ``max(P(milestone), 1 - P(milestone))`` from the
       ``scope_is_milestone`` question (a missing probability fails);
    4. ``P(unknown) <= 0.10`` from the ``scope_unknown`` question (a
       missing probability fails closed and quarantines).

    On accept, the decision carries the canonical identity: the agreed
    stat, the deterministic rung/side, and the scope implied by the
    milestone probability (>= 0.5 milestone, else alternate-or-main as
    the deterministic ladder position states: at the cross-book main
    point -> main, otherwise alternate). Pure; no API calls.
    """
    view: Mapping[str, Any] = parser_view or {}
    candidate = view.get("candidate_stat", entry.get("parser_candidate_stat", entry.get("candidate_stat")))
    reasons: list[str] = []

    # 1. Deterministic rung/side first -- before any model output is read.
    det = deterministic_rung(entry)
    raw_point = entry.get("raw_point")
    if det is None:
        reasons.append("no_deterministic_rung_parse")
        rung = side = None
    else:
        rung, side = det
        if not isinstance(raw_point, (int, float)) or float(raw_point) != rung:
            reasons.append(f"rung_mismatch:parsed={rung}!=raw_point={raw_point}")

    # 2. Choice stat must exactly equal the parser candidate stat.
    pick = _choice_pick(answer)
    if pick is None:
        reasons.append("missing_choice_stat")
    elif pick == UNKNOWN_OPTION:
        reasons.append("choice_stat_unknown")
    elif candidate is None:
        reasons.append("no_parser_candidate_stat")
    elif pick != candidate:
        reasons.append(f"choice_stat_mismatch:{pick}!={candidate}")

    # 3. noul >= 0.95 (confidence in the scope call).
    p_milestone = _noul_probability(answer, QUESTION_MILESTONE)
    noul: Optional[float] = None
    if p_milestone is None:
        reasons.append("missing_milestone_probability")
    else:
        noul = max(p_milestone, 1.0 - p_milestone)
        if noul < NOUL_MIN:
            reasons.append(f"noul_below_threshold:{noul:.4f}<{NOUL_MIN}")

    # 4. P(unknown) <= 0.10; missing fails closed.
    p_unknown = _noul_probability(answer, QUESTION_UNKNOWN)
    if p_unknown is None:
        reasons.append("missing_unknown_probability")
    elif p_unknown > UNKNOWN_MAX:
        reasons.append(f"unknown_above_threshold:{p_unknown:.4f}>{UNKNOWN_MAX}")

    accepted = not reasons
    scope: Optional[str] = None
    if accepted:
        if p_milestone is not None and p_milestone >= 0.5:
            scope = "milestone"
        else:
            main_point = view.get("cross_book_main_point", entry.get("cross_book_main_point"))
            if main_point is not None and rung is not None and float(main_point) == rung:
                scope = "main"
            else:
                scope = "alternate"
    return {
        "decision": "accept" if accepted else "quarantine",
        "accepted": accepted,
        "reasons": reasons,
        "stat": pick if accepted else None,
        "candidate_stat": candidate,
        "rung": rung if accepted else None,
        "side": side if accepted else None,
        "scope": scope,
        "noul": noul,
        "p_milestone": p_milestone,
        "p_unknown": p_unknown,
    }
