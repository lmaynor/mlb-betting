"""mlb_core.alerts.beezy_score must agree with beezy-vip/lib/beezy-score.ts (the source of truth)."""
import re
from pathlib import Path

import pytest

from mlb_core.alerts import beezy_score as bs

TS = Path(__file__).resolve().parents[1] / "beezy-vip" / "lib" / "beezy-score.ts"


def test_constants_match_the_typescript_source():
    src = TS.read_text()
    w = re.search(r"SCORE_WEIGHTS = \{ edge: (\d+), consensus: (\d+), hit: (\d+) \}", src).groups()
    assert dict(zip(("edge", "consensus", "hit"), map(int, w))) == bs.WEIGHTS
    c = re.search(r"TIER_CUTS = \{ strong: (\d+), lean: (\d+) \}", src).groups()
    assert {"strong": int(c[0]), "lean": int(c[1])} == bs.TIER_CUTS
    assert int(re.search(r"HR_MODEL_ONLY_CAP = (\d+)", src).group(1)) == bs.HR_MODEL_ONLY_CAP
    assert int(re.search(r"HR_MIN_BOOKS = (\d+)", src).group(1)) == bs.HR_MIN_BOOKS


# Golden cases lifted from beezy-vip/tests/beezy-score.test.ts (values asserted there against the TS implementation).
def test_golden_52_edge_30_consensus_4_hit_17():
    assert bs.beezy_score(0.076, bs.books_from_notes("x (5 books)"), 0.452) == 52


def test_no_edge_scores_zero_however_many_books_agree():
    assert bs.beezy_score(0, 13, 0.5) == 0
    assert bs.beezy_score(-0.03, 13, 0.5) == 0


def test_edge_fraction_and_percent_are_the_same():
    assert bs.beezy_score(0.08, None, 0.45, "K") == bs.beezy_score(8, None, 0.45, "K")


def test_capped_at_100():
    assert bs.beezy_score(0.5, 20, 0.9) <= 100


def test_longshot_scores_below_coinflip_at_the_same_edge():
    assert bs.beezy_score(0.17, None, 0.04) < bs.beezy_score(0.17, None, 0.5)


def test_hr_model_only_is_capped_inside_lean_unless_books_back_it():
    assert bs.beezy_score(0.15, None, 0.5, "K") >= bs.TIER_CUTS["strong"]
    assert bs.beezy_score(0.15, None, 0.5, "HR") == bs.HR_MODEL_ONLY_CAP
    assert bs.tier(bs.beezy_score(0.15, None, 0.5, "HR")) == "lean"
    assert bs.beezy_score(0.15, 3, 0.5, "HR") == bs.HR_MODEL_ONLY_CAP
    assert bs.beezy_score(0.15, 8, 0.5, "HR") >= bs.TIER_CUTS["strong"]


def test_rounding_is_half_up_like_math_round():
    # 0.125% edge = exactly 0.5 edge points; 3 books = 0 consensus; no hit prob: JS Math.round(0.5) = 1, not banker's 0
    assert bs.beezy_score(0.00125, 3, None) == 1


@pytest.mark.parametrize("score,expected", [(70, "strong"), (69, "lean"), (45, "lean"), (44, "watch")])
def test_tier_cuts(score, expected):
    assert bs.tier(score) == expected
