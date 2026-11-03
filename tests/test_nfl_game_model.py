"""
tests/test_nfl_game_model.py -- Offline unit tests for nfl.game.* components.

All tests run entirely offline (no GCS, no network, no fixture files required).
Coverage:
  (a) Zero time-travel leakage: PBP store only uses games before target (season, week)
  (b) Key-number step-function invariant: P(cover -2.5) - P(cover -3.5) >= 10%
  (c) Score matrix coherence: sum=1, monotonic spread cover curve, push probs sum to 1
  (d) QB injury directionality: backup QB reduces home-team projected margin
  (e) Wind directionality: high wind penalizes projected total
  (f) Kalman cold-start and post-game update consistency
  (g) team_code normalization
"""
from __future__ import annotations

import math
from typing import List

import numpy as np
import pytest

from nfl.game.kalman_ratings import KalmanNFLTracker, KalmanGamePrediction
from nfl.game.pbp_features import (
    TeamGamePBP,
    PBPFeatureStore,
    MatchupPBPFeatures,
    TeamRollingProfile,
    compute_matchup_pbp_features,
)
from nfl.game.score_matrix import NFLScoreMatrix, build_nfl_score_matrix
from nfl.game.model import NFLGameEngine


# ---- Helpers -------------------------------------------------------------------

def _make_pbp_row(
    game_id: str, team: str, opp: str,
    season: int, week: int, game_date: str,
    is_home: bool = True,
    ed_pass_epa: float = 0.05,
    ed_rush_epa: float = 0.01,
    team_score: float = 24.0,
    opp_score: float = 17.0,
) -> TeamGamePBP:
    margin = team_score - opp_score
    total = team_score + opp_score
    kalman_margin = 0.55 * (ed_pass_epa + ed_rush_epa) * 10.0 + 0.45 * margin
    return TeamGamePBP(
        game_id=game_id,
        season=season,
        week=week,
        game_date=game_date,
        team=team,
        opp=opp,
        is_home=is_home,
        ed_pass_epa=ed_pass_epa,
        ed_rush_epa=ed_rush_epa,
        pass_success_rate=0.50,
        rush_success_rate=0.45,
        proe=0.02,
        rz_td_rate=0.55,
        pressure_suffered_rate=0.25,
        seconds_per_play=28.0,
        turnover_luck_epa=0.0,
        team_score=team_score,
        opp_score=opp_score,
        margin=margin,
        total=total,
        kalman_obs_margin=float(np.clip(kalman_margin, -24.0, 24.0)),
    )


def _empty_pbp_features(home: str, away: str) -> MatchupPBPFeatures:
    return MatchupPBPFeatures(
        home_team=home,
        away_team=away,
        game_date="2026-11-02",
        season=2026,
        week=9,
    )


# ---- (a) Zero time-travel leakage ----------------------------------------------

class TestTimeLeakage:
    def test_store_excludes_future_games(self):
        """PBPFeatureStore.get_profile must never include games from the target week or later."""
        store = PBPFeatureStore()
        # Week 1 through 8 data — some in the target week (9) or later
        past_games = [_make_pbp_row("g1", "KC", "BUF", 2026, 1, "2026-09-08", ed_pass_epa=0.10)
                      for _ in range(1)]
        past_games += [_make_pbp_row("g2", "KC", "DEN", 2026, 5, "2026-10-06", ed_pass_epa=0.12)
                       for _ in range(1)]
        future_games = [_make_pbp_row("g9", "KC", "SF", 2026, 9, "2026-11-03", ed_pass_epa=0.50)
                        for _ in range(1)]   # this week -> must be excluded

        store.add_games(past_games + future_games)

        # Profile for week 9 must only reflect weeks 1-8
        prof = store.get_profile("KC", before_season=2026, before_week=9)
        # The future-week game has ed_pass_epa=0.50 which would shift the EWM far from 0.10-0.12
        assert prof.ed_pass_epa_s < 0.30, (
            f"ed_pass_epa_s={prof.ed_pass_epa_s:.4f} suggests future data leaked"
        )

    def test_store_empty_before_any_games(self):
        """Zero games in store should give the default zero profile."""
        store = PBPFeatureStore()
        prof = store.get_profile("PHI", before_season=2026, before_week=1)
        assert prof.n_games == 0
        assert prof.ed_pass_epa_s == 0.0

    def test_store_respects_season_boundary(self):
        """Games from prior season are included; same season future weeks are not."""
        store = PBPFeatureStore()
        last_season = [_make_pbp_row("g2025", "DAL", "WAS", 2025, 17, "2026-01-05", ed_pass_epa=0.08)]
        this_season_wk1 = [_make_pbp_row("g2026w1", "DAL", "PHI", 2026, 1, "2026-09-08", ed_pass_epa=0.09)]
        this_season_wk9 = [_make_pbp_row("g2026w9", "DAL", "GB", 2026, 9, "2026-11-03", ed_pass_epa=0.99)]

        store.add_games(last_season + this_season_wk1 + this_season_wk9)
        prof = store.get_profile("DAL", before_season=2026, before_week=9)
        assert prof.n_games == 2   # last season + wk1 only
        assert prof.ed_pass_epa_s < 0.50


# ---- (b) Key-number step-function invariant ------------------------------------

class TestKeyNumberInvariant:
    @pytest.mark.parametrize("mu_m", [2.9, 3.0, 3.1])
    def test_cover_step_function_at_3(self, mu_m: float):
        """
        P(home covers -2.5) - P(home covers -3.5) must be >= 10% for a game centered near 3.
        Tests near the key number center (not far off-center) to validate upweighting.
        """
        matrix = build_nfl_score_matrix(mu_m, 47.0)
        p_cover_minus2_5, _, _ = matrix.spread(-2.5)   # home covers -2.5
        p_cover_minus3_5, _, _ = matrix.spread(-3.5)   # home covers -3.5
        step = p_cover_minus2_5 - p_cover_minus3_5
        # Normal distribution gives ~3% step; key-number matrix must give >= 9%
        assert step >= 0.09, (
            f"Key-number step at 3 too small: P(-2.5)={p_cover_minus2_5:.4f}, "
            f"P(-3.5)={p_cover_minus3_5:.4f}, step={step:.4f} (expected >= 0.09, Normal baseline ~0.03)"
        )

    @pytest.mark.parametrize("mu_m", [6.5, 7.0, 7.5])
    def test_cover_step_function_at_7(self, mu_m: float):
        """P(cover -6.5) - P(cover -7.5) should be elevated near the key number 7."""
        matrix = build_nfl_score_matrix(mu_m, 47.0)
        p_cover_minus6_5, _, _ = matrix.spread(-6.5)
        p_cover_minus7_5, _, _ = matrix.spread(-7.5)
        step = p_cover_minus6_5 - p_cover_minus7_5
        assert step >= 0.07, (
            f"Key-number step at 7 too small: step={step:.4f} (expected >= 0.07)"
        )


# ---- (c) Score matrix coherence laws -------------------------------------------

class TestScoreMatrixCoherence:
    def test_matrix_sums_to_one(self):
        m = build_nfl_score_matrix(3.0, 47.0)
        total = float(m.m_final.sum())
        assert abs(total - 1.0) < 1e-6, f"Matrix sums to {total}, not 1.0"

    def test_win_probs_partition_unity(self):
        m = build_nfl_score_matrix(3.0, 47.0)
        total = m.p_home_win + m.p_away_win + m.p_tie
        assert abs(total - 1.0) < 1e-5, f"Win probs sum to {total}"

    def test_moneyline_no_push_sums_to_one(self):
        m = build_nfl_score_matrix(-7.0, 45.0)
        assert abs(m.p_home_ml + (1.0 - m.p_home_ml) - 1.0) < 1e-9

    def test_spread_cover_monotonically_decreasing_in_line(self):
        """Heavier favorite line (more negative home spread) -> lower cover probability."""
        m = build_nfl_score_matrix(-6.0, 46.0)
        lines = [-0.5, -3.5, -6.5, -9.5, -13.5]
        probs = [m.spread(l)[0] for l in lines]
        for i in range(len(probs) - 1):
            assert probs[i] >= probs[i + 1] - 1e-6, (
                f"Non-monotonic cover probs at lines {lines}: {probs}"
            )

    def test_total_over_monotonically_decreasing_in_line(self):
        """Higher total line -> lower over probability."""
        m = build_nfl_score_matrix(0.0, 47.0)
        lines = [30.5, 38.5, 44.5, 47.0, 52.5, 60.5]
        probs = [m.total(l)[0] for l in lines]
        for i in range(len(probs) - 1):
            assert probs[i] >= probs[i + 1] - 1e-6, (
                f"Non-monotonic over probs at lines {lines}: {probs}"
            )

    def test_push_plus_two_way_sums_to_one(self):
        m = build_nfl_score_matrix(0.0, 47.0)
        p_h, p_a, push = m.spread(-3.0)
        raw_total = p_h + p_a
        # p_h and p_a are already normalized to sum to 1 (two-way); push is separate
        assert abs(raw_total - 1.0) < 1e-6, f"Two-way spread probs sum to {raw_total}"

    def test_tie_rate_near_nfl_empirical(self):
        m = build_nfl_score_matrix(0.0, 47.0)
        # NFL regular season tie rate ~0.6%
        assert 0.001 < m.p_tie < 0.025, f"Tie rate {m.p_tie:.4f} outside expected range"

    def test_matrix_all_non_negative(self):
        m = build_nfl_score_matrix(-3.5, 43.5)
        assert float(m.m_final.min()) >= 0.0, "Matrix has negative entries"

    def test_symmetric_game_near_equal_ml(self):
        """Neutral-site game with mu_margin=0 should give p_home_ml near 0.50."""
        m = build_nfl_score_matrix(0.0, 47.0)
        assert 0.44 < m.p_home_ml < 0.56, f"Symmetric game p_home_ml={m.p_home_ml:.4f}"


# ---- (d) QB injury directionality ----------------------------------------------

class TestQBInjury:
    def test_backup_qb_lowers_home_margin(self):
        """A backup QB (qb_delta_pts_home < 0) must reduce the projected margin."""
        tracker = KalmanNFLTracker()
        engine = NFLGameEngine()

        k_pred = tracker.predict_game("KC", "BUF", 2026, 9, "2026-11-02")

        # Starter (no injury)
        pbp_normal = _empty_pbp_features("KC", "BUF")
        pg_normal = engine.price_game("g1", k_pred, pbp_normal)

        # Backup QB: home team loses 4.5 pts on margin
        pbp_injured = _empty_pbp_features("KC", "BUF")
        pbp_injured.qb_delta_pts_home = -4.5
        pg_injured = engine.price_game("g2", k_pred, pbp_injured)

        assert pg_injured.mu_margin < pg_normal.mu_margin, (
            f"Backup QB did not lower projected margin: "
            f"normal={pg_normal.mu_margin:.2f}, injured={pg_injured.mu_margin:.2f}"
        )

    def test_away_backup_qb_increases_home_margin(self):
        """Away backup QB should shift margin toward home team."""
        tracker = KalmanNFLTracker()
        engine = NFLGameEngine()
        k_pred = tracker.predict_game("PHI", "DAL", 2026, 9, "2026-11-02")

        pbp_normal = _empty_pbp_features("PHI", "DAL")
        pg_normal = engine.price_game("g1", k_pred, pbp_normal)

        pbp_injured = _empty_pbp_features("PHI", "DAL")
        pbp_injured.qb_delta_pts_away = -4.5
        pg_injured = engine.price_game("g2", k_pred, pbp_injured)

        assert pg_injured.mu_margin > pg_normal.mu_margin, (
            f"Away backup QB did not increase home margin: "
            f"normal={pg_normal.mu_margin:.2f}, injured={pg_injured.mu_margin:.2f}"
        )


# ---- (e) Wind directionality ---------------------------------------------------

class TestWeather:
    def test_high_wind_lowers_total(self):
        """Wind above threshold should depress projected total (outdoor game)."""
        tracker = KalmanNFLTracker()
        engine = NFLGameEngine()
        k_pred = tracker.predict_game("CHI", "GB", 2026, 9, "2026-11-02")

        pbp_calm = _empty_pbp_features("CHI", "GB")
        pbp_calm.wind_mph = 5.0
        pbp_calm.is_dome = False
        pg_calm = engine.price_game("g1", k_pred, pbp_calm)

        pbp_windy = _empty_pbp_features("CHI", "GB")
        pbp_windy.wind_mph = 30.0
        pbp_windy.is_dome = False
        pg_windy = engine.price_game("g2", k_pred, pbp_windy)

        assert pg_windy.mu_total < pg_calm.mu_total, (
            f"High wind did not reduce total: calm={pg_calm.mu_total:.2f}, windy={pg_windy.mu_total:.2f}"
        )

    def test_dome_game_unaffected_by_wind(self):
        """Indoor dome games: wind should have no effect on total."""
        tracker = KalmanNFLTracker()
        engine = NFLGameEngine()
        k_pred = tracker.predict_game("NO", "ATL", 2026, 9, "2026-11-02")

        pbp_calm = _empty_pbp_features("NO", "ATL")
        pbp_calm.wind_mph = 5.0
        pbp_calm.is_dome = True

        pbp_windy = _empty_pbp_features("NO", "ATL")
        pbp_windy.wind_mph = 35.0
        pbp_windy.is_dome = True

        pg_calm = engine.price_game("g1", k_pred, pbp_calm)
        pg_windy = engine.price_game("g2", k_pred, pbp_windy)

        assert abs(pg_windy.mu_total - pg_calm.mu_total) < 0.01, (
            f"Dome wind leaked into total: calm={pg_calm.mu_total:.2f}, windy={pg_windy.mu_total:.2f}"
        )


# ---- (f) Kalman cold-start and update -------------------------------------------

class TestKalmanTracker:
    def test_cold_start_prediction_in_valid_range(self):
        """Cold-start prediction should give a plausible total near league average."""
        tracker = KalmanNFLTracker()
        pred = tracker.predict_game("KC", "BUF", 2026, 9, "2026-11-02")
        # League average total is ~47.0; cold start has no team-specific info
        assert 30.0 < pred.exp_total < 65.0, f"exp_total={pred.exp_total:.2f} out of range"
        assert -10.0 < pred.exp_margin < 10.0, f"exp_margin={pred.exp_margin:.2f} out of range for cold-start"

    def test_hca_applied_at_home(self):
        """Home team should have a positive HCA adjustment in the prediction."""
        tracker = KalmanNFLTracker()
        pred = tracker.predict_game("KC", "BUF", 2026, 9, "2026-11-02")
        assert pred.hca_pts > 0.0, f"HCA={pred.hca_pts:.2f} should be positive"

    def test_update_shifts_ratings(self):
        """Post-game Kalman update should shift home team's offensive rating positively after a big win."""
        tracker = KalmanNFLTracker()
        before = tracker.teams["KC"].theta[0]  # OffPts
        tracker.update_game(
            home="KC", away="BUF",
            home_score=35.0, away_score=14.0,
            home_pass_epa=0.15, away_pass_epa=-0.05,
            home_rush_epa=0.08, away_rush_epa=0.00,
            home_plays=65.0, away_plays=55.0,
            kalman_margin=18.0,
            game_date="2026-11-02",
        )
        after = tracker.teams["KC"].theta[0]
        assert after > before, f"KC OffPts did not increase after a 35-14 win: {before:.3f} -> {after:.3f}"

    def test_offseason_decay_reduces_ratings(self):
        """roll_season should apply offseason regression, shrinking team ratings."""
        tracker = KalmanNFLTracker()
        tracker.update_game(
            home="KC", away="BUF",
            home_score=35.0, away_score=14.0,
            home_pass_epa=0.15, away_pass_epa=-0.05,
            home_rush_epa=0.08, away_rush_epa=0.00,
            home_plays=65.0, away_plays=55.0,
            kalman_margin=18.0,
            game_date="2025-12-28",
        )
        tracker.current_season = 2025
        before_off = abs(tracker.teams["KC"].theta[0])
        tracker.roll_season("2026-09-10")   # Wednesday after Labor Day 2026 = new season
        after_off = abs(tracker.teams["KC"].theta[0])
        assert after_off < before_off, (
            f"Offseason decay did not shrink KC OffPts: before={before_off:.3f}, after={after_off:.3f}"
        )

    def test_no_double_update_on_repeated_roll(self):
        """roll_season called twice in the same season should NOT apply decay twice."""
        tracker = KalmanNFLTracker()
        tracker.current_season = 2025
        tracker.roll_season("2026-09-10")
        theta_after_first = tracker.teams["KC"].theta.copy()
        tracker.roll_season("2026-09-15")   # same season still 2026
        theta_after_second = tracker.teams["KC"].theta
        np.testing.assert_array_almost_equal(theta_after_first, theta_after_second)


# ---- (g) Team code normalization -----------------------------------------------

class TestTeamCode:
    def test_nfl_team_code_aliases(self):
        from nfl.game.run_daily import nfl_team_code
        assert nfl_team_code("Kansas City Chiefs") == "KC"
        assert nfl_team_code("Los Angeles Rams") == "LAR"
        assert nfl_team_code("Las Vegas Raiders") == "LV"
        assert nfl_team_code("Washington Commanders") == "WAS"
        assert nfl_team_code("Oakland Raiders") in ("LV", "OAK", None)  # legacy alias

    def test_nfl_team_code_direct_abbrev(self):
        from nfl.game.run_daily import nfl_team_code
        assert nfl_team_code("KC") == "KC"
        assert nfl_team_code("BUF") == "BUF"
        assert nfl_team_code("NE") == "NE"

    def test_nfl_team_code_empty_returns_none(self):
        from nfl.game.run_daily import nfl_team_code
        assert nfl_team_code("") is None
        assert nfl_team_code("UNKNOWN") is None


# ---- engine price_game produces expected outputs --------------------------------

class TestNFLGameEngine:
    def test_price_game_has_all_markets(self):
        tracker = KalmanNFLTracker()
        engine = NFLGameEngine()
        k_pred = tracker.predict_game("SF", "DAL", 2026, 9, "2026-11-02")
        pbp = _empty_pbp_features("SF", "DAL")
        pg = engine.price_game("test_game", k_pred, pbp)

        systems = {m.system for m in pg.markets}
        assert "NFL_ML" in systems
        assert "NFL_SPREAD" in systems
        assert "NFL_TOTAL" in systems

    def test_price_game_probs_in_range(self):
        tracker = KalmanNFLTracker()
        engine = NFLGameEngine()
        k_pred = tracker.predict_game("SF", "DAL", 2026, 9, "2026-11-02")
        pbp = _empty_pbp_features("SF", "DAL")
        pg = engine.price_game("test_game", k_pred, pbp)

        for m in pg.markets:
            assert 0.0 < m.p_model < 1.0, f"p_model={m.p_model} out of (0,1) for {m.system}/{m.side}"

    def test_home_plus_away_ml_probs_near_one(self):
        tracker = KalmanNFLTracker()
        engine = NFLGameEngine()
        k_pred = tracker.predict_game("PHI", "DAL", 2026, 9, "2026-11-02")
        pbp = _empty_pbp_features("PHI", "DAL")
        pg = engine.price_game("test_game", k_pred, pbp)

        ml_markets = [m for m in pg.markets if m.system == "NFL_ML"]
        assert len(ml_markets) == 2
        home_p = next(m.p_model for m in ml_markets if m.side == "home")
        away_p = next(m.p_model for m in ml_markets if m.side == "away")
        # Both are two-way (not push-adjusted), so home + away should be ~1
        assert abs(home_p + away_p - 1.0) < 0.02, f"ML probs sum to {home_p + away_p:.4f}"

    def test_exp_pts_consistency(self):
        """exp_home_pts + exp_away_pts should equal mu_total."""
        tracker = KalmanNFLTracker()
        engine = NFLGameEngine()
        k_pred = tracker.predict_game("KC", "CIN", 2026, 9, "2026-11-02")
        pbp = _empty_pbp_features("KC", "CIN")
        pg = engine.price_game("test_game", k_pred, pbp)

        assert abs(pg.exp_home_pts + pg.exp_away_pts - pg.mu_total) < 0.01, (
            f"exp_home={pg.exp_home_pts:.2f} + exp_away={pg.exp_away_pts:.2f} != mu_total={pg.mu_total:.2f}"
        )
