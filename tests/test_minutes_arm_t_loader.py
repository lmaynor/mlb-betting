"""Tests for the Arm T Q4/Q5 prose loader (prose_loader) and the Q4
mention-field wiring in the Decisions classifier.

All fixtures are synthetic and offline. The contract under test is Argon's
2026-11-11/12 rulings (arm_t_config): the Q4 input contract, the Q5
decision-time fallback amendment, and the run-time cleansing view.
"""
from __future__ import annotations

import pandas as pd
import pytest

from nba.minutes_restriction.arm_t_config import MENTION_FIELD_HEADER
from nba.minutes_restriction.decisions_client import (
    DecisionsBandClassifier,
)
from nba.minutes_restriction.prose_loader import (
    ProseCorpus,
    ProseItem,
    corpus_completeness,
    matching_sentences,
    predict_case,
    resolve_decision_time,
    slice_case_prose,
    strata_cluster_keys,
)

ET = "America/New_York"
GAME_DATE = pd.Timestamp("2025-01-04").date()
TIP = pd.Timestamp("2025-01-04 19:00", tz=ET)
FREEZE = pd.Timestamp("2025-01-04 17:30", tz=ET)


def make_item(player, subject, ts, headline="H", body="B", **kw):
    ts = pd.Timestamp(ts)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ProseItem(
        corpus_key=(float(ts.timestamp()), headline, subject),
        source=kw.get("source", "nbc"),
        page_player=player,
        subject_player=subject,
        is_subject_for_page=(subject == player),
        published_ts=ts,
        headline=headline,
        body=body,
        updated_ts=kw.get("updated_ts"),
        migration_stratum=kw.get("migration_stratum", False),
        subject_name_match=kw.get("subject_name_match", False),
    )


def make_corpus(items_by_player):
    corpus = ProseCorpus(name="nbc")
    from nba.minutes_restriction.cases import _norm_name

    for player, items in items_by_player.items():
        corpus.items_by_player[_norm_name(player)] = sorted(
            items, key=lambda it: it.published_ts
        )
        for it in items:
            corpus.pool_by_key.setdefault(it.corpus_key, it)
    return corpus


def decision(freeze=FREEZE):
    from nba.minutes_restriction.prose_loader import DecisionTime

    return DecisionTime(
        freeze_ts=freeze, ideal_stamp=freeze, last_banked_stamp=freeze,
        was_fallback=False, excluded=False, exclusion_reason=None,
    )


CASE = {"case_id": "g1-1-return", "player": "Kawhi Leonard", "team": "LAC",
        "game_date": "2025-01-04", "games_missed": 26, "baseline_minutes": 32.0,
        "designation": "Questionable"}


# ---------------------------------------------------------------------------
# Mention sentence filter: BOTH conditions required (Q4)
# ---------------------------------------------------------------------------

def test_mention_sentence_filter_requires_name_and_token():
    text = (
        "Leonard is expected to be on a minutes restriction Saturday. "
        "Leonard enjoyed the weather in Los Angeles. "
        "The backup will see an increased workload with the start. "
        "Leonard ramped up his conditioning work this week."
    )
    got = matching_sentences(text, "leonard")
    assert len(got) == 2
    assert "restriction" in got[0]
    assert "ramped" in got[1]


def test_mention_excerpts_come_from_teammate_items_only():
    subject = make_item("Kawhi Leonard", "Kawhi Leonard",
                        "2025-01-04T20:00:00Z", "Leonard update",
                        "Leonard will play Saturday.")
    teammate = make_item(
        "Kawhi Leonard", "James Harden", "2025-01-04T21:00:00Z",
        "Harden stuff",
        "Harden said Leonard will be on a minutes limit in his return. "
        "Harden scored 30 last night.",
    )
    corpus = make_corpus({"Kawhi Leonard": [subject, teammate]})
    out = slice_case_prose(CASE, corpus, decision())
    assert not out.abstention
    assert out.audit.n_subject_items == 1
    assert out.audit.n_mention_items == 1
    assert any("minutes limit" in e for e in out.mention_excerpts)
    assert not any("scored 30" in e for e in out.mention_excerpts)


def test_subject_role_survives_banked_flag_id_type_bug():
    # Banked NBC records can carry is_subject=False for the page player's
    # own items when the puller compared an int id to a str id; the
    # record's subject_player name still identifies the subject.
    item = make_item("Anthony Davis", "Anthony Davis",
                     "2025-01-04T20:00:00Z", "Davis update", "Davis plays.",
                     subject_name_match=True)
    item.is_subject_for_page = False  # the banked flag, as the bug wrote it
    corpus = make_corpus({"Anthony Davis": [item]})
    case = dict(CASE, player="Anthony Davis")
    out = slice_case_prose(case, corpus, decision())
    assert not out.abstention
    assert out.audit.n_subject_items == 1


def test_mention_window_and_cap():
    subject = make_item("Kawhi Leonard", "Kawhi Leonard",
                        "2025-01-04T20:00:00Z", "S", "Leonard plays.")
    # Three admissible mention items: one inside 72h is too old, two inside.
    old = make_item("Kawhi Leonard", "James Harden", "2025-01-01T20:00:00Z",
                    "Old", "Leonard is ramping up his practice work.")
    m1 = make_item("Kawhi Leonard", "James Harden", "2025-01-03T23:00:00Z",
                   "M1", "Leonard went through shootaround today.")
    m2 = make_item("Kawhi Leonard", "Norman Powell", "2025-01-04T21:00:00Z",
                   "M2", "Leonard will come off the bench on a cap.")
    corpus = make_corpus({"Kawhi Leonard": [subject, old, m1, m2]})
    out = slice_case_prose(CASE, corpus, decision())
    assert out.audit.n_mention_items == 2
    assert out.audit.n_mention_items_considered == 2  # the old one never enters the 72h pool


# ---------------------------------------------------------------------------
# Zero-subject abstention: fail closed, mentions never drive a prediction
# ---------------------------------------------------------------------------

def test_zero_subject_items_abstains_even_with_mentions():
    teammate = make_item(
        "Kawhi Leonard", "James Harden", "2025-01-04T21:00:00Z", "Harden",
        "Leonard is probable and will start Saturday.",
    )
    corpus = make_corpus({"Kawhi Leonard": [teammate]})
    out = slice_case_prose(CASE, corpus, decision())
    assert out.abstention
    assert out.abstention_reason == "zero_subject_items"
    assert out.subject_blurbs == []


def test_predict_case_never_calls_classifier_on_abstention():
    class ExplodingClassifier:
        def classify_case(self, *a, **k):  # pragma: no cover - must not run
            raise AssertionError("classifier called on an abstention")

    teammate = make_item("Kawhi Leonard", "James Harden",
                         "2025-01-04T21:00:00Z", "H", "Leonard will start.")
    corpus = make_corpus({"Kawhi Leonard": [teammate]})
    out = slice_case_prose(CASE, corpus, decision())
    pred = predict_case(ExplodingClassifier(), CASE, out)
    assert pred.abstention
    assert pred.abstention_reason == "zero_subject_items"


# ---------------------------------------------------------------------------
# Subject window + cap
# ---------------------------------------------------------------------------

def test_subject_window_and_cap():
    items = []
    days = ["2024-12-28", "2024-12-29", "2024-12-30", "2024-12-31",
            "2025-01-01", "2025-01-02", "2025-01-03"]  # all inside 14d
    for i, d in enumerate(days):
        items.append(make_item("Kawhi Leonard", "Kawhi Leonard",
                               f"{d}T20:00:00Z", f"S{i}", "body"))
    too_old = make_item("Kawhi Leonard", "Kawhi Leonard",
                        "2024-12-20T20:00:00Z", "OLD", "body")  # > 14d before freeze
    at_freeze = make_item("Kawhi Leonard", "Kawhi Leonard",
                          "2025-01-04T22:30:00Z", "AT", "body")  # == freeze (17:30 ET)
    corpus = make_corpus({"Kawhi Leonard": items + [too_old, at_freeze]})
    out = slice_case_prose(CASE, corpus, decision())
    assert out.audit.n_subject_items == 5
    headlines = [b["headline"] for b in out.subject_blurbs]
    assert headlines == ["S3", "S4", "S5", "S6", "AT"]  # most recent 5, oldest first


# ---------------------------------------------------------------------------
# Migration strata: identical-timestamp cluster, date rule, banked flag
# ---------------------------------------------------------------------------

def test_strata_cluster_and_date_rule_drop_items():
    cluster_ts = "2025-01-02T15:04:00Z"
    cluster = [
        make_item("Kawhi Leonard", "Kawhi Leonard", cluster_ts, f"C{i}", "x")
        for i in range(3)
    ]
    migration = make_item("Kawhi Leonard", "Kawhi Leonard",
                          "2018-09-14T05:00:00Z", "MIG", "x")
    flagged = make_item("Kawhi Leonard", "Kawhi Leonard",
                        "2025-01-03T20:00:00Z", "FLAG", "x", migration_stratum=True)
    good = make_item("Kawhi Leonard", "Kawhi Leonard",
                     "2025-01-04T20:00:00Z", "GOOD", "Leonard plays.")
    corpus = make_corpus({"Kawhi Leonard": cluster + [migration, flagged, good]})
    keys = strata_cluster_keys(corpus)
    assert {it.corpus_key for it in cluster} <= keys
    out = slice_case_prose(CASE, corpus, decision(), cluster_keys=keys)
    assert [b["headline"] for b in out.subject_blurbs] == ["GOOD"]
    # The 2018 item is outside the 14d window, so only in-window drops count:
    # the 3 cluster items + the banked-flag item.
    assert out.audit.n_strata_dropped == 4


def test_midnight_stamps_are_not_a_strata_cluster():
    # Date-granular sources (RotoWire) stamp a whole day's items at
    # 00:00 UTC; >= 3 of them is normal publication, not a stratum.
    items = [
        make_item("Kawhi Leonard", "Kawhi Leonard", "2025-01-02T00:00:00Z",
                  f"D{i}", "x", source="rotowire")
        for i in range(4)
    ]
    corpus = make_corpus({"Kawhi Leonard": items})
    assert strata_cluster_keys(corpus) == set()


def test_pre_spell_items_dropped_when_spell_start_supplied():
    pre = make_item("Kawhi Leonard", "Kawhi Leonard", "2024-12-30T20:00:00Z",
                    "PRE", "x")
    post = make_item("Kawhi Leonard", "Kawhi Leonard", "2025-01-03T20:00:00Z",
                     "POST", "x")
    corpus = make_corpus({"Kawhi Leonard": [pre, post]})
    out = slice_case_prose(
        CASE, corpus, decision(),
        absence_start=pd.Timestamp("2025-01-01T00:00:00Z"),
    )
    assert [b["headline"] for b in out.subject_blurbs] == ["POST"]
    assert out.audit.n_pre_spell_dropped == 1


# ---------------------------------------------------------------------------
# Retrospective edits: stripped at run time, raw corpus untouched
# ---------------------------------------------------------------------------

def test_retrospective_edit_stripped():
    edited = make_item("Kawhi Leonard", "Kawhi Leonard", "2025-01-03T20:00:00Z",
                       "EDITED", "post-game text",
                       updated_ts=pd.Timestamp("2025-01-05T03:00:00Z"))
    clean = make_item("Kawhi Leonard", "Kawhi Leonard", "2025-01-04T20:00:00Z",
                      "CLEAN", "pre-game text",
                      updated_ts=pd.Timestamp("2025-01-04T21:00:00Z"))
    corpus = make_corpus({"Kawhi Leonard": [edited, clean]})
    out = slice_case_prose(CASE, corpus, decision())
    assert [b["headline"] for b in out.subject_blurbs] == ["CLEAN"]
    assert out.audit.n_retro_stripped == 1


# ---------------------------------------------------------------------------
# Decision time: fallback amendment (Q5)
# ---------------------------------------------------------------------------

def _stamps(*specs):
    return [pd.Timestamp(s, tz=ET) for s in specs]


def test_decision_freeze_is_designation_stamp_when_listed():
    banked = _stamps("2025-01-04 13:30", "2025-01-04 17:30")
    out = resolve_decision_time(
        game_date=GAME_DATE, tip_ts=TIP, banked_stamps=banked,
        designation_ts=pd.Timestamp("2025-01-04 17:30", tz=ET),
    )
    assert not out.excluded and not out.was_fallback
    assert out.freeze_ts == pd.Timestamp("2025-01-04 17:30", tz=ET)


def test_decision_freeze_unlisted_player_uses_last_banked_stamp():
    banked = _stamps("2025-01-04 13:30", "2025-01-04 17:30")
    out = resolve_decision_time(
        game_date=GAME_DATE, tip_ts=TIP, banked_stamps=banked, designation_ts=None,
    )
    assert not out.excluded
    assert out.freeze_ts == pd.Timestamp("2025-01-04 17:30", tz=ET)


def test_fallback_inside_window_rolls_back_and_survives():
    # The 17:30 grid stamp is unbanked; the loader falls back to 13:30,
    # 330 minutes before the 19:00 tip: inside [tip-360, tip-60].
    banked = _stamps("2025-01-04 13:30")
    out = resolve_decision_time(
        game_date=GAME_DATE, tip_ts=TIP, banked_stamps=banked, designation_ts=None,
    )
    assert out.was_fallback and not out.excluded
    assert out.freeze_ts == pd.Timestamp("2025-01-04 13:30", tz=ET)


def test_fallback_outside_window_excludes_case():
    # Only yesterday's late stamp is banked: the fallback freeze would sit
    # ~19.5h before tip, outside the amendment's window.
    banked = _stamps("2025-01-03 23:30")
    out = resolve_decision_time(
        game_date=GAME_DATE, tip_ts=TIP, banked_stamps=banked, designation_ts=None,
    )
    assert out.was_fallback and out.excluded
    assert out.exclusion_reason == "fallback_stamp_outside_window"
    assert out.freeze_ts is None


def test_old_designation_stamp_is_not_a_fallback_when_grid_stamp_banked():
    banked = _stamps("2025-01-03 13:30", "2025-01-04 17:30")
    out = resolve_decision_time(
        game_date=GAME_DATE, tip_ts=TIP, banked_stamps=banked,
        designation_ts=pd.Timestamp("2025-01-03 13:30", tz=ET),
    )
    assert not out.was_fallback and not out.excluded
    assert out.freeze_ts == pd.Timestamp("2025-01-03 13:30", tz=ET)


def test_no_banked_stamp_excludes():
    out = resolve_decision_time(
        game_date=GAME_DATE, tip_ts=TIP, banked_stamps=[], designation_ts=None,
    )
    assert out.excluded
    assert out.exclusion_reason == "no_banked_stamp_at_or_before_t60"


# ---------------------------------------------------------------------------
# Completeness gate arithmetic
# ---------------------------------------------------------------------------

def test_corpus_completeness_reports_missing():
    corpus = ProseCorpus(name="nbc")
    corpus.banked_ids = {"1", "2"}
    report = corpus_completeness(corpus, {"1", "2", "3"})
    assert not report.complete
    assert report.n_banked == 2 and report.n_resolved == 3
    assert report.missing_ids == ["3"]
    assert corpus_completeness(corpus, {"1", "2"}).complete


# ---------------------------------------------------------------------------
# Classifier wiring: the separate mention field (Q4)
# ---------------------------------------------------------------------------

def _stub_classifier():
    def transport(url, headers, body, timeout):
        return 200, {
            "answers": {
                "minutes_band": {
                    "type": "choice",
                    "choice": "light_restriction",
                    "probabilities": {
                        "out": 0.05, "heavy_restriction": 0.25,
                        "light_restriction": 0.45, "full_workload": 0.25,
                    },
                    "confidence": 0.6,
                }
            }
        }

    return DecisionsBandClassifier(api_key="test", transport=transport)


def test_classifier_context_separates_mentions_with_weight_header():
    clf = _stub_classifier()
    ctx = clf.build_context(
        CASE,
        [{"published_ts": "2025-01-04", "headline": "H", "body": "B"}],
        mentions=["Leonard will be on a minutes limit."],
    )
    assert MENTION_FIELD_HEADER in ctx
    assert "0.25x" in ctx
    assert "Leonard will be on a minutes limit." in ctx
    ctx_plain = clf.build_context(
        CASE, [{"published_ts": "2025-01-04", "headline": "H", "body": "B"}]
    )
    assert MENTION_FIELD_HEADER not in ctx_plain


def test_predict_case_passes_subjects_and_mentions():
    subject = make_item("Kawhi Leonard", "Kawhi Leonard",
                        "2025-01-04T20:00:00Z", "S", "Leonard plays.")
    teammate = make_item("Kawhi Leonard", "James Harden",
                         "2025-01-04T21:00:00Z", "T",
                         "Leonard will start and is probable for Saturday.")
    corpus = make_corpus({"Kawhi Leonard": [subject, teammate]})
    out = slice_case_prose(CASE, corpus, decision())
    pred = predict_case(_stub_classifier(), CASE, out)
    assert not pred.abstention
    assert pred.band == "light_restriction"
    assert out.audit.raw_token_count > out.audit.cleansed_token_count >= 0
    assert out.audit.n_subject_items == 1 and out.audit.n_mention_items == 1
