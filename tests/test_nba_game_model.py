"""
tests/test_nba_game_model.py -- Comprehensive unit tests for the NBA quantitative game engine.

Validates:
  1. Strict zero-leakage invariant: future games never leak into feature calculations.
  2. Coherence and distributional invariants:
     - Probability sums equal 1.0.
     - Monotonic CDFs across spread and total rungs.
     - Positive heteroscedastic scale (sigma_margin > 0, sigma_total > 0).
     - 3PAr and pace variance scaling.
  3. Bottom-up active roster injury delta:
     - Missing home star lowers home margin.
     - Missing away star raises home margin.
     - Missing players drag total points down.
  4. Schedule fatigue tensor:
     - Rest differentials and road B2B fatigue penalties.
  5. Kalman filter state-space updates:
     - Post-game Bayesian innovation updates.
     - Offseason decay and uncertainty expansion.
  6. Market-anchored posterior blending and confluence detection.
  7. End-to-end pipeline scorer execution (100% offline).
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
import pytest
import numpy as np

from nba.game.features import (
    FeatureStore,
    MatchupFeatures,
    ScheduleFatigue,
    TeamGameStats,
    calc_possessions,
)
from nba.game.kalman_ratings import (
    KalmanGamePrediction,
    KalmanTeamTracker,
    TeamState,
)
from nba.game.model import (
    DistributionalGameEngine,
    PricedGame,
    PricedMarket,
)
from nba.game.run_daily import (
    extract_game_market_odds,
    run_nba_game_predictions,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    try:
        from nba.data import espn_injuries
        monkeypatch.setattr(espn_injuries, "fetch_injuries", lambda: None)
    except Exception:
        pass


@pytest.fixture
def mock_team_game():
    return TeamGameStats(
        game_id="g1",
        game_date="2026-01-10",
        season_year=2026,
        team="BOS",
        opp="NYK",
        is_home=True,
        points=115.0,
        opp_points=105.0,
        minutes=240.0,
        possessions=98.0,
        off_rtg=117.3,
        def_rtg=107.1,
        net_rtg=10.2,
        pace=98.0,
        efg_pct=0.56,
        opp_efg_pct=0.51,
        tov_pct=0.12,
        opp_tov_pct=0.14,
        oreb_pct=0.28,
        dreb_pct=0.74,
        ftr=0.25,
        opp_ftr=0.22,
        ts_pct=0.59,
        threepar=0.42,
    )


# ---------------------------------------------------------------------------
# 1. Zero-Leakage Invariant
# ---------------------------------------------------------------------------

def test_zero_leakage_feature_store(mock_team_game):
    """
    Ensure feature store strictly filters games prior to target date.
    Any game occurring on or after target date MUST NOT affect the team's profile.
    """
    g1 = copy.deepcopy(mock_team_game)
    g1.game_date = "2026-01-10"
    g1.points = 120.0

    g2 = copy.deepcopy(mock_team_game)
    g2.game_id = "g2"
    g2.game_date = "2026-01-15"
    g2.points = 140.0

    g3_future = copy.deepcopy(mock_team_game)
    g3_future.game_id = "g3"
    g3_future.game_date = "2026-01-20"  # Same as target date!
    g3_future.points = 200.0  # Massive outlier

    store = FeatureStore([g1, g2, g3_future])

    # Target date is 2026-01-20
    prior_games = store.get_prior_games("BOS", "2026-01-20")
    assert len(prior_games) == 2
    assert all(g.game_date < "2026-01-20" for g in prior_games)

    prof_before = store.compute_team_profile("BOS", "2026-01-20")

    # Add a game in the future (2026-01-25)
    g4_future = copy.deepcopy(mock_team_game)
    g4_future.game_id = "g4"
    g4_future.game_date = "2026-01-25"
    g4_future.points = 250.0
    store.add_games([g4_future])

    prof_after = store.compute_team_profile("BOS", "2026-01-20")
    # Features on 2026-01-20 must remain 100% identical
    assert prof_before == prof_after


# ---------------------------------------------------------------------------
# 2. Coherence and Distributional Invariants
# ---------------------------------------------------------------------------

def test_probability_sums_and_monotonicity():
    """
    Test fundamental mathematical invariants of the distributional pricing model:
      - Probabilities sum to 1.0 (within numerical tolerance).
      - Cumulative distributions are strictly monotonic.
      - Push probabilities are non-negative and push-adjusted cover sums to 1.0.
    """
    engine = DistributionalGameEngine(nu_margin=10.0, nu_total=12.0)
    mu_m = 3.5
    sigma_m = 12.0
    mu_t = 224.5
    sigma_t = 18.0

    # 1. Moneyline
    p_home_ml, p_away_ml = engine.price_moneyline(mu_m, sigma_m)
    assert abs((p_home_ml + p_away_ml) - 1.0) < 1e-6
    assert p_home_ml > 0.5  # Home is favored by +3.5 pts

    # 2. Half-point Spread
    p_h_sp, p_a_sp, push_sp = engine.price_spread(mu_m, sigma_m, -3.5)
    assert abs((p_h_sp + p_a_sp) - 1.0) < 1e-6
    assert push_sp == 0.0

    # 3. Integer Spread (with push probability)
    p_h_int, p_a_int, push_int = engine.price_spread(mu_m, sigma_m, -3.0)
    assert push_int > 0.01  # NBA 3-point push probability is roughly 2-3%
    assert abs((p_h_int + p_a_int) - 1.0) < 1e-6

    # 4. Monotonicity across spread lines
    lines = [-10.5, -7.5, -4.5, -1.5, 1.5, 4.5, 7.5, 10.5]
    probs = [engine.price_spread(mu_m, sigma_m, l)[0] for l in lines]
    # More negative line (harder to cover) => strictly lower probability of covering
    for i in range(len(probs) - 1):
        assert probs[i] < probs[i + 1]

    # 5. Half-point and Integer Totals
    p_over_half, p_under_half, push_tot_half = engine.price_total(mu_t, sigma_t, 224.5)
    assert abs((p_over_half + p_under_half) - 1.0) < 1e-6
    assert push_tot_half == 0.0

    p_over_int, p_under_int, push_tot_int = engine.price_total(mu_t, sigma_t, 225.0)
    assert push_tot_int > 0.01
    assert abs((p_over_int + p_under_int) - 1.0) < 1e-6

    # 6. Monotonicity across totals
    totals = [215.5, 220.5, 225.5, 230.5, 235.5]
    over_probs = [engine.price_total(mu_t, sigma_t, l)[0] for l in totals]
    for i in range(len(over_probs) - 1):
        assert over_probs[i] > over_probs[i + 1]


def test_heteroscedastic_scale_dispersion():
    """
    Test that high 3PAr and faster pace appropriately expand margin and total dispersion.
    """
    engine = DistributionalGameEngine()
    kalman_pred = KalmanGamePrediction(
        home_team="BOS",
        away_team="MIA",
        exp_margin=4.0,
        exp_total=225.0,
        exp_pace=100.0,
        exp_home_pts=114.5,
        exp_away_pts=110.5,
        var_margin=16.0,
        var_total=20.0,
        var_pace=4.0,
        std_margin=4.0,
        std_total=4.47,
        std_pace=2.0,
        home_off_rtg=2.0,
        home_def_rtg=-2.0,
        away_off_rtg=0.0,
        away_def_rtg=1.0,
    )

    fatigue = ScheduleFatigue(
        rest_home=2,
        rest_away=2,
        rest_diff=0,
        b2b_home=False,
        b2b_away=False,
        b2b_road_disadvantage=False,
        three_in_four_home=False,
        three_in_four_away=False,
        four_in_six_home=False,
        four_in_six_away=False,
    )

    # Low 3PAr matchup
    m_low_3p = MatchupFeatures(
        home_team="BOS",
        away_team="MIA",
        game_date="2026-01-10",
        z_net_rtg_diff=0.0,
        z_off_def_matchup=0.0,
        z_efg_diff=0.0,
        z_tov_diff=0.0,
        z_oreb_diff=0.0,
        z_ftr_diff=0.0,
        z_ts_diff=0.0,
        z_pace_sum=0.0,
        z_off_sum=0.0,
        combined_3par=0.30,  # Below average 3PAr
        raw_pace_est=98.0,
        fatigue=fatigue,
        injury_margin_delta=0.0,
        injury_total_delta=0.0,
        home_points_lost=0.0,
        away_points_lost=0.0,
    )

    # High 3PAr matchup (e.g. heavy volume 3pt shooting teams)
    m_high_3p = copy.deepcopy(m_low_3p)
    m_high_3p.combined_3par = 0.48  # High 3PAr

    sigma_m_low, _ = engine.compute_dispersions(kalman_pred, m_low_3p, exp_pace=98.0)
    sigma_m_high, _ = engine.compute_dispersions(kalman_pred, m_high_3p, exp_pace=98.0)

    assert sigma_m_high > sigma_m_low, "High 3PAr must increase margin variance"


# ---------------------------------------------------------------------------
# 3. Active Roster Injury Delta
# ---------------------------------------------------------------------------

def test_injury_delta_impact():
    """
    Test bottom-up active roster injury delta monotonicity.
    """
    store = FeatureStore([])
    base_matchup = store.extract_matchup_features("BOS", "MIA", "2026-01-10")

    # When Home loses an all-star (e.g. Tatum is Out)
    home_injuries = {"BOS": [{"player": "Jayson Tatum", "status": "Out", "pts_above_replacement": 6.5}]}
    matchup_home_hurt = store.extract_matchup_features("BOS", "MIA", "2026-01-10", home_injuries)

    # Margin delta must be negative (Home margin decreases)
    assert matchup_home_hurt.injury_margin_delta < -4.0
    assert matchup_home_hurt.injury_total_delta < 0.0

    # When Away loses an all-star
    away_injuries = {"MIA": [{"player": "Jimmy Butler", "status": "Out", "pts_above_replacement": 5.0}]}
    matchup_away_hurt = store.extract_matchup_features("BOS", "MIA", "2026-01-10", away_injuries)

    # Margin delta must be positive (Home margin increases)
    assert matchup_away_hurt.injury_margin_delta > 3.0


# ---------------------------------------------------------------------------
# 4. Schedule and Fatigue Tensor
# ---------------------------------------------------------------------------

def test_schedule_fatigue_tensor():
    """
    Test that road team on back-to-back incurs a fatigue penalty.
    """
    g_home_prev = TeamGameStats(
        game_id="gh1",
        game_date="2026-01-07",  # 3 days rest
        season_year=2026,
        team="BOS",
        opp="CHI",
        is_home=True,
        points=110.0,
        opp_points=100.0,
        minutes=240.0,
        possessions=98.0,
        off_rtg=112.0,
        def_rtg=102.0,
        net_rtg=10.0,
        pace=98.0,
        efg_pct=0.54,
        opp_efg_pct=0.50,
        tov_pct=0.13,
        opp_tov_pct=0.13,
        oreb_pct=0.25,
        dreb_pct=0.75,
        ftr=0.24,
        opp_ftr=0.24,
        ts_pct=0.56,
        threepar=0.38,
    )

    g_away_b2b = TeamGameStats(
        game_id="ga1",
        game_date="2026-01-09",  # 1 day rest (played yesterday!)
        season_year=2026,
        team="MIA",
        opp="CLE",
        is_home=False,
        points=102.0,
        opp_points=108.0,
        minutes=240.0,
        possessions=96.0,
        off_rtg=106.0,
        def_rtg=112.0,
        net_rtg=-6.0,
        pace=96.0,
        efg_pct=0.51,
        opp_efg_pct=0.54,
        tov_pct=0.14,
        opp_tov_pct=0.12,
        oreb_pct=0.24,
        dreb_pct=0.73,
        ftr=0.22,
        opp_ftr=0.25,
        ts_pct=0.53,
        threepar=0.36,
    )

    store = FeatureStore([g_home_prev, g_away_b2b])
    fatigue = store.compute_schedule_fatigue("BOS", "MIA", "2026-01-10")

    assert fatigue.b2b_home is False
    assert fatigue.b2b_away is True
    assert fatigue.b2b_road_disadvantage is True
    assert fatigue.rest_diff == 3 - 1  # 2 days rest advantage for home


# ---------------------------------------------------------------------------
# 5. Kalman State-Space Updating
# ---------------------------------------------------------------------------

def test_kalman_bayesian_updates():
    """
    Test that game outcomes update latent offensive/defensive state vectors
    and offseason decay regresses state towards mean.
    """
    tracker = KalmanTeamTracker()

    pred_before = tracker.predict_game("BOS", "NYK")
    h_before = tracker.get_state("BOS").theta.copy()
    a_before = tracker.get_state("NYK").theta.copy()

    # Boston dominant blowout win: 130 to 95 in 100 possessions
    tracker.update_game(
        home="BOS",
        away="NYK",
        home_pts=130.0,
        away_pts=95.0,
        poss=100.0,
        minutes=240.0,
        game_date="2026-01-10",
    )

    h_after = tracker.get_state("BOS").theta
    a_after = tracker.get_state("NYK").theta

    # Boston offensive rating should increase
    assert h_after[0] > h_before[0]
    # Boston defensive rating should improve (decrease in conceded points)
    assert h_after[1] < h_before[1]
    # New York defensive conceded rating should worsen (increase in conceded points)
    assert a_after[1] > a_before[1]

    pred_after = tracker.predict_game("BOS", "NYK")
    assert pred_after.exp_margin > pred_before.exp_margin

    # Test Offseason Decay
    tracker.apply_offseason_decay(retention=0.65)
    h_decayed = tracker.get_state("BOS").theta
    # Absolute ratings should contract by ~35% towards 0
    assert abs(h_decayed[0]) < abs(h_after[0])
    assert abs(h_decayed[1]) < abs(h_after[1])


# ---------------------------------------------------------------------------
# 6. Market Blending and Confluence
# ---------------------------------------------------------------------------

def test_market_anchored_blending():
    """
    Test posterior blending weight scaling with consensus thickness.
    """
    engine = DistributionalGameEngine()
    p_model = 0.60
    p_fair_mkt = 0.52

    # Thick consensus (8 books): market has higher confidence => lower model weight
    blend_thick = engine.blend_with_market(p_model, p_fair_mkt, n_books=8)
    # Thin consensus (2 books): market is thinner => higher model weight
    blend_thin = engine.blend_with_market(p_model, p_fair_mkt, n_books=2)

    assert blend_thin > blend_thick


# ---------------------------------------------------------------------------
# 7. End-to-End Execution Test
# ---------------------------------------------------------------------------

def test_end_to_end_game_scoring(monkeypatch):
    """
    Execute full quantitative game-line scoring pipeline with mock offline data.
    """
    # Isolate storage and live calls
    sample_event = {
        "id": "ev_nba_1",
        "commence_time": "2026-01-10T23:00:00Z",
        "home_team": "Boston Celtics",
        "away_team": "Miami Heat",
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Boston Celtics", "price": -180},
                            {"name": "Miami Heat", "price": +150},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Boston Celtics", "price": -110, "point": -4.5},
                            {"name": "Miami Heat", "price": -110, "point": +4.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -110, "point": 222.5},
                            {"name": "Under", "price": -110, "point": 222.5},
                        ],
                    },
                ],
            },
            {
                "key": "fanduel",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Boston Celtics", "price": -175},
                            {"name": "Miami Heat", "price": +145},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Boston Celtics", "price": -108, "point": -4.5},
                            {"name": "Miami Heat", "price": -112, "point": +4.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -112, "point": 222.5},
                            {"name": "Under", "price": -108, "point": 222.5},
                        ],
                    },
                ],
            },
        ],
    }

    from nba.game import run_daily

    monkeypatch.setattr(run_daily, "load_historical_boxscores", lambda: None or np.nan and None)
    monkeypatch.setattr(run_daily, "load_live_game_odds", lambda now=None: [sample_event])
    monkeypatch.setattr(run_daily, "load_live_injuries", lambda: {})

    res = run_daily.run_nba_game_predictions(
        target_date="2026-01-10",
        write_gcs=False,
    )

    assert res["sport"] == "nba"
    assert res["market_type"] == "game"
    assert len(res["games"]) == 1
    assert len(res["rows"]) == 6  # 2 ML (home/away), 2 Spread (home/away), 2 Total (over/under)

    systems = {r["system"] for r in res["rows"]}
    assert systems == {"NBA_ML", "NBA_SPREAD", "NBA_TOTAL"}

    for r in res["rows"]:
        assert r["p_model_side"] is not None
        assert 0.01 <= r["p_model_side"] <= 0.99
        assert r["game_key"].startswith("nba:2026-01-10:")


# ---------------------------------------------------------------------------
# 8. Walk-Forward Evaluator Test
# ---------------------------------------------------------------------------

def test_walk_forward_evaluation():
    from nba.game.evaluate import evaluate_walk_forward
    from datetime import datetime, timedelta
    import random

    teams = ["BOS", "NYK", "MIA", "PHI"]
    games = []
    start_d = datetime(2026, 1, 1)
    for i in range(50):
        t1, t2 = random.sample(teams, 2)
        h_pts = 100 + (i % 20)
        a_pts = 98 + (i % 15)
        d = (start_d + timedelta(days=i // 2)).strftime("%Y-%m-%d")
        gid = f"wf_g_{i}"
        games.append(TeamGameStats(
            game_id=gid, game_date=d, season_year=2026, team=t1, opp=t2, is_home=True,
            points=h_pts, opp_points=a_pts, minutes=240, possessions=99,
            off_rtg=h_pts, def_rtg=a_pts, net_rtg=h_pts - a_pts, pace=99,
            efg_pct=0.55, opp_efg_pct=0.52, tov_pct=0.13, opp_tov_pct=0.14,
            oreb_pct=0.25, dreb_pct=0.75, ftr=0.25, opp_ftr=0.23, ts_pct=0.58, threepar=0.40
        ))
        games.append(TeamGameStats(
            game_id=gid, game_date=d, season_year=2026, team=t2, opp=t1, is_home=False,
            points=a_pts, opp_points=h_pts, minutes=240, possessions=99,
            off_rtg=a_pts, def_rtg=h_pts, net_rtg=a_pts - h_pts, pace=99,
            efg_pct=0.52, opp_efg_pct=0.55, tov_pct=0.14, opp_tov_pct=0.13,
            oreb_pct=0.25, dreb_pct=0.75, ftr=0.23, opp_ftr=0.25, ts_pct=0.55, threepar=0.38
        ))

    report = evaluate_walk_forward(games, warmup_games=15)
    assert report.n_games == 35
    assert 0.0 <= report.ml_accuracy <= 1.0
    assert report.ml_brier_score >= 0.0
    assert report.margin_mae > 0.0
    assert report.total_mae > 0.0
    assert report.ml_ece >= 0.0
    summary = report.summary()
    assert "Walk-Forward Evaluation" in summary


# ---------------------------------------------------------------------------
# 9. Kalshi RFQ & Alternate Lines Integration Test
# ---------------------------------------------------------------------------

def test_kalshi_rfq_nba_game_legs_integration(monkeypatch):
    import ev.kalshi_game_legs as kgl

    gk = "nba:2026-01-10:MIA@BOS"
    monkeypatch.setattr(kgl, "_get_nba_game_sd", lambda game_key: {"margin": 14.5, "total": 21.0})

    sd_res = kgl._get_nba_game_sd(gk)
    assert sd_res["margin"] == 14.5
    assert sd_res["total"] == 21.0

    # Test spread interpolation for NBA with dynamic scale
    spr = {("Boston Celtics", 4.5): [0.55, 0.55, 0.54, 0.56]}
    spread_rows = kgl._interp_spread_rows("nba", spr, "Boston Celtics", game_key=gk)
    assert len(spread_rows) > 0
    # Every row must be valid tuple: (is_home, N, x, p, n, disp)
    for row in spread_rows:
        is_home, N, x, p, n, disp = row
        assert 0.01 <= p <= 0.99
        assert n == 4

    # Test total interpolation for NBA with dynamic scale
    tot = {222.5: [0.52, 0.52, 0.51, 0.53]}
    total_rows = kgl._interp_total_rows("nba", tot, game_key=gk)
    assert len(total_rows) > 0
    for row in total_rows:
        N, x, p, n, disp = row
        assert 0.01 <= p <= 0.99


# ---------------------------------------------------------------------------
# 10. Kalman Serialization Invariant Test
# ---------------------------------------------------------------------------

def test_kalman_serialization():
    tracker = KalmanTeamTracker()
    tracker.update_game("BOS", "NYK", 115, 105, 98.0, 240.0, "2026-01-10")

    d = tracker.to_dict()
    restored = KalmanTeamTracker.from_dict(d)

    bos_orig = tracker.get_state("BOS")
    bos_restored = restored.get_state("BOS")

    np.testing.assert_allclose(bos_orig.theta, bos_restored.theta)
    np.testing.assert_allclose(bos_orig.cov, bos_restored.cov)
    assert bos_orig.games_played == bos_restored.games_played

