"""Arm T configuration: Argon's minutes-program rulings, encoded.

Source of truth: the Argon consult of 2026-11-11/12, saved verbatim at
``~/workspace/beezy/profitability/argon-minutes-program-VERBATIM-2026-11-11.md``
(Q1 kill bar, Q4 input contract, Q5 split / decision-time / cleansing
amendments). This module ENCODES those rulings as constants and a frozen
config object. It does not run anything.

Two layers live here:

- The LOADER contract (Q4 + Q5): windows, caps, the mention sentence filter
  vocabulary, the migration-stratum rules, decision-time discipline and the
  fallback amendment. ``prose_loader`` reads these; do not restate the
  numbers there.
- The GRADED-RUN bars (Q1): recorded so the graded runner (a separate,
  Luc-authorized step) grades against exactly these numbers. Note the
  log-loss prong here is TIGHTER than the one in ``grade_t`` (which only
  requires Arm T's band log-loss to beat the dev base rate): Argon requires
  a >= 0.010-nat margin AND p < 0.05 on a date-clustered paired test. The
  graded wiring must use THIS prong; see ``GRADED_BARS``.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Season split (Q5, confirmed identical to Arm S)
# ---------------------------------------------------------------------------

DEV_SEASON_STARTS: tuple[int, ...] = (2021, 2022, 2023)   # 2021-22 .. 2023-24
TEST_SEASON_STARTS: tuple[int, ...] = (2024, 2025)        # 2024-25, 2025-26

# ---------------------------------------------------------------------------
# Q4 input contract
# ---------------------------------------------------------------------------

#: Subject items: the most recent SUBJECT_CAP deduplicated items where the
#: case player is the headline/subject, published within
#: [decision_report_ts - SUBJECT_WINDOW_DAYS, decision_report_ts].
SUBJECT_WINDOW_DAYS = 14
SUBJECT_CAP = 5

#: Mention items: admissible only under the sentence-level filter, capped at
#: the MENTION_CAP most recent items within
#: [decision_report_ts - MENTION_WINDOW_HOURS, decision_report_ts], carried
#: in a separate field at <= MENTION_WEIGHT x the weight of subject items.
MENTION_WINDOW_HOURS = 72
MENTION_CAP = 2
MENTION_WEIGHT = 0.25

#: Workload / availability tokens for the mention sentence filter (Argon's
#: list, verbatim). A mention sentence must contain the case player's last
#: name AND at least one of these. Matching is case-insensitive substring.
WORKLOAD_TOKENS: tuple[str, ...] = (
    "minute", "limit", "restriction", "cap", "burst", "stint", "ramp",
    "start", "bench", "available", "questionable", "probable", "out",
    "g league", "practice", "shootaround", "conditioning", "back-to-back",
)

#: Header rendered above the mention excerpts in the classifier context:
#: the separate-field instruction Q4 requires.
MENTION_FIELD_HEADER = (
    "TEAMMATE MENTION EXCERPTS (secondary evidence): the sentences below "
    "come from news items headlined by OTHER players that mention this "
    "player. Treat them as secondary: when they conflict with the primary "
    "subject items above, they carry at most 0.25x the weight of a subject "
    "item."
)

#: Migration-stratum rules (Q4): hard-exclude from BOTH fit and test.
#: (a) any item dated on or before this instant (the 2018-09-14 platform
#:     migration/backfill stratum);
MIGRATION_STRATUM_CUTOFF = "2018-09-15T23:59:59+00:00"
#: (b) any item in a same-feed cluster of >= STRATA_CLUSTER_MIN items
#:     sharing an identical minute-level publication timestamp;
STRATA_CLUSTER_MIN = 3
#: (c) any item published before the player's NBA debut / the start of the
#:     case's absence spell (applied when the caller supplies the spell
#:     start; see prose_loader.slice_case_prose).

#: Zero-subject rule (Q4): a case with 0 valid subject items in the window
#: is an ABSTENTION (fail-closed). Mentions alone never drive a prediction.

# ---------------------------------------------------------------------------
# Q5 decision-time discipline + fallback amendment
# ---------------------------------------------------------------------------

#: Decision time is the last official report published at least this many
#: minutes before scheduled tip (mirrors cases.DECISION_LEAD_MIN).
DECISION_LEAD_MIN = 60

#: Fallback amendment: when the T-60 stamp is unbanked and the loader falls
#: back to an earlier banked stamp, that stamp must sit within
#: [tip - FALLBACK_MAX_AGE_MIN, tip - DECISION_LEAD_MIN] or the case is
#: excluded.
FALLBACK_MAX_AGE_MIN = 360

#: The official report's expected stamp grid: hourly at :30 ET, hours
#: 11..23, on the day before and the day of the game (late-evening reports
#: cover the next day's games). Verified against the banked backfill:
#: report_ts_et values sit at :30 past the slot hour across all five
#: seasons (rare :00 exceptions exist and are banked stamps, never the
#: grid's last word -- the freeze always uses an actual banked timestamp).
STAMP_GRID_HOURS_ET: tuple[int, ...] = tuple(range(11, 24))
STAMP_GRID_MINUTE_ET = 30

# ---------------------------------------------------------------------------
# Q1 graded-run bars (ENCODED ONLY -- the graded run is a separate,
# Luc-authorized step; nothing in this repo's Arm T machinery executes it
# until the corpus-completeness gate in prose_loader passes)
# ---------------------------------------------------------------------------

#: Cohort Arm S-alone MAE on the missed >= 4 injury cohort (n = 227 test
#: cases), the ONLY comparator Arm T is graded against.
COHORT_ARM_S_MAE_MIN = 4.96
#: n of that cohort (test seasons pooled).
COHORT_N = 227
#: Arm T band-mapped MAE must reach <= COHORT_ARM_S_MAE * (1 - MIN_REL) on
#: the identical non-abstaining cases (= 4.46 min at full coverage).
MIN_REL_IMPROVEMENT = 0.10
MIN_ABS_IMPROVEMENT_MIN = 0.40
TARGET_MAE_MIN = 4.46
#: Minimum non-abstaining evaluated sample inside the cohort.
MIN_EVALUATED_N = 150
#: Abstention caps: <= 33.9% of the n = 227 cohort, or <= 20.0% if grouped
#: over all n = 364 missed >= 4 cases.
MAX_ABSTENTION_RATE_COHORT = 0.339
GROUPED_COHORT_N = 364
MAX_ABSTENTION_RATE_GROUPED = 0.20
#: Distribution prong: Arm T's 4-band log-loss on test must beat the
#: dev-season empirical base-rate log-loss by >= this many nats, with
#: p < LOGLOSS_MAX_P on a date-clustered paired test.
LOGLOSS_MIN_GAIN_NATS = 0.010
LOGLOSS_MAX_P = 0.05

#: FRAME LOCK (Argon frame-provenance ruling, 2026-11-13, pre-registered
#: BEFORE the NBC backfill merge completed, per the ruling's Q3): the
#: graded run uses EXCLUSIVELY the rebuilt canonical case frame
#: (case_frame.parquet, built by build_cases over the complete 13,729-file
#: GCS report backfill). A graded run executed on any other frame is VOID
#: under the ruling. The graded path must assert the loaded frame's
#: SHA-256 equals this lock and refuse (exit 2) on any mismatch.
CANONICAL_FRAME_NAME = "case_frame.parquet"
CANONICAL_FRAME_SHA256 = "86506b8a15703de7c6cd5f04bccf9b9876db4570ca96b2a4131b33786ad2ec53"
CANONICAL_FRAME_ROWS = 5365
CANONICAL_FRAME_COHORT_N = 227

#: The graded run's corpus requirement (Q1 scope lock + Q4 contract): the
#: NBC Sports (Rotoworld) archive, complete over the resolved player set.
GRADED_CORPUS = "nbc"
#: Resolved NBC players in the current map (the completeness gate compares
#: the banked set against the player map at runtime; this is the registered
#: expectation, not the mechanism).
EXPECTED_RESOLVED_NBC_PLAYERS = 386

GRADED_BARS: dict = {
    "comparator": "cohort Arm S-alone MAE on identical non-abstaining cases",
    "cohort_arm_s_mae_min": COHORT_ARM_S_MAE_MIN,
    "cohort_n": COHORT_N,
    "min_rel_improvement": MIN_REL_IMPROVEMENT,
    "min_abs_improvement_min": MIN_ABS_IMPROVEMENT_MIN,
    "target_mae_min": TARGET_MAE_MIN,
    "min_evaluated_n": MIN_EVALUATED_N,
    "max_abstention_rate_cohort": MAX_ABSTENTION_RATE_COHORT,
    "grouped_cohort_n": GROUPED_COHORT_N,
    "max_abstention_rate_grouped": MAX_ABSTENTION_RATE_GROUPED,
    "logloss_min_gain_nats": LOGLOSS_MIN_GAIN_NATS,
    "logloss_max_p_date_clustered_paired": LOGLOSS_MAX_P,
    "graded_corpus": GRADED_CORPUS,
    "fit_seasons": [f"{y}-{(y + 1) % 100:02d}" for y in DEV_SEASON_STARTS],
    "judge_seasons": [f"{y}-{(y + 1) % 100:02d}" for y in TEST_SEASON_STARTS],
}


@dataclass(frozen=True)
class ArmTConfig:
    """The loader contract as one object (defaults = Argon's numbers)."""

    subject_window_days: int = SUBJECT_WINDOW_DAYS
    subject_cap: int = SUBJECT_CAP
    mention_window_hours: int = MENTION_WINDOW_HOURS
    mention_cap: int = MENTION_CAP
    mention_weight: float = MENTION_WEIGHT
    decision_lead_min: int = DECISION_LEAD_MIN
    fallback_max_age_min: int = FALLBACK_MAX_AGE_MIN

    def graded_bars(self) -> dict:
        return dict(GRADED_BARS)
