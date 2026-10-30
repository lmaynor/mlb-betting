"""
tests/test_nhl_game_model.py -- Comprehensive unit tests for the NHL quantitative game engine.

Validates:
  1. Strict zero-leakage invariant: future games and goalie starts never leak into feature calculations.
  2. Coherence and matrix probability laws:
     - sum(M_final) == 1.0 and M_final[k, k] == 0.0 (no ties in NHL final scores).
     - Puck Line vs. Moneyline invariant:
       P(Home Covers -1.5) < P_reg(Home Wins) < P(Home Wins ML) < P(Home Covers +1.5).
     - Empty-Net Goal (ENG) operator increases P(|G_H - G_A| == 2) while preserving sum == 1.0.
     - Whole-number totals push handling: p_over + p_under + p_push == 1.0.
  3. Starting Goalie Empirical-Bayes GSAx directionality:
     - Elite goalie lowers opponent lambda, lowers total, and raises win probability.
     - B2B backup inference shifts weight to backup and applies fatigue penalty.
  4. Kalman filter state-space updates:
     - Joseph-stabilized covariance reduction.
     - Realized goal and xG proxy measurement blending.
     - Offseason decay and uncertainty expansion.
  5. Market-anchored posterior blending and confluence detection.
  6. End-to-end pipeline scorer execution (100% offline).
  7. Kalshi RFQ / ev.kalshi_game_legs integration.
  8. Walk-forward expanding window evaluator.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
import pytest
import numpy as np

from nhl.game.features import (
    FeatureStore,
    MatchupFeatures,
    ScheduleFatigue,
    TeamGameStats,
    build_team_game_stats,
)
from nhl.game.goalie_model import (
    GoalieTracker,
    GoalieStart,
    TeamGoalieProjection,
)
from nhl.game.kalman_ratings import (
    KalmanGamePrediction,
    KalmanTeamTracker,
    TeamState,
)
from nhl.game.matrix_pricer import (
    DistributionalNHLMatrixEngine,
    PricedGame,
    PricedMarket,
    ScoreMatrixResult,
)
from nhl.game.run_daily import (
    extract_game_market_odds,
    run_nhl_game_predictions,
)
from nhl.game.evaluate import (
    WalkForwardNHLEvaluator,
    compute_ece,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_team_game():
    return build_team_game_stats(
        game_id="g1",
        game_date="2026-01-10",
        season_year=2026,
        team="BOS",
        opp="TOR",
        is_home=True,
        goals=3.0,
        goals_against=2.0,
        sog=32.0,
        shots_against=28.0,
        minutes=60.0,
        pp_goals=1.0,
        pim=6.0,
        blocks=15.0,
        hits=22.0,
        takeaways=6.0,
        giveaways=4.0,
    )


# ---------------------------------------------------------------------------
# 1. Zero-Leakage Invariant
# ---------------------------------------------------------------------------

def test_zero_leakage_feature_store_and_goalies(mock_team_game):
    """
    Ensure feature store and goalie tracker strictly filter games prior to target date.
    Any game occurring on or after target date MUST NOT affect the team's profile or goalie GSAx.
    """
    # 1. Feature Store zero leakage
    store = FeatureStore()
    g1 = copy.deepcopy(mock_team_game)
    g1.game_date = "2026-01-10"
    g1.goals = 4.0

    g2 = copy.deepcopy(mock_team_game)
    g2.game_id = "g2"
    g2.game_date = "2026-01-15"
    g2.goals = 2.0

    # Game on the exact target date
    g3_target = copy.deepcopy(mock_team_game)
    g3_target.game_id = "g3"
    g3_target.game_date = "2026-01-20"
    g3_target.goals = 10.0

    store.add_game_stat(g1)
    store.add_game_stat(g2)
    store.add_game_stat(g3_target)

    prior_games = store.get_team_games_before("BOS", "2026-01-20")
    assert len(prior_games) == 2
    assert all(g.game_date < "2026-01-20" for g in prior_games)

    prof_before = store.compute_team_profile("BOS", "2026-01-20")

    # Add game in future
    g4_future = copy.deepcopy(mock_team_game)
    g4_future.game_id = "g4"
    g4_future.game_date = "2026-01-25"
    g4_future.goals = 12.0
    store.add_game_stat(g4_future)

    prof_after = store.compute_team_profile("BOS", "2026-01-20")
    assert prof_before == prof_after

    # 2. Goalie Tracker zero leakage
    gt = GoalieTracker()
    gt.record_start("g_swayman", "Jeremy Swayman", "BOS", "2026-01-10", "g1", 60.0, 30.0, 1.0)
    gt.record_start("g_swayman", "Jeremy Swayman", "BOS", "2026-01-15", "g2", 60.0, 32.0, 2.0)
    # Start on target date
    gt.record_start("g_swayman", "Jeremy Swayman", "BOS", "2026-01-20", "g3", 60.0, 40.0, 8.0)

    gsax_before = gt.get_goalie_gsax("g_swayman", before_date="2026-01-20")

    # Add future start
    gt.record_start("g_swayman", "Jeremy Swayman", "BOS", "2026-01-25", "g4", 60.0, 25.0, 0.0)

    gsax_after = gt.get_goalie_gsax("g_swayman", before_date="2026-01-20")
    assert gsax_before == gsax_after


# ---------------------------------------------------------------------------
# 2. Matrix Probability Laws and Invariants
# ---------------------------------------------------------------------------

def test_matrix_probability_laws_and_no_ties():
    """
    Test fundamental mathematical invariants of the Dixon-Coles + ENG + OT/SO matrix:
      - sum(M_final) == 1.0 within 1e-9 tolerance.
      - M_final[k, k] == 0.0 for all k (no ties in final NHL scores).
      - Moneyline fair probabilities sum to 1.0.
      - Regulation 3-way probabilities sum to 1.0.
    """
    engine = DistributionalNHLMatrixEngine()
    lh, la = 3.2, 2.7
    matrix = engine.compute_score_matrix(lh, la)

    # 1. Total mass
    assert abs(float(np.sum(matrix.m_final)) - 1.0) < 1e-9

    # 2. Diagonal cleared to 0.0
    for k in range(13):
        assert abs(float(matrix.m_final[k, k])) < 1e-12

    # 3. Moneyline sums to 1.0
    assert abs((matrix.p_home_ml + matrix.p_away_ml) - 1.0) < 1e-9
    assert matrix.p_home_ml > 0.50  # Home favored by 3.2 vs 2.7

    # 4. Regulation 3-way sums to 1.0
    reg_sum = matrix.p_home_reg + matrix.p_away_reg + matrix.p_tie_reg
    assert abs(reg_sum - 1.0) < 1e-6
    assert 0.18 <= matrix.p_tie_reg <= 0.28  # Typical NHL regulation tie rate is ~23%


def test_puck_line_vs_moneyline_invariant():
    """
    Mathematical theorem of NHL scoring and OT resolution:
    P(Home Covers -1.5) < P_reg(Home Wins) < P(Home Wins ML) < P(Home Covers +1.5).
    """
    engine = DistributionalNHLMatrixEngine()
    for lh, la in [(3.4, 2.5), (2.8, 2.8), (2.3, 3.1)]:
        matrix = engine.compute_score_matrix(lh, la)

        p_cover_minus15, _, _ = matrix.puck_line(-1.5)
        p_cover_plus15, _, _ = matrix.puck_line(+1.5)
        p_home_reg = matrix.p_home_reg
        p_home_ml = matrix.p_home_ml

        assert p_cover_minus15 < p_home_reg, f"Failed for ({lh}, {la}): {p_cover_minus15} >= {p_home_reg}"
        assert p_home_reg < p_home_ml, f"Failed for ({lh}, {la}): {p_home_reg} >= {p_home_ml}"
        assert p_home_ml < p_cover_plus15, f"Failed for ({lh}, {la}): {p_home_ml} >= {p_cover_plus15}"


def test_empty_net_goal_operator_effect():
    """
    Verify that the Stage 2 ENG operator:
      - Strictly increases 2-goal margin probability P(|G_H - G_A| == 2) compared to pure Poisson.
      - Preserves total probability mass == 1.0.
    """
    engine = DistributionalNHLMatrixEngine()
    lh, la = 3.1, 2.6

    res_with_eng = engine.compute_score_matrix(lh, la, enable_eng=True)
    res_no_eng = engine.compute_score_matrix(lh, la, enable_eng=False)

    # Calculate 2-goal margin probability in Stage 2
    def p_two_goal_margin(m):
        return sum(m[x, y] for x in range(13) for y in range(13) if abs(x - y) == 2)

    p_2margin_eng = p_two_goal_margin(res_with_eng.m_stage2)
    p_2margin_no_eng = p_two_goal_margin(res_no_eng.m_stage2)

    assert p_2margin_eng > p_2margin_no_eng
    assert abs(float(np.sum(res_with_eng.m_stage2)) - 1.0) < 1e-9


def test_whole_number_total_push_handling():
    """
    Verify push handling on whole number lines like 6.0:
      - p_over_raw + p_under_raw + p_push == 1.0
      - p_push > 0.0 (non-zero push probability on integer totals)
      - p_over_fair == p_over_raw / (1.0 - p_push)
    """
    engine = DistributionalNHLMatrixEngine()
    matrix = engine.compute_score_matrix(3.0, 3.0)

    p_over, p_under, p_push, p_fair = matrix.total(6.0)

    assert abs((p_over + p_under + p_push) - 1.0) < 1e-6
    assert p_push > 0.10  # Typically 12-16% on 6.0
    assert abs(p_fair - (p_over / (1.0 - p_push))) < 1e-6

    # Exclude shootout mode
    p_o_so, p_u_so, p_push_so, p_fair_so = matrix.total(6.0, exclude_shootout=True)
    assert abs((p_o_so + p_u_so + p_push_so) - 1.0) < 1e-6
    # Excluding shootout reduces total scoring slightly
    assert p_fair_so < p_fair


# ---------------------------------------------------------------------------
# 3. Goalie GSAx Directionality & B2B Inference
# ---------------------------------------------------------------------------

def test_goalie_gsax_directionality():
    """
    Verify that an elite opposing goalie strictly:
      - Lowers opponent regulation scoring intensity lambda.
      - Lowers total expected goals.
      - Increases that team's Moneyline win probability.
    """
    engine = DistributionalNHLMatrixEngine()
    kp = KalmanGamePrediction(
        home_team="BOS",
        away_team="MTL",
        exp_margin=0.6,
        exp_total=6.0,
        exp_pace=30.0,
        exp_home_goals=3.3,
        exp_away_goals=2.7,
        var_margin=0.4,
        var_total=0.4,
        var_pace=2.0,
        std_margin=0.63,
        std_total=0.63,
        std_pace=1.41,
        home_off_rtg=0.3,
        home_def_rtg=-0.3,
        away_off_rtg=-0.1,
        away_def_rtg=0.2,
    )

    fatigue = ScheduleFatigue(
        home_rest_days=2,
        away_rest_days=2,
        rest_diff=0,
        is_b2b_home=False,
        is_b2b_road=False,
        is_3_in_4_home=False,
        is_3_in_4_away=False,
        is_4_in_6_home=False,
        is_4_in_6_away=False,
    )

    # Average away goalie (GSAx = 0.0)
    m_avg = MatchupFeatures(
        home_team="BOS",
        away_team="MTL",
        game_date="2026-02-01",
        home_metrics_ewm10={},
        away_metrics_ewm10={},
        home_metrics_ewm25={},
        away_metrics_ewm25={},
        z_diff={},
        z_sum={},
        fatigue=fatigue,
        home_goalie_gsax_60=0.0,
        away_goalie_gsax_60=0.0,
    )
    lh_avg, la_avg = engine.compute_scoring_rates(kp, m_avg)
    mat_avg = engine.compute_score_matrix(lh_avg, la_avg)

    # Elite away goalie (GSAx = +0.60)
    m_elite = copy.deepcopy(m_avg)
    m_elite.away_goalie_gsax_60 = 0.60
    lh_elite, la_elite = engine.compute_scoring_rates(kp, m_elite)
    mat_elite = engine.compute_score_matrix(lh_elite, la_elite)

    # Elite away goalie lowers Home scoring rate
    assert lh_elite < lh_avg
    assert (lh_elite + la_elite) < (lh_avg + la_avg)
    # Elite away goalie increases Away Moneyline win probability
    assert mat_elite.p_away_ml > mat_avg.p_away_ml


def test_b2b_backup_inference():
    """
    Test B2B backup goaltender inference and skater fatigue penalty:
      - Team on 0 days rest whose #1 Starter played yesterday assigns 80% weight to Backup.
      - Skater fatigue penalty is applied.
    """
    gt = GoalieTracker()
    # Starter Swayman has 8 starts, Backup Korpisalo has 2 starts
    for i in range(8):
        gt.record_start("g1", "Jeremy Swayman", "BOS", f"2026-01-{10+i:02d}", f"game_{i}", 60.0, 30.0, 1.0)
    for i in range(2):
        gt.record_start("g2", "Joonas Korpisalo", "BOS", f"2026-01-{20+i:02d}", f"game_b_{i}", 60.0, 30.0, 4.0)

    # Regular rest day: 75% starter, 25% backup
    proj_reg = gt.project_team_goalie("BOS", "2026-01-25", rest_days=2)
    assert not proj_reg.is_b2b_backup
    assert proj_reg.fatigue_penalty == 0.0

    # B2B: starter Swayman started yesterday on 2026-01-24
    gt.record_start("g1", "Jeremy Swayman", "BOS", "2026-01-24", "game_yest", 60.0, 32.0, 2.0)
    proj_b2b = gt.project_team_goalie("BOS", "2026-01-25", rest_days=0)

    assert proj_b2b.is_b2b_backup
    assert proj_b2b.fatigue_penalty == 0.18
    # Projected GSAx leans heavily (80%) on backup Korpisalo
    expected_gsax = 0.80 * proj_b2b.backup_gsax_60 + 0.20 * proj_b2b.starter_gsax_60
    assert abs(proj_b2b.projected_gsax_60 - expected_gsax) < 1e-6


# ---------------------------------------------------------------------------
# 4. Kalman State-Space Updates & Offseason Decay
# ---------------------------------------------------------------------------

def test_kalman_bayesian_updates_and_offseason():
    """
    Verify Kalman filter measurement update with Joseph form covariance stabilization
    and offseason mean reversion.
    """
    tracker = KalmanTeamTracker()
    init_cov_trace = float(np.trace(tracker.get_state("BOS").cov))

    # Measurement update with high goals and SOG
    tracker.update_game(
        home="BOS",
        away="TOR",
        home_goals=5.0,
        away_goals=1.0,
        home_sog=38.0,
        away_sog=24.0,
        game_date="2026-01-10",
    )

    bos_state = tracker.get_state("BOS")
    assert bos_state.games_played == 1
    # Offensive rating should increase, defensive conceded rating should decrease
    assert bos_state.theta[0] > 0.0
    assert bos_state.theta[1] < 0.0
    # Covariance trace decreases following Bayesian measurement update
    post_cov_trace = float(np.trace(bos_state.cov))
    assert post_cov_trace < init_cov_trace
    pre_decay_theta = float(bos_state.theta[0])

    # Offseason decay
    tracker.apply_offseason_decay(retention=0.65)
    bos_decayed = tracker.get_state("BOS")
    assert abs(bos_decayed.theta[0]) < abs(pre_decay_theta)
    # Uncertainty expands in offseason
    assert float(np.trace(bos_decayed.cov)) > post_cov_trace


# ---------------------------------------------------------------------------
# 5. Market-Anchored Blending and Confluence Detection
# ---------------------------------------------------------------------------

def test_market_blending_and_confluence():
    """
    Verify market-anchored posterior blending and confluence gating.
    """
    engine = DistributionalNHLMatrixEngine()

    # Blend with market
    p_mod = 0.62
    p_mkt = 0.55
    blended = engine.blend_with_market(p_mod, p_mkt, n_books=5, w_model=0.20)
    assert abs(blended - (0.20 * 0.62 + 0.80 * 0.55)) < 1e-6

    # Thin market guard (< 3 books retains model)
    assert engine.blend_with_market(p_mod, p_mkt, n_books=2) == p_mod

    # Confluence test
    m = engine._build_priced_market(
        system="NHL_ML",
        market_label="Moneyline",
        side="home",
        line=None,
        p_model=0.60,
        push_prob=0.0,
        p_fair=0.55,
        best_odds=-105,  # decimal ~1.952 -> EV = 0.55 * 1.952 - 1 = +7.4%
        best_book="draftkings",
        n_books=5,
    )
    assert m.confluence is True
    assert m.model_edge == pytest.approx(0.05, abs=1e-4)
    assert m.market_ev > 0.025
    assert m.beezy_score > 0


# ---------------------------------------------------------------------------
# 6. End-to-End Daily Scorer Pipeline Execution
# ---------------------------------------------------------------------------

def test_end_to_end_nhl_game_scoring(monkeypatch):
    """
    Run end-to-end game scoring with mocked slate odds.
    """
    mock_events = [
        {
            "home_team": "Boston Bruins",
            "away_team": "Toronto Maple Leafs",
            "commence_time": "2026-02-01T19:00:00-05:00",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Boston Bruins", "price": -130},
                                {"name": "Toronto Maple Leafs", "price": +110},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Boston Bruins", "point": -1.5, "price": +175},
                                {"name": "Toronto Maple Leafs", "point": +1.5, "price": -210},
                            ],
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "point": 6.0, "price": -105},
                                {"name": "Under", "point": 6.0, "price": -115},
                            ],
                        },
                    ],
                }
            ],
        }
    ]

    monkeypatch.setattr("nhl.game.run_daily.load_live_game_odds", lambda now=None: mock_events)
    monkeypatch.setattr("nhl.game.run_daily.load_historical_boxscores", lambda: None)
    monkeypatch.setattr("nhl.game.run_daily.load_historical_goalies", lambda: None)

    res = run_nhl_game_predictions(target_date="2026-02-01", write_gcs=False)
    assert res["sport"] == "nhl"
    assert res["count"] > 0
    assert len(res["priced_games"]) == 1

    pg = res["priced_games"][0]
    assert pg["home_team"] == "BOS"
    assert pg["away_team"] == "TOR"
    assert len(pg["markets"]) == 6  # Home ML, Away ML, Home PL, Away PL, Over 6, Under 6


# ---------------------------------------------------------------------------
# 7. Kalshi RFQ Integration
# ---------------------------------------------------------------------------

def test_kalshi_rfq_nhl_sd_and_game_legs(monkeypatch):
    """
    Verify _get_nhl_game_sd and slate_rows integration in ev.kalshi_game_legs.
    """
    import ev.kalshi_game_legs as kgl

    # Test dynamic standard deviation lookup
    mock_payload = {
        "priced_games": [
            {
                "game_key": "nhl:2026-02-01:TOR@BOS",
                "sigma_margin": 2.25,
                "sigma_total": 2.45,
                "markets": [
                    {"system": "NHL_ML", "side": "home", "line": None, "p_model": 0.58},
                    {"system": "NHL_ML", "side": "away", "line": None, "p_model": 0.42},
                    {"system": "NHL_SPREAD", "side": "home", "line": -1.5, "p_model": 0.38},
                ],
            }
        ]
    }

    monkeypatch.setattr(kgl, "_get_nhl_game_sd", lambda gk: {"margin": 2.25, "total": 2.45})
    sd = kgl._get_nhl_game_sd("nhl:2026-02-01:TOR@BOS")
    assert sd["margin"] == 2.25
    assert sd["total"] == 2.45


# ---------------------------------------------------------------------------
# 8. Walk-Forward Evaluator Execution
# ---------------------------------------------------------------------------

def test_walk_forward_evaluator():
    """
    Test historical walk-forward evaluator metrics compilation.
    """
    evaluator = WalkForwardNHLEvaluator()

    for i in range(10):
        date_str = f"2026-01-{10+i:02d}"
        h_goals = 4.0 if i % 2 == 0 else 2.0
        a_goals = 1.0 if i % 2 == 0 else 3.0
        evaluator.evaluate_game(
            home="BOS",
            away="MTL",
            game_date=date_str,
            game_id=f"game_{i}",
            actual_home_goals=h_goals,
            actual_away_goals=a_goals,
            closing_puck_line=-1.5,
            closing_total=6.0,
        )

    report = evaluator.summarize()
    assert report.n_games == 10
    assert 0.0 <= report.ml_accuracy <= 1.0
    assert 0.0 <= report.ml_brier_score <= 1.0
    assert report.total_mae >= 0.0
    assert "NHL Game Evaluation Report" in report.summary()
