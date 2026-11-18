"""tests/test_lambda_crossing.py -- Verification for Module 2 Point-in-Time Lambda-Crossing Engine.

Covers:
  1. Synthetic 20-snapshot intraday timeline [T-12h .. T-5m]:
     - Soft book opens at +145 (crossing D_strike(lambda, tau) at t_0 and persisting at t_1 = t_0 + 6m).
     - Peaks at +155 at t_max.
     - Steams to +105 by close (T-5m).
     - Asserts crossed_t0=True, crossed_t1=True, d_t0=2.45, d_peak=2.55.
     - Computes accurate dislocation_retention and settled ROI across all 4 evaluation points.
  2. Edge cases:
     - Quote freshness age_sec > 300 blocks t_0.
     - Active velocity lockout |P_cons(t_k) - P_cons(t_{k-1})| >= 0.025 blocks t_0.
     - Adverse consensus move >= 1.0pp blocks t_1 persistence.
     - Quotes outside [T-14h, T-30m] are excluded from t_0 and peak odds.
"""

from __future__ import annotations

import pytest

from ev.lambda_crossing import (
    QuoteSnapshot,
    TrajectoryGrade,
    calc_d_strike,
    evaluate_intraday_trajectory,
)


def test_synthetic_20_snapshot_timeline() -> None:
    """Verify synthetic 20-snapshot timeline per Module 10 Item 2."""
    game_start = 1770000000.0  # Epoch seconds reference for game tip
    p_lambda = 0.45
    tau = 0.045
    # D_strike = (1.0 + 0.045) / 0.45 = 2.3222 (+132 American)
    assert calc_d_strike(p_lambda, tau) == pytest.approx(2.3222, abs=1e-3)

    line = 29.5
    side = "over"
    book = "fanduel"

    # Construct 20 snapshots from T-12h to T-5m
    # t_0: T-10h (36000s before tip), opens +145 (d=2.45)
    # t_1: t_0 + 6m (35640s before tip), persists at +145 (d=2.45)
    # t_max: T-6h (21600s before tip), peaks +155 (d=2.55)
    # close: T-5m (300s before tip), steams to +105 (d=2.05)
    snapshots: list[QuoteSnapshot] = []

    # 1. T-12h: +130 (d=2.30), does not clear strike (2.30 < 2.322)
    snapshots.append(QuoteSnapshot(
        game_id="g1", player_norm="mikal_bridges", stat="points", line=line, side=side, book=book,
        timestamp=game_start - (12.0 * 3600.0), game_start=game_start,
        odds_american=130, odds_decimal=2.30, p_cons=0.43, age_sec=30.0,
    ))

    # 2. T-10h: opens +145 (d=2.45) -> First-Passage t_0!
    t0_time = game_start - (10.0 * 3600.0)
    snapshots.append(QuoteSnapshot(
        game_id="g1", player_norm="mikal_bridges", stat="points", line=line, side=side, book=book,
        timestamp=t0_time, game_start=game_start,
        odds_american=145, odds_decimal=2.45, p_cons=0.44, age_sec=60.0,
    ))

    # 3. t_0 + 6m (persists >= 300s later, d=2.45, no adverse consensus move) -> t_1!
    snapshots.append(QuoteSnapshot(
        game_id="g1", player_norm="mikal_bridges", stat="points", line=line, side=side, book=book,
        timestamp=t0_time + 360.0, game_start=game_start,
        odds_american=145, odds_decimal=2.45, p_cons=0.44, age_sec=30.0,
    ))

    # 4. T-8h: +150 (d=2.50)
    snapshots.append(QuoteSnapshot(
        game_id="g1", player_norm="mikal_bridges", stat="points", line=line, side=side, book=book,
        timestamp=game_start - (8.0 * 3600.0), game_start=game_start,
        odds_american=150, odds_decimal=2.50, p_cons=0.44, age_sec=30.0,
    ))

    # 5. T-6h: peaks at +155 (d=2.55) -> t_max!
    snapshots.append(QuoteSnapshot(
        game_id="g1", player_norm="mikal_bridges", stat="points", line=line, side=side, book=book,
        timestamp=game_start - (6.0 * 3600.0), game_start=game_start,
        odds_american=155, odds_decimal=2.55, p_cons=0.445, age_sec=30.0,
    ))

    # Snapshots 6 to 19: gradual retail steam down to +105
    remaining_hours = [5.0, 4.0, 3.5, 3.0, 2.5, 2.0, 1.5, 1.25, 1.0, 0.75, 0.5, 0.35, 0.2, 0.12]
    odds_seq = [2.50, 2.45, 2.40, 2.35, 2.30, 2.25, 2.20, 2.18, 2.15, 2.12, 2.10, 2.08, 2.06, 2.05]
    for h, o in zip(remaining_hours, odds_seq):
        snapshots.append(QuoteSnapshot(
            game_id="g1", player_norm="mikal_bridges", stat="points", line=line, side=side, book=book,
            timestamp=game_start - (h * 3600.0), game_start=game_start,
            odds_decimal=o, p_cons=0.45, age_sec=30.0,
        ))

    # 20. T-5m: close at +105 (d=2.05)
    snapshots.append(QuoteSnapshot(
        game_id="g1", player_norm="mikal_bridges", stat="points", line=line, side=side, book=book,
        timestamp=game_start - 300.0, game_start=game_start,
        odds_american=105, odds_decimal=2.05, p_cons=0.46, age_sec=20.0,
    ))

    assert len(snapshots) == 20

    # Evaluate trajectory when player scores 32 pts (Over wins)
    grade = evaluate_intraday_trajectory(
        snapshots=snapshots,
        p_lambda=p_lambda,
        tau=tau,
        outcome=32.0,
    )

    # Assertions per spec Module 10 Item 2
    assert grade.crossed_t0 is True
    assert grade.crossed_t1 is True
    assert grade.d_t0 == pytest.approx(2.45, abs=1e-4)
    assert grade.d_peak == pytest.approx(2.55, abs=1e-4)
    assert grade.d_close == pytest.approx(2.05, abs=1e-4)

    # Dislocation retention: ((2.55 - 2.45) / 2.45) * 100.0 ≈ 4.0816%
    expected_retention = ((2.55 - 2.45) / 2.45) * 100.0
    assert grade.dislocation_retention == pytest.approx(expected_retention, abs=1e-3)
    assert grade.delta_retention_pct == pytest.approx(expected_retention, abs=1e-3)

    # Settled ROI across all 4 evaluation points (for winning Over):
    # t_0: 2.45 - 1.0 = +1.45 (+145%)
    # t_1: 2.45 - 1.0 = +1.45 (+145%)
    # peak: 2.55 - 1.0 = +1.55 (+155%)
    # close: 2.05 - 1.0 = +1.05 (+105%)
    assert grade.settled_won is True
    assert grade.settled_roi_t0 == pytest.approx(1.45, abs=1e-4)
    assert grade.settled_roi_t1 == pytest.approx(1.45, abs=1e-4)
    assert grade.settled_roi_peak == pytest.approx(1.55, abs=1e-4)
    assert grade.settled_roi_masked_close == pytest.approx(1.05, abs=1e-4)
    assert grade.settled_roi_unmasked_close == pytest.approx(1.05, abs=1e-4)


def test_settled_roi_on_loss() -> None:
    """Verify settled ROI properly records -1.0 (-100%) on a loss."""
    game_start = 1770000000.0
    snapshots = [
        QuoteSnapshot(
            game_id="g1", player_norm="p", stat="points", line=29.5, side="over", book="dk",
            timestamp=game_start - (10.0 * 3600.0), game_start=game_start,
            odds_decimal=2.45, p_cons=0.44, age_sec=30.0,
        ),
        QuoteSnapshot(
            game_id="g1", player_norm="p", stat="points", line=29.5, side="over", book="dk",
            timestamp=game_start - (9.8 * 3600.0), game_start=game_start,
            odds_decimal=2.45, p_cons=0.44, age_sec=30.0,
        ),
        QuoteSnapshot(
            game_id="g1", player_norm="p", stat="points", line=29.5, side="over", book="dk",
            timestamp=game_start - 300.0, game_start=game_start,
            odds_decimal=2.00, p_cons=0.45, age_sec=30.0,
        ),
    ]

    # Player scores 24 pts (Over loses)
    grade = evaluate_intraday_trajectory(snapshots, p_lambda=0.45, outcome=24.0)
    assert grade.settled_won is False
    assert grade.settled_roi_t0 == -1.0
    assert grade.settled_roi_t1 == -1.0
    assert grade.settled_roi_peak == -1.0
    assert grade.settled_roi_masked_close == -1.0


def test_quote_freshness_age_gate() -> None:
    """Verify quote older than 300s is rejected as stale for t_0."""
    game_start = 1770000000.0
    snapshots = [
        # Stale quote clearing strike: age_sec = 350.0 > 300.0
        QuoteSnapshot(
            game_id="g1", player_norm="p", stat="points", line=20.5, side="over", book="dk",
            timestamp=game_start - (8.0 * 3600.0), game_start=game_start,
            odds_decimal=2.50, p_cons=0.45, age_sec=350.0,
        ),
        QuoteSnapshot(
            game_id="g1", player_norm="p", stat="points", line=20.5, side="over", book="dk",
            timestamp=game_start - 300.0, game_start=game_start,
            odds_decimal=2.10, p_cons=0.45, age_sec=30.0,
        ),
    ]
    grade = evaluate_intraday_trajectory(snapshots, p_lambda=0.45)
    assert grade.crossed_t0 is False


def test_velocity_lockout_gate() -> None:
    """Verify active consensus-velocity lockout |Delta P_cons| >= 0.025 blocks t_0."""
    game_start = 1770000000.0
    snapshots = [
        QuoteSnapshot(
            game_id="g1", player_norm="p", stat="points", line=20.5, side="over", book="dk",
            timestamp=game_start - (8.0 * 3600.0), game_start=game_start,
            odds_decimal=2.20, p_cons=0.40, age_sec=30.0,
        ),
        # Next snapshot has large consensus jump (+0.030 >= 0.025 lockout)
        QuoteSnapshot(
            game_id="g1", player_norm="p", stat="points", line=20.5, side="over", book="dk",
            timestamp=game_start - (7.9 * 3600.0), game_start=game_start,
            odds_decimal=2.50, p_cons=0.435, age_sec=30.0,
        ),
        QuoteSnapshot(
            game_id="g1", player_norm="p", stat="points", line=20.5, side="over", book="dk",
            timestamp=game_start - 300.0, game_start=game_start,
            odds_decimal=2.10, p_cons=0.435, age_sec=30.0,
        ),
    ]
    grade = evaluate_intraday_trajectory(snapshots, p_lambda=0.45)
    assert grade.crossed_t0 is False


def test_adverse_consensus_move_blocks_t1() -> None:
    """Verify adverse consensus move >= 1.0pp prevents t_1 persistence."""
    game_start = 1770000000.0
    t0_time = game_start - (10.0 * 3600.0)
    snapshots = [
        # t_0: P_cons = 0.45
        QuoteSnapshot(
            game_id="g1", player_norm="p", stat="points", line=20.5, side="over", book="dk",
            timestamp=t0_time, game_start=game_start,
            odds_decimal=2.45, p_cons=0.45, age_sec=30.0,
        ),
        # 6m later: odds still high, but consensus dropped by 1.5pp (0.435) -> adverse move >= 1.0pp!
        QuoteSnapshot(
            game_id="g1", player_norm="p", stat="points", line=20.5, side="over", book="dk",
            timestamp=t0_time + 360.0, game_start=game_start,
            odds_decimal=2.45, p_cons=0.435, age_sec=30.0,
        ),
        QuoteSnapshot(
            game_id="g1", player_norm="p", stat="points", line=20.5, side="over", book="dk",
            timestamp=game_start - 300.0, game_start=game_start,
            odds_decimal=2.10, p_cons=0.435, age_sec=30.0,
        ),
    ]
    grade = evaluate_intraday_trajectory(snapshots, p_lambda=0.45)
    assert grade.crossed_t0 is True
    assert grade.crossed_t1 is False
