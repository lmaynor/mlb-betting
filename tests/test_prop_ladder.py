"""ev/prop_ladder.py: alt-line-aware parametric ladder fit (normal for yardage/points, NegBin for counts)."""
import math
from statistics import NormalDist

import pytest

from ev.prop_ladder import MAX_SD, fit_ladder, family

ND = NormalDist()


def normal_anchors(mu, sd, pts, n=4):
    return [(x, 1.0 - ND.cdf((x - mu) / sd), n) for x in pts]


def test_normal_ladder_recovers_mu_sd_from_main_plus_alternate_lines():
    f = fit_ladder(normal_anchors(62.0, 24.0, [39.5, 49.5, 59.5, 79.5]), "rec_yds", "nfl")
    assert f is not None and f.fam == "normal"
    assert f.mu == pytest.approx(62.0, abs=0.5) and f.scale == pytest.approx(24.0, abs=0.5) and f.rmse < 0.02
    assert f.p_over(69.5) == pytest.approx(1.0 - ND.cdf((69.5 - 62.0) / 24.0), abs=0.01)           # interior rung nobody quoted
    assert f.p_over(99.5) == pytest.approx(1.0 - ND.cdf((99.5 - 62.0) / 24.0), abs=0.02)          # 20 yards past the last alternate


def test_a_single_book_alternate_still_shapes_the_fit_but_weighs_less():
    base = normal_anchors(62.0, 24.0, [49.5, 59.5], n=5) + [(89.5, 0.09, 1)]                       # a one-book alternate at +1.15 sd, shaded ~3.5pp below the true 12.6%
    f = fit_ladder(base, "rec_yds", "nfl")
    clean = fit_ladder(normal_anchors(62.0, 24.0, [49.5, 59.5], n=5), "rec_yds", "nfl")
    assert f is not None and clean is not None
    truth = 1.0 - ND.cdf((79.5 - 62.0) / 24.0)
    assert abs(f.p_over(79.5) - truth) < 0.04                                                       # the 3-line fit tracks the true tail
    assert abs(clean.p_over(79.5) - truth) > abs(f.p_over(79.5) - truth)                            # 2 close lines fall back to the prior sd: worse
    assert f.distance(99.5) < clean.distance(99.5)                                                  # the alternate extends the range the fit stands on


def test_count_ladder_is_negbin_and_extends_to_rungs_never_quoted():
    from scipy import stats
    m, r = 4.2, 6.0
    pts = [2.5, 4.5, 6.5]
    anchors = [(x, float(stats.nbinom.sf(math.floor(x), r, r / (r + m))), 5) for x in pts]
    f = fit_ladder(anchors, "receptions", "nfl")
    assert f is not None and f.fam == "count" and f.rmse < 0.05
    for x in (1.5, 3.5, 5.5, 7.5):
        assert f.p_over(x) == pytest.approx(float(stats.nbinom.sf(math.floor(x), r, r / (r + m))), abs=0.03)
    assert f.p_over(3.5) > f.p_over(4.5) > f.p_over(5.5)                                            # monotone in the line


def test_the_fit_refuses_to_speak_far_from_the_quoted_lines():
    f = fit_ladder(normal_anchors(62.0, 24.0, [49.5, 59.5]), "rec_yds", "nfl")
    assert f.speaks(59.5) and f.speaks(75.5) and not f.speaks(59.5 + (MAX_SD + 0.5) * 24.0)
    assert f.distance(55.0) == 0.0 and f.distance(59.5 + f.unit) == pytest.approx(1.0, abs=0.05)


def test_flat_or_increasing_ladders_and_bad_fits_return_none():
    assert fit_ladder([(49.5, 0.3, 4), (59.5, 0.6, 4)], "rec_yds", "nfl") is None                 # increasing in the line: the fit is bad even with the prior sd
    assert fit_ladder([(49.5, 0.999, 4)], "rec_yds", "nfl") is None                                # a 99.9% price has no shape


def test_lone_anchor_needs_three_books_and_a_variance_prior():
    assert fit_ladder([(60.5, 0.5, 2)], "rec_yds", "nfl") is None
    assert fit_ladder([(60.5, 0.5, 3)], "rec_yds", "nfl") is not None
    assert fit_ladder([(24.5, 0.5, 5)], "threes", "nba") is not None                               # count: NegBin prior r=8
    assert fit_ladder([(24.5, 0.5, 5)], "unknown_normal_stat", "nfl") is not None                  # count fallback is allowed
    assert fit_ladder([(24.5, 0.5, 5)], "points", "nhl") is not None


def test_family_depends_on_sport_because_points_means_different_things():
    assert family("nba", "points") == "normal" and family("nhl", "points") == "count"
    assert family("nfl", "receptions") == "count" and family("nfl", "pass_yds") == "normal"


def test_narrow_alternate_cluster_keeps_the_prior_sd_and_still_extrapolates():
    # Matthew Stafford: eight books quote 263.5..271.5, all within 8 yards; the slope across that span is noise.
    anchors = [(263.5, 0.56, 8), (264.5, 0.55, 8), (265.5, 0.53, 8), (267.5, 0.50, 8), (268.5, 0.49, 8), (269.5, 0.47, 8), (271.5, 0.45, 8)]
    f = fit_ladder(anchors, "pass_yds", "nfl")
    assert f is not None and f.fam == "normal"
    assert f.scale == pytest.approx(0.22 * 267.0, rel=0.05)                       # the prior CV, not an ill-conditioned slope
    assert f.speaks(199.5) and 0.80 < f.p_over(199.5) < 0.93                      # 200+ passing yards: ~1.2 prior sds below the line


def test_flat_ladder_without_a_prior_is_still_refused_but_with_one_it_falls_back():
    assert fit_ladder([(49.5, 0.5, 4), (59.5, 0.5, 4)], "rec_yds", "nfl") is not None       # prior sd fills in; mean from the anchors
