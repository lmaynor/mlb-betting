"""Unit tests for ev.consensus_fv_anchor (RUL-20261125 Half 2 / MAK-01)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ev.consensus_fv_anchor import (
    AnchorResult,
    BookLine,
    PROP_MODEL_WEIGHT_C,
    compute_anchor,
    from_ev_quotes,
)
from ev.first_scorer_grader import devig_n
from ev.kalshi_wide_quoter_rules import WideSpreadConfig
from ev.props import Quote
from mlb_core.odds.utils import american_to_implied_prob


NOW = datetime(2026, 11, 21, 18, 0, 0, tzinfo=timezone.utc)


def test_prop_model_weight_hardcoded_zero():
    assert PROP_MODEL_WEIGHT_C == 0.00


def test_from_ev_quotes_filters_stat_point_and_two_sided():
    quotes = [
        Quote("pinnacle", "points", False, "Ayo Dosunmu", "ayo dosunmu", "MIN", 14.5, -108, -108, ts="2026-11-21T17:59:00Z"),
        Quote("fanduel", "points", False, "Ayo Dosunmu", "ayo dosunmu", "MIN", 14.5, -115, None, ts="2026-11-21T17:59:00Z"),
        Quote("draftkings", "points", False, "Ayo Dosunmu", "ayo dosunmu", "MIN", 15.5, -110, -110, ts="2026-11-21T17:59:00Z"),
        Quote("betmgm", "rebounds", False, "Ayo Dosunmu", "ayo dosunmu", "MIN", 14.5, -110, -110, ts="2026-11-21T17:59:00Z"),
    ]
    lines = from_ev_quotes(quotes, stat="points", point=14.5)
    assert len(lines) == 1
    assert lines[0].book == "pinnacle"
    assert lines[0].over_american == -108
    assert lines[0].under_american == -108
    assert lines[0].ts == datetime(2026, 11, 21, 17, 59, 0, tzinfo=timezone.utc)


def test_drops_kalshi_dfs_and_high_hold_and_gap():
    cfg = WideSpreadConfig()
    fresh = NOW - timedelta(seconds=30)
    # -105/-105 -> hold ~0.024 (sharp, 2x)
    # -112/-112 -> hold ~0.0566 (retail, 1x)
    # -116/-116 -> hold ~0.074 (retail, 1x)
    # -117/-117 -> hold ~0.0784 (in 7.5%-8.0% gap -> excluded fail-closed)
    # -120/-120 -> hold ~0.0909 (> 8.0% -> excluded)
    lines = [
        BookLine("Kalshi", 100, -110, fresh),
        BookLine("sleeper", -112, -112, fresh),
        BookLine("underdog", -105, -105, fresh),
        BookLine("pinnacle", -105, -105, fresh),
        BookLine("draftkings", -112, -112, fresh),
        BookLine("fanduel", -116, -116, fresh),
        BookLine("gap_book", -117, -117, fresh),
        BookLine("high_vig", -120, -120, fresh),
    ]
    res = compute_anchor(lines, NOW, cfg)
    assert isinstance(res, AnchorResult)
    assert res.ok is True
    assert res.reason == "ok"
    assert res.n_fresh_books == 3
    used_books = {b for b, _, _ in res.books_used}
    assert used_books == {"pinnacle", "draftkings", "fanduel"}
    weights = {b: w for b, _, w in res.books_used}
    assert weights == {"pinnacle": 2.0, "draftkings": 1.0, "fanduel": 1.0}
    assert res.fv_yes_prob == pytest.approx(0.5, abs=1e-9)


def test_quorum_requires_3_with_sharp_and_4_without_sharp():
    cfg = WideSpreadConfig()
    fresh = NOW - timedelta(seconds=20)
    # 3 retail books only (hold ~6.5%): fails quorum because no sharp book
    retail_3 = [
        BookLine("dk", -114, -114, fresh),
        BookLine("fd", -114, -114, fresh),
        BookLine("mgm", -114, -114, fresh),
    ]
    res_fail = compute_anchor(retail_3, NOW, cfg)
    assert res_fail.ok is False
    assert res_fail.reason == "quorum"
    assert res_fail.fv_yes_prob is None
    assert res_fail.n_fresh_books == 3

    # 4 retail books passes
    retail_4 = retail_3 + [BookLine("czr", -114, -114, fresh)]
    res_pass = compute_anchor(retail_4, NOW, cfg)
    assert res_pass.ok is True
    assert res_pass.n_fresh_books == 4

    # 1 sharp + 2 retail = 3 passes
    mixed_3 = [
        BookLine("pinnacle", -105, -105, fresh),
        BookLine("dk", -114, -114, fresh),
        BookLine("fd", -114, -114, fresh),
    ]
    res_mixed = compute_anchor(mixed_3, NOW, cfg)
    assert res_mixed.ok is True
    assert res_mixed.n_fresh_books == 3


def test_staleness_limit_formula_and_missing_ts():
    fresh = NOW - timedelta(seconds=179)
    stale_181 = NOW - timedelta(seconds=181)
    # feed_poll_interval_s=30 -> limit = min(300, max(180, 60)) = 180s
    cfg_30 = WideSpreadConfig(feed_poll_interval_s=30)
    lines = [
        BookLine("pinnacle", -105, -105, fresh),
        BookLine("dk", -110, -110, fresh),
        BookLine("fd", -110, -110, stale_181),
        BookLine("mgm", -110, -110, None),
        BookLine("czr", -110, -110, "not-a-timestamp"),
    ]
    res = compute_anchor(lines, NOW, cfg_30)
    assert res.ok is False
    assert res.reason == "quorum"
    assert res.n_fresh_books == 2
    assert res.n_stale_dropped == 3

    # feed_poll_interval_s=120 -> limit = 240s, so 181s is now fresh!
    cfg_120 = WideSpreadConfig(feed_poll_interval_s=120)
    res_120 = compute_anchor(lines, NOW, cfg_120)
    assert res_120.ok is True
    assert res_120.n_fresh_books == 3
    assert res_120.max_book_age_sec == pytest.approx(181.0)

    # feed_poll_interval_s=200 -> capped at 300s
    cfg_200 = WideSpreadConfig(feed_poll_interval_s=200)
    res_301 = compute_anchor(
        [
            BookLine("pinnacle", -105, -105, fresh),
            BookLine("dk", -110, -110, fresh),
            BookLine("fd", -110, -110, NOW - timedelta(seconds=301)),
        ],
        NOW,
        cfg_200,
    )
    assert res_301.ok is False
    assert res_301.n_fresh_books == 2


def test_weighted_median_and_dispersion_guard():
    cfg = WideSpreadConfig()
    fresh = NOW - timedelta(seconds=10)
    # Sharp book (hold <= 5.5%, weight 2) at ~0.534, two retail books (hold 5.6%-7.5%, weight 1 each) at ~0.517 and ~0.525
    # Sorted p_i: dk (w=1), fd (w=1), pinnacle (w=2). Total w=4, half=2.0 -> tie at ordered[1] & ordered[2] -> average!
    lines = [
        BookLine("pinnacle", -125, 105, fresh),  # hold ~4.3% -> sharp (weight 2.0)
        BookLine("dk", -122, -106, fresh),        # hold ~6.4% -> retail (weight 1.0)
        BookLine("fd", -126, -103, fresh),        # hold ~6.5% -> retail (weight 1.0)
    ]
    res = compute_anchor(lines, NOW, cfg)
    assert res.ok is True
    p_pin = devig_n([american_to_implied_prob(-125), american_to_implied_prob(105)], "power")[0]
    p_dk = devig_n([american_to_implied_prob(-122), american_to_implied_prob(-106)], "power")[0]
    p_fd = devig_n([american_to_implied_prob(-126), american_to_implied_prob(-103)], "power")[0]
    ordered = sorted([p_pin, p_dk, p_fd])
    assert res.fv_yes_prob == pytest.approx((ordered[1] + ordered[2]) / 2.0, abs=1e-9)
    assert res.dispersion_pp == pytest.approx((ordered[2] - ordered[0]) * 100.0, abs=1e-9)
    assert res.dispersion_pp <= cfg.dispersion_max_pp

    # Now push one book > 4.0pp away -> dispersion failure, fv_yes_prob=None, dispersion_pp populated
    wide_lines = [
        BookLine("pinnacle", -105, -105, fresh),   # 0.50
        BookLine("dk", -110, -110, fresh),         # 0.50
        BookLine("fd", -135, 110, fresh),          # > 0.55 -> dispersion > 5pp
    ]
    res_disp = compute_anchor(wide_lines, NOW, cfg)
    assert res_disp.ok is False
    assert res_disp.reason == "dispersion"
    assert res_disp.fv_yes_prob is None
    assert res_disp.dispersion_pp is not None and res_disp.dispersion_pp > 4.0
