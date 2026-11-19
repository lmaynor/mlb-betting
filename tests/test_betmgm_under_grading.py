"""Tests for ev.betmgm_under_grading (RES-17 / RUL-20261119)."""

import math
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sp_stats

from ev import betmgm_under_grading as GR


def test_clustered_t_known_example():
    """Verify clustered_t formula matches hand computation on a known two-day dataset (§4, §5)."""
    # Two days with 2 bets each
    data = {
        "day1": [1.0, 2.0],
        "day2": [3.0, 4.0],
    }
    # Hand calculation:
    # N = 4, G = 2, df = 1
    # Day 1: S_1 = 3.0, n_1 = 2
    # Day 2: S_2 = 7.0, n_2 = 2
    # mu = (3.0 + 7.0) / 4 = 2.5
    # S_1 - n_1 * mu = 3.0 - 5.0 = -2.0 -> squared = 4.0
    # S_2 - n_2 * mu = 7.0 - 5.0 = 2.0 -> squared = 4.0
    # Var = (2 / 1) * (4.0 + 4.0) / (4^2) = 2 * 8.0 / 16.0 = 1.0
    # se = sqrt(1.0) = 1.0
    # t = 2.5 / 1.0 = 2.5
    res = GR.clustered_t(data)

    assert res["n"] == 4
    assert res["G"] == 2
    assert res["mean"] == pytest.approx(2.5)
    assert res["se"] == pytest.approx(1.0)
    assert res["t"] == pytest.approx(2.5)

    df = 1
    t_crit = float(sp_stats.t.ppf(0.975, df=df))
    expected_lo = 2.5 - t_crit * 1.0
    expected_hi = 2.5 + t_crit * 1.0
    expected_p = float(2.0 * sp_stats.t.sf(2.5, df=df))

    assert res["lo"] == pytest.approx(expected_lo)
    assert res["hi"] == pytest.approx(expected_hi)
    assert res["p"] == pytest.approx(expected_p)


def test_ece_house():
    """Verify ece() matches exact hand computation on a small vector (§4, §5)."""
    # 4 predictions with 2 bins:
    p = np.array([0.1, 0.2, 0.8, 0.9])
    y = np.array([0.0, 0.0, 1.0, 1.0])
    # For bins=2:
    # quantiles: [0.1, 0.5, 0.9] -> bin0: [0.1, 0.2], bin1: [0.8, 0.9]
    # Bin 0: mean(p) = 0.15, mean(y) = 0.0, diff = 0.15, weight = 0.5 -> 0.075
    # Bin 1: mean(p) = 0.85, mean(y) = 1.0, diff = 0.15, weight = 0.5 -> 0.075
    # Total ECE = 0.15
    score = GR.ece(p, y, bins=2)
    assert score == pytest.approx(0.15)


def test_family_assignment_nhl_never_pooled():
    """NHL rows must never enter pooled_nba, assists, or trio frames (§4, Ruling Q5)."""
    rows = []
    # 20 NBA assists
    for i in range(20):
        rows.append({"sport": "nba", "stat": "assists", "player_name": f"NBA Ast {i}"})
    # 30 NBA points (trio)
    for i in range(30):
        rows.append({"sport": "nba", "stat": "points", "player_name": f"NBA Pts {i}"})
    # 40 NHL rows (20 assists, 20 points) that would pass
    for i in range(20):
        rows.append({"sport": "nhl", "stat": "assists", "player_name": f"NHL Ast {i}"})
    for i in range(20):
        rows.append({"sport": "nhl", "stat": "points", "player_name": f"NHL Pts {i}"})

    df = pd.DataFrame(rows)
    f_frames = GR.family_frames(df)

    assert len(f_frames["pooled_nba"]) == 50
    assert (f_frames["pooled_nba"]["sport"] == "nba").all()

    assert len(f_frames["assists"]) == 20
    assert (f_frames["assists"]["sport"] == "nba").all()

    assert len(f_frames["trio"]) == 30
    assert (f_frames["trio"]["sport"] == "nba").all()

    assert len(f_frames["nhl"]) == 40
    assert (f_frames["nhl"]["sport"] == "nhl").all()
    assert len(f_frames["nhl_assists"]) == 20
    assert len(f_frames["nhl_points"]) == 20


def test_premise_kill():
    """Verify premise kill threshold: N >= 400, >= 4 consecutive present days, eq_rate >= 0.75 (§4, §5)."""
    # Case 1: Triggered (N=400, 4 consecutive days, eq_rate = 0.80)
    telem_trig = {
        f"2026-11-{24+i}": {"nba": {"n_pairs": 100, "n_line_eq": 80}}
        for i in range(4)
    }
    res1 = GR.premise_kill(telem_trig, "2026-11-27")
    assert res1["triggered"] is True
    assert res1["N"] == 400
    assert res1["eq_rate"] == 0.80
    assert res1["longest_run"] == 4

    # Case 2: Not triggered due to eq_rate < 0.75 (0.74)
    telem_rate_fail = {
        f"2026-11-{24+i}": {"nba": {"n_pairs": 100, "n_line_eq": 74}}
        for i in range(4)
    }
    res2 = GR.premise_kill(telem_rate_fail, "2026-11-27")
    assert res2["triggered"] is False
    assert res2["eq_rate"] == 0.74

    # Case 3: Not triggered due to N < 400 (N=399 at 0.80)
    telem_n_fail = {
        "2026-11-24": {"nba": {"n_pairs": 99, "n_line_eq": 79}},
        "2026-11-25": {"nba": {"n_pairs": 100, "n_line_eq": 80}},
        "2026-11-26": {"nba": {"n_pairs": 100, "n_line_eq": 80}},
        "2026-11-27": {"nba": {"n_pairs": 100, "n_line_eq": 80}},
    }
    res3 = GR.premise_kill(telem_n_fail, "2026-11-27")
    assert res3["triggered"] is False
    assert res3["N"] == 399


def test_backstop_disposition():
    """Verify backstop and cadence circuit breaker dispositions (§4, §5)."""
    stats = {
        "n": 50,
        "G": 6,
        "roi": {"mean": 0.05, "lo": -0.01, "hi": 0.11, "p": 0.10},
        "hit_bias": {"mean": 0.0, "hi": 0.02},
    }

    # 1. At backstop (2026-12-29), n=50, G=6 (below assists floor 60, 10 and n < 100) -> INSUFFICIENT_N_SHELVED
    v_backstop = GR.verdict_for_family(
        family="assists",
        stats=stats,
        ece_val=0.010,
        clv_same_book={"mean": 0.01},
        clv_consensus={"mean": 0.01},
        as_of="2026-12-29",
    )
    assert v_backstop["verdict"] == "INSUFFICIENT_N_SHELVED"

    # 2. Before backstop (2026-12-20), same numbers -> ACCRUING
    v_accruing = GR.verdict_for_family(
        family="assists",
        stats=stats,
        ece_val=0.010,
        clv_same_book={"mean": 0.01},
        clv_consensus={"mean": 0.01},
        as_of="2026-12-20",
    )
    assert v_accruing["verdict"] == "ACCRUING"

    # 3. At Day 15 (2026-12-09) with present_days = 3 (< 4) -> PAUSED_INSUFFICIENT_COVERAGE
    v_cadence = GR.verdict_for_family(
        family="assists",
        stats=stats,
        ece_val=0.010,
        clv_same_book={"mean": 0.01},
        clv_consensus={"mean": 0.01},
        as_of="2026-12-09",
        cadence_state={"present_days": 3},
    )
    assert v_cadence["verdict"] == "PAUSED_INSUFFICIENT_COVERAGE"


def test_diagnostics_non_governing():
    """Diagnostic 5pp slice passing does not grant PASS when governing slice fails (§4, §5)."""
    # Governing 3pp slice fails ROI (mean = -0.01 < 0.01, hi = 0.02 < 0.04)
    gov_stats = {
        "n": 70,  # Floor for assists is (60, 10)
        "G": 12,
        "roi": {"mean": -0.01, "lo": -0.04, "hi": 0.02, "p": 0.60},
        "hit_bias": {"mean": -0.01, "hi": 0.01},
    }
    # Diagnostic 5pp slice that would have passed ROI
    diag_5pp = {
        "n": 45,
        "G": 10,
        "roi": {"mean": 0.07, "lo": 0.02, "hi": 0.12, "p": 0.01},
        "hit_bias": {"mean": 0.01, "hi": 0.03},
    }
    diagnostics = {"diag_5pp": diag_5pp, "diag_8pp": {}}

    v = GR.verdict_for_family(
        family="assists",
        stats=gov_stats,
        ece_val=0.010,
        clv_same_book={"mean": 0.005},
        clv_consensus={"mean": 0.010},
        as_of="2026-12-20",
        diagnostics=diagnostics,
    )

    # Must NOT be PASS
    assert v["verdict"] != "PASS"
    assert v["verdict"] == "KILLED_ROI"

    # Markdown rendering explicitly marks diagnostics as non-governing
    report = {
        "meta": {"ruling": GR.RULING, "as_of": "2026-12-20", "start_date": GR.START_DATE, "backstop": GR.BACKSTOP},
        "cadence": {"present_days": 12, "longest_consecutive_run": 5, "nba_pairs_total": 500, "consensus_convergence_rate": 0.40},
        "premise_kill": {"triggered": False, "N": 500, "eq_rate": 0.40, "present_days": 12, "longest_run": 5},
        "hold_drift": {"nba_median": 1.071, "nhl_median": None, "regime_shift": False, "tightened": False, "comparable": True},
        "holm_step_down": {},
        "verdicts": {"assists": v},
        "artifact_cuts": {},
    }
    md = GR.render_markdown(report)
    assert "Diagnostic Slices (Non-Governing)" in md


def test_grey_zone_extension():
    """Floors met, ROI mean 0.02 in [0.01, 0.04) extends until Jan 12, then kills (§4, §5)."""
    stats = {
        "n": 70,  # Floor met (60, 10)
        "G": 12,
        "roi": {"mean": 0.02, "lo": -0.01, "hi": 0.05, "p": 0.20},
        "hit_bias": {"mean": -0.01, "hi": 0.01},
    }

    # Before GREY_END (2026-12-30 < 2027-01-12) -> EXTENDED_GREY
    v_extend = GR.verdict_for_family(
        family="assists",
        stats=stats,
        ece_val=0.010,
        clv_same_book={"mean": 0.00},
        clv_consensus={"mean": 0.01},
        as_of="2026-12-30",
    )
    assert v_extend["verdict"] == "EXTENDED_GREY"

    # After GREY_END (2027-01-13 > 2027-01-12) -> KILLED_ROI
    v_kill = GR.verdict_for_family(
        family="assists",
        stats=stats,
        ece_val=0.010,
        clv_same_book={"mean": 0.00},
        clv_consensus={"mean": 0.01},
        as_of="2027-01-13",
    )
    assert v_kill["verdict"] == "KILLED_ROI"


def test_backstop_shelves_subfloor_family_without_kill_evaluation():
    """Ruling Q3 verbatim: a family still short of its n/G floors on Dec 29 gets an
    automatic INSUFFICIENT_N / SHELVED_FOR_COVERAGE verdict. Kill bars are NOT
    evaluated on sub-floor samples at the backstop (early kills, which have their
    own n/G triggers, are the only sub-floor kill path)."""
    stats = {
        "n": 150,  # below pooled floor 200
        "G": 9,    # below pooled floor 10
        # Terrible ROI mean, but upper CI >= 0.08 blocks Early Kill 1,
        # benign bias blocks Early Kill 2, positive CLV blocks Early Kill 4.
        "roi": {"mean": -0.05, "lo": -0.12, "hi": 0.10, "p": 0.40},
        "hit_bias": {"mean": 0.0, "hi": 0.02},
    }
    v = GR.verdict_for_family(
        family="pooled_nba",
        stats=stats,
        ece_val=0.010,
        clv_same_book={"mean": 0.005},
        clv_consensus={"mean": 0.010},
        as_of="2026-12-30",
        cadence_state={"present_days": 9},
    )
    assert v["verdict"] == "INSUFFICIENT_N_SHELVED"


def _ledger_row(game_date, outcome="win", pnl=0.85):
    return {
        "game_date": game_date, "sport": "nba", "stat": "points",
        "gate_bucket": "3pp", "outcome_under": outcome, "flat_pnl_1u": pnl,
        "p_dist_under": 0.60, "line_mgm": 20.5, "line_mgm_close": 20.5,
        "dec_under_mgm": 1.85, "odds_under_mgm_close": -115,
        "p_imp_under_mgm_close": 0.5349, "p_imp_under_mgm": 0.52,
        "p_cons_under_close": None, "line_cons_close": 20.5, "line_cons": 20.5,
        "two_sided_sum_mgm": 1.071, "mgm_present_at_close": True,
    }


def test_cohort_window_excludes_prewindow_rows():
    """build_report counts only rows with game_date >= START_DATE (2026-11-24):
    a pre-window backfill row must never enter the verdict cohort."""
    import pandas as pd
    ledger = pd.DataFrame([
        _ledger_row("2026-11-15"),   # pre-window smoke/backfill row
        _ledger_row("2026-11-25"),   # in-window row
    ])
    report = GR.build_report(ledger, telemetry={}, as_of="2026-12-01")
    assert report["verdicts"]["pooled_nba"]["n"] == 1
    assert report["verdicts"]["trio"]["n"] == 1
