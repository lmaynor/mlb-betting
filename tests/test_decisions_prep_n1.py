"""Tests for the N1 decisions-prep workstream (F1 fixture + F7 schema/gate).

Pure and offline: the fixture is a local JSONL file, the module
(ev/decisions_prep/n1_ladder_identity.py) makes no API calls, and the repo
conftest blocks network in tests regardless.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from ev.decisions_prep.n1_ladder_identity import (
    NOUL_MIN,
    QUESTION_IDS,
    QUESTION_MILESTONE,
    QUESTION_STAT,
    QUESTION_UNKNOWN,
    SCOPES,
    UNKNOWN_MAX,
    adjudicate,
    build_questions,
    deterministic_rung,
    is_ambiguous,
    parse_rung_side,
    select_ambiguity_set,
)

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "decisions"
    / "n1_ladder_identity"
    / "labels.jsonl"
)

REQUIRED_ENTRY_FIELDS = {
    "book",
    "event_id",
    "game",
    "player",
    "raw_market_key",
    "raw_description",
    "raw_name",
    "raw_point",
    "raw_price",
    "raw_price_side_info",
    "parser_candidate_stat",
    "cross_book_main_point",
    "label",
    "grounding",
    "grounding_detail",
    "ambiguity_class",
    "source_object",
}
REQUIRED_LABEL_FIELDS = {"canonical_stat", "rung", "side", "scope"}
AMBIGUITY_CLASSES = {"unmapped_key", "onesided_ladder_rung", "twosided_offmain_rung"}
GROUNDINGS = {"cross_book_consensus", "same_book_main_anchor+cross_book_consensus"}
CANONICAL_STATS = {
    "pass_yds", "rush_yds", "rec_yds", "receptions", "pass_tds", "anytime_td",
    "pass_attempts", "pass_completions", "rush_attempts", "interceptions",
}


def _load_labels() -> list[dict]:
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


LABELS = _load_labels()


# ---------------------------------------------------------------------------
# F1 fixture shape
# ---------------------------------------------------------------------------


def test_fixture_has_at_least_500_labels():
    assert len(LABELS) >= 500


def test_fixture_spans_at_least_four_books():
    books = Counter(e["book"] for e in LABELS)
    assert len(books) >= 4, f"only {len(books)} books: {dict(books)}"


def test_fixture_covers_all_ambiguity_classes():
    classes = Counter(e["ambiguity_class"] for e in LABELS)
    assert set(classes) == AMBIGUITY_CLASSES


def test_fixture_entries_have_required_fields():
    for e in LABELS:
        assert REQUIRED_ENTRY_FIELDS <= set(e), f"missing {REQUIRED_ENTRY_FIELDS - set(e)}"
        assert REQUIRED_LABEL_FIELDS <= set(e["label"])
        info = e["raw_price_side_info"]
        assert {"sides_present", "over_price", "under_price", "yes_price"} <= set(info)
        assert info["sides_present"], "a quoted entry posts at least one side"


def test_fixture_labels_are_consistent():
    for e in LABELS:
        lab = e["label"]
        assert lab["canonical_stat"] in CANONICAL_STATS
        assert isinstance(lab["rung"], (int, float))
        assert lab["rung"] == e["raw_point"], "label rung must equal the raw point"
        assert lab["side"] in {"over", "under", "yes", "no"}
        assert lab["side"] in e["raw_price_side_info"]["sides_present"]
        assert lab["scope"] in SCOPES
        assert e["ambiguity_class"] in AMBIGUITY_CLASSES
        assert e["grounding"] in GROUNDINGS
        assert e["grounding_detail"]
        # The parser candidate is the key table's stat: null exactly for
        # the unmapped-key class, the label stat everywhere else (the
        # label's stat is corroborated cross-book, never copied from the
        # extractor's resolution of this same entry).
        if e["ambiguity_class"] == "unmapped_key":
            assert e["parser_candidate_stat"] is None
        else:
            assert e["parser_candidate_stat"] == lab["canonical_stat"]


def test_fixture_entries_are_unique_quotes():
    keys = [
        (e["book"], e["event_id"], e["player_norm"], e["raw_market_key"], e["raw_point"])
        for e in LABELS
    ]
    assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------
# parse_rung_side
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Over 49.5", (49.5, "over")),
        ("Under 6.5", (6.5, "under")),
        ("over 49.5", (49.5, "over")),
        ("  Under 224.5  ", (224.5, "under")),
        ("Over 49.5 Receiving Yards", (49.5, "over")),
        ("Under 6.5 Receptions", (6.5, "under")),
        ("50+", (49.5, "yes")),
        ("50+ Yds", (49.5, "yes")),
        ("50+ Receiving Yds", (49.5, "yes")),
        ("100+ Rushing Yards", (99.5, "yes")),
        ("1+ TD", (0.5, "yes")),
        ("3+ Touchdowns", (2.5, "yes")),
    ],
)
def test_parse_rung_side_parses(text, expected):
    assert parse_rung_side(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Yes",
        "No",
        "Over Ja'Marr Chase",      # outcome name: no number
        "Over",                    # no number
        "49.5",                    # bare number, no side
        "fifty yards",
        "Over -3.5",               # signed numbers are not rungs here
        "Over 49.5 to 59.5",       # a range is not a rung
        "50+ Yds Guaranteed",      # trailing prose beyond the stat phrase
        "0+",                      # a zero threshold is not a milestone
        "",
        None,
        49.5,
    ],
)
def test_parse_rung_side_rejects(text):
    assert parse_rung_side(text) is None


def test_deterministic_rung_from_raw_point_and_name():
    entry = {"raw_name": "Over Michael Wilson (ARI)", "raw_description": "Michael Wilson (ARI)",
             "raw_point": 44.5}
    assert deterministic_rung(entry) == (44.5, "over")
    entry = {"raw_name": "Yes", "raw_description": "Elijah Higgins", "raw_point": 0.5}
    assert deterministic_rung(entry) == (0.5, "yes")
    entry = {"raw_name": "Yes", "raw_description": "Someone", "raw_point": None}
    assert deterministic_rung(entry) is None


# ---------------------------------------------------------------------------
# Ambiguity pre-filter (synthetic entries)
# ---------------------------------------------------------------------------


def _entry(**kw):
    base = {
        "book": "bookx",
        "player": "Test Player",
        "raw_market_key": "player_rec_yds",
        "raw_description": "Test Player",
        "raw_name": "Over Test Player",
        "raw_point": 44.5,
        "raw_price_side_info": {"sides_present": ["over", "under"]},
    }
    base.update(kw)
    return base


def _view(**kw):
    base = {
        "candidate_stat": "rec_yds",
        "two_sided": True,
        "points_for_book_player": [44.5],
        "cross_book_main_point": 44.5,
    }
    base.update(kw)
    return base


def test_filter_unmapped_key_is_ambiguous():
    assert is_ambiguous(_entry(raw_market_key="player_touchdown_passes"),
                        _view(candidate_stat=None))


def test_filter_exact_main_match_never_selected():
    assert not is_ambiguous(_entry(), _view())


def test_filter_standalone_single_point_never_selected():
    assert not is_ambiguous(_entry(raw_point=39.5), _view(points_for_book_player=[39.5]))


def test_filter_one_sided_ladder_rung_selected():
    e = _entry(raw_price_side_info={"sides_present": ["yes"]}, raw_name="Yes")
    v = _view(two_sided=False, points_for_book_player=[174.5, 199.5, 224.5],
              cross_book_main_point=174.5)
    assert is_ambiguous(e, v)


def test_filter_two_sided_offmain_rung_selected():
    e = _entry(raw_point=64.5)
    v = _view(points_for_book_player=[44.5, 64.5], cross_book_main_point=44.5)
    assert is_ambiguous(e, v)


def test_filter_two_sided_main_rung_of_ladder_not_selected():
    v = _view(points_for_book_player=[44.5, 64.5], cross_book_main_point=44.5)
    assert not is_ambiguous(_entry(raw_point=44.5), v)


def test_filter_ladder_rung_without_main_reference_selected():
    e = _entry(raw_point=64.5)
    v = _view(points_for_book_player=[44.5, 64.5], cross_book_main_point=None)
    assert is_ambiguous(e, v)


def test_select_ambiguity_set_mixed_inputs():
    entries = [_entry(), _entry(raw_market_key="player_touchdown_passes"),
               _entry(raw_point=64.5)]
    views = [_view(), _view(candidate_stat=None),
             _view(points_for_book_player=[44.5, 64.5])]
    picked = select_ambiguity_set(entries, views)
    assert picked == [entries[1], entries[2]]
    # A callable parser view works too.
    assert select_ambiguity_set(entries, lambda e: views[entries.index(e)]) == picked


# ---------------------------------------------------------------------------
# Question pack
# ---------------------------------------------------------------------------


def test_build_questions_envelope_style():
    q = build_questions(_entry(book="pinnacle", raw_market_key="player_touchdown_passes"),
                        ["pass_tds", "anytime_td"])
    assert set(q) == set(QUESTION_IDS)
    stat_q = q[QUESTION_STAT]
    assert stat_q["type"] == "choice"
    assert set(stat_q["criteria"]) == {"pass_tds", "anytime_td", "unknown"}
    assert q[QUESTION_MILESTONE]["type"] == "noul"
    assert q[QUESTION_UNKNOWN]["type"] == "noul"
    for qid in QUESTION_IDS:
        assert q[qid]["instructions"]


# ---------------------------------------------------------------------------
# adjudicate gate
# ---------------------------------------------------------------------------


def _gate_entry(**kw):
    base = {
        "book": "pinnacle",
        "player": "Cam Ward",
        "raw_market_key": "player_touchdown_passes",
        "raw_description": "Cam Ward",
        "raw_name": "Over Cam Ward",
        "raw_point": 0.5,
        "parser_candidate_stat": "pass_tds",
        "cross_book_main_point": 0.5,
    }
    base.update(kw)
    return base


def _accept_answer(**kw):
    base = {
        QUESTION_STAT: {"choice": "pass_tds"},
        QUESTION_MILESTONE: {"probability": 0.01},
        QUESTION_UNKNOWN: {"probability": 0.02},
    }
    base.update(kw)
    return base


def test_adjudicate_accept_path():
    d = adjudicate(_gate_entry(), _accept_answer())
    assert d["accepted"] and d["decision"] == "accept"
    assert d["stat"] == "pass_tds" and d["rung"] == 0.5 and d["side"] == "over"
    assert d["scope"] == "main"          # at the cross-book main point, not a milestone
    assert d["noul"] >= NOUL_MIN and d["p_unknown"] <= UNKNOWN_MAX


def test_adjudicate_accept_milestone_scope_flat_answers():
    ans = {QUESTION_STAT: "pass_tds", QUESTION_MILESTONE: 0.98, QUESTION_UNKNOWN: 0.05}
    d = adjudicate(_gate_entry(), ans)
    assert d["accepted"] and d["scope"] == "milestone" and d["noul"] == pytest.approx(0.98)


def test_adjudicate_accept_alternate_scope():
    d = adjudicate(_gate_entry(raw_point=2.5, raw_name="Over Cam Ward"), _accept_answer())
    assert d["accepted"] and d["scope"] == "alternate" and d["rung"] == 2.5


def test_adjudicate_quarantines_on_rung_mismatch():
    # The deterministic parse (raw_point 0.5) cannot be reconciled with a
    # milestone text rung of 49.5: quarantined regardless of model scores.
    e = _gate_entry(raw_description="50+ Passing Yds", raw_name="Yes")
    d = adjudicate(e, _accept_answer())
    assert not d["accepted"] and any(r.startswith("rung_mismatch") for r in d["reasons"])


def test_adjudicate_quarantines_without_deterministic_rung():
    d = adjudicate(_gate_entry(raw_point=None), _accept_answer())
    assert not d["accepted"] and "no_deterministic_rung_parse" in d["reasons"]


def test_adjudicate_quarantines_on_stat_mismatch():
    ans = _accept_answer(**{QUESTION_STAT: {"choice": "anytime_td"}})
    d = adjudicate(_gate_entry(), ans)
    assert not d["accepted"] and any(r.startswith("choice_stat_mismatch") for r in d["reasons"])


def test_adjudicate_quarantines_on_unknown_choice():
    ans = _accept_answer(**{QUESTION_STAT: {"choice": "unknown"}})
    d = adjudicate(_gate_entry(), ans)
    assert not d["accepted"] and "choice_stat_unknown" in d["reasons"]


def test_adjudicate_quarantines_on_low_noul():
    ans = _accept_answer(**{QUESTION_MILESTONE: {"probability": 0.40}})  # noul = 0.60
    d = adjudicate(_gate_entry(), ans)
    assert not d["accepted"] and any(r.startswith("noul_below_threshold") for r in d["reasons"])


def test_adjudicate_quarantines_on_high_unknown_probability():
    ans = _accept_answer(**{QUESTION_UNKNOWN: {"probability": 0.25}})
    d = adjudicate(_gate_entry(), ans)
    assert not d["accepted"] and any(r.startswith("unknown_above_threshold") for r in d["reasons"])


def test_adjudicate_quarantines_on_missing_probabilities():
    d = adjudicate(_gate_entry(), {QUESTION_STAT: {"choice": "pass_tds"}})
    assert not d["accepted"]
    assert "missing_milestone_probability" in d["reasons"]
    assert "missing_unknown_probability" in d["reasons"]
