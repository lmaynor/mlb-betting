"""
nfl.game.model -- NFL distributional game-line pricing engine.

Synthesizes expected margin and total from:
  1. Kalman Filter state-space ratings (PassOff, RushOff, PassDef, RushDef, Pace)
  2. PBP non-garbage-time EPA Z-score differentials
  3. QB availability and OL injury cluster adjustment
  4. Stadium weather and rest/schedule adjustments

Prices NFL_ML, NFL_SPREAD (main + key-number alternates), and NFL_TOTAL
using the key-number-aware score matrix (nfl.game.score_matrix).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from mlb_core.alerts.beezy_score import beezy_score as calc_beezy_score
from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob, devig_two_way
from nfl.game.kalman_ratings import KalmanGamePrediction
from nfl.game.pbp_features import MatchupPBPFeatures
from nfl.game.score_matrix import NFLScoreMatrix, build_nfl_score_matrix

log = logging.getLogger("nfl.game.model")

# -- Model blending weights -----------------------------------------------------
W_KALMAN: float = 0.60     # Kalman filter contribution to expected margin
W_ZFEAT: float = 0.40      # PBP Z-score feature contribution

# -- Z-score feature coefficients for margin (home - away perspective) ----------
# Signs: positive Z means home team is better on that dimension
COEF_ED_PASS: float = 3.20   # early-down pass EPA differential -> margin pts
COEF_ED_RUSH: float = 1.40
COEF_RZ_TD: float = 2.50
COEF_LUCK: float = -2.00    # negative: luck regresses -> down-weight lucky teams
COEF_PASS_SUC: float = 1.60
COEF_PRESSURE: float = 1.80  # home pressure advantage (z_pressure_diff > 0 = home pressures more)

# -- Schedule adjustments -------------------------------------------------------
TNF_TOTAL_PENALTY: float = -1.4      # short week hurts total
BYE_MARGIN_BONUS: float = 1.1        # bye week rest advantage on spread
DIVISIONAL_TOTAL_PENALTY: float = -0.8  # tighter divisional games

# -- Weather adjustments (outdoor) -----------------------------------------------
WIND_THRESHOLD_MPH: float = 12.0
WIND_TOTAL_PENALTY_PER_MPH: float = -0.35
PRECIP_TOTAL_PENALTY: float = -1.5

# -- QB delta clamps (in points on margin and total) ----------------------------
QB_MARGIN_MIN: float = -6.5
QB_MARGIN_MAX: float = 1.5
QB_TOTAL_MIN: float = -3.0
QB_TOTAL_MAX: float = 0.5

# -- Sigma parameters (outcome dispersion of the score matrix) ------------------
BASE_SIGMA_MARGIN: float = 13.5
BASE_SIGMA_TOTAL: float = 12.0


@dataclass
class PricedMarket:
    system: str
    market_label: str
    side: str
    line: Optional[float]
    p_model: float
    push_prob: float = 0.0
    p_fair: Optional[float] = None
    p_blended: Optional[float] = None
    best_odds: Optional[int] = None
    best_book: Optional[str] = None
    n_books: Optional[int] = None
    model_edge: Optional[float] = None
    market_ev: Optional[float] = None
    confluence: bool = False
    beezy_score: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "system": self.system,
            "market_label": self.market_label,
            "side": self.side,
            "line": self.line,
            "p_model": round(self.p_model, 4),
            "push_prob": round(self.push_prob, 4),
            "p_fair": round(self.p_fair, 4) if self.p_fair is not None else None,
            "p_blended": round(self.p_blended, 4) if self.p_blended is not None else None,
            "best_odds": self.best_odds,
            "best_book": self.best_book,
            "n_books": self.n_books,
            "model_edge": round(self.model_edge, 4) if self.model_edge is not None else None,
            "market_ev": round(self.market_ev, 4) if self.market_ev is not None else None,
            "confluence": self.confluence,
            "beezy_score": self.beezy_score,
        }


@dataclass
class PricedGame:
    game_key: str
    home_team: str
    away_team: str
    game_date: str
    mu_margin: float
    mu_total: float
    sigma_margin: float
    sigma_total: float
    exp_home_pts: float
    exp_away_pts: float
    markets: List[PricedMarket] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_key": self.game_key,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "game_date": self.game_date,
            "mu_margin": round(self.mu_margin, 2),
            "mu_total": round(self.mu_total, 2),
            "sigma_margin": round(self.sigma_margin, 2),
            "sigma_total": round(self.sigma_total, 2),
            "exp_home_pts": round(self.exp_home_pts, 2),
            "exp_away_pts": round(self.exp_away_pts, 2),
            "markets": [m.to_dict() for m in self.markets],
        }


class NFLGameEngine:
    """Quantitative NFL game-line engine combining Kalman ratings, PBP EPA features,
    schedule/weather adjustments, and key-number-aware score matrix pricing."""

    def __init__(self) -> None:
        pass

    def compute_means(
        self,
        kalman: KalmanGamePrediction,
        pbp: MatchupPBPFeatures,
    ) -> Tuple[float, float]:
        """
        Synthesize expected margin and total:
          * 60% Kalman-derived margin + 40% PBP Z-score margin
          * Kalman-derived total + weather/schedule adjustments
        """
        # Z-score feature-based margin (home perspective)
        mu_m_z = (
            kalman.hca_pts
            + COEF_ED_PASS * pbp.z_ed_pass_diff
            + COEF_ED_RUSH * pbp.z_ed_rush_diff
            + COEF_RZ_TD * pbp.z_rz_td_diff
            + COEF_LUCK * pbp.z_luck_diff
            + COEF_PASS_SUC * pbp.z_pass_suc_diff
            + COEF_PRESSURE * pbp.z_pressure_diff
        )

        # Schedule adjustments on margin
        bye_adj = 0.0
        if pbp.coming_off_bye_home:
            bye_adj += BYE_MARGIN_BONUS
        if pbp.coming_off_bye_away:
            bye_adj -= BYE_MARGIN_BONUS

        mu_margin = float(
            W_KALMAN * kalman.exp_margin
            + W_ZFEAT * mu_m_z
            + bye_adj
            + pbp.qb_delta_pts_home
            - pbp.qb_delta_pts_away
        )

        # Total: Kalman base + schedule + weather
        mu_total = float(kalman.exp_total) + pbp.total_injury_drag

        if pbp.is_short_week_tnf:
            mu_total += TNF_TOTAL_PENALTY

        if pbp.is_divisional:
            mu_total += DIVISIONAL_TOTAL_PENALTY

        if not pbp.is_dome and pbp.wind_mph > WIND_THRESHOLD_MPH:
            mu_total += WIND_TOTAL_PENALTY_PER_MPH * (pbp.wind_mph - WIND_THRESHOLD_MPH)

        mu_margin = float(np.clip(mu_margin, -28.0, 28.0))
        mu_total = float(np.clip(mu_total, 20.0, 80.0))

        return mu_margin, mu_total

    def blend_with_market(
        self,
        p_model: float,
        p_fair: Optional[float],
        n_books: Optional[int],
    ) -> float:
        if p_fair is None or p_fair <= 0.0 or p_fair >= 1.0:
            return float(p_model)
        nb = max(1, int(n_books or 4))
        w = float(np.clip(0.30 * (4.0 / max(4.0, float(nb))), 0.10, 0.40))
        return float(np.clip(w * p_model + (1.0 - w) * p_fair, 0.01, 0.99))

    def price_game(
        self,
        game_key: str,
        kalman: KalmanGamePrediction,
        pbp: MatchupPBPFeatures,
        market_odds: Optional[Dict[str, Any]] = None,
    ) -> PricedGame:
        """Full NFL game prediction and market pricing."""
        mu_m, mu_t = self.compute_means(kalman, pbp)
        exp_home_pts = (mu_t + mu_m) / 2.0
        exp_away_pts = (mu_t - mu_m) / 2.0

        matrix = build_nfl_score_matrix(mu_m, mu_t, BASE_SIGMA_MARGIN, BASE_SIGMA_TOTAL)

        priced = PricedGame(
            game_key=game_key,
            home_team=kalman.home_team,
            away_team=kalman.away_team,
            game_date=kalman.game_date,
            mu_margin=mu_m,
            mu_total=mu_t,
            sigma_margin=BASE_SIGMA_MARGIN,
            sigma_total=BASE_SIGMA_TOTAL,
            exp_home_pts=exp_home_pts,
            exp_away_pts=exp_away_pts,
            markets=[],
        )

        mkt = market_odds or {}

        # 1. Moneyline (NFL_ML)
        h2h_info = mkt.get("h2h", {})
        p_home_ml = matrix.p_home_ml
        p_away_ml = 1.0 - p_home_ml

        priced.markets.append(self._build_market(
            system="NFL_ML", market_label="Moneyline", side="home", line=None,
            p_model=p_home_ml, push_prob=matrix.p_tie,
            p_fair=h2h_info.get("p_home_fair"),
            best_odds=h2h_info.get("best_home_odds"), best_book=h2h_info.get("best_home_book"),
            n_books=h2h_info.get("n_books"),
        ))
        priced.markets.append(self._build_market(
            system="NFL_ML", market_label="Moneyline", side="away", line=None,
            p_model=p_away_ml, push_prob=matrix.p_tie,
            p_fair=h2h_info.get("p_away_fair"),
            best_odds=h2h_info.get("best_away_odds"), best_book=h2h_info.get("best_away_book"),
            n_books=h2h_info.get("n_books"),
        ))

        # 2. Main Spread (NFL_SPREAD)
        sp_info = mkt.get("spreads", {})
        spread_line = sp_info.get("line")
        if spread_line is None:
            spread_line = round(-mu_m * 2.0) / 2.0   # nearest half-point to fair spread

        p_h_sp, p_a_sp, push_sp = matrix.spread(float(spread_line))

        priced.markets.append(self._build_market(
            system="NFL_SPREAD", market_label=f"Spread {spread_line:+g}", side="home",
            line=float(spread_line), p_model=p_h_sp, push_prob=push_sp,
            p_fair=sp_info.get("p_home_fair"),
            best_odds=sp_info.get("best_home_odds"), best_book=sp_info.get("best_home_book"),
            n_books=sp_info.get("n_books"),
        ))
        priced.markets.append(self._build_market(
            system="NFL_SPREAD", market_label=f"Spread {-spread_line:+g}", side="away",
            line=-float(spread_line), p_model=p_a_sp, push_prob=push_sp,
            p_fair=sp_info.get("p_away_fair"),
            best_odds=sp_info.get("best_away_odds"), best_book=sp_info.get("best_away_book"),
            n_books=sp_info.get("n_books"),
        ))

        # 3. Game Total (NFL_TOTAL)
        tot_info = mkt.get("totals", {})
        total_line = tot_info.get("line")
        if total_line is None:
            total_line = round(mu_t * 2.0) / 2.0

        p_over, p_under, push_tot = matrix.total(float(total_line))

        priced.markets.append(self._build_market(
            system="NFL_TOTAL", market_label=f"Over {total_line:g}", side="over",
            line=float(total_line), p_model=p_over, push_prob=push_tot,
            p_fair=tot_info.get("p_over_fair"),
            best_odds=tot_info.get("best_over_odds"), best_book=tot_info.get("best_over_book"),
            n_books=tot_info.get("n_books"),
        ))
        priced.markets.append(self._build_market(
            system="NFL_TOTAL", market_label=f"Under {total_line:g}", side="under",
            line=float(total_line), p_model=p_under, push_prob=push_tot,
            p_fair=tot_info.get("p_under_fair"),
            best_odds=tot_info.get("best_under_odds"), best_book=tot_info.get("best_under_book"),
            n_books=tot_info.get("n_books"),
        ))

        return priced

    def _build_market(
        self,
        system: str,
        market_label: str,
        side: str,
        line: Optional[float],
        p_model: float,
        push_prob: float,
        p_fair: Optional[float],
        best_odds: Optional[int],
        best_book: Optional[str],
        n_books: Optional[int],
    ) -> PricedMarket:
        p_model = float(np.clip(p_model, 0.01, 0.99))
        p_blended = self.blend_with_market(p_model, p_fair, n_books)

        model_edge = None
        market_ev = None
        confluence = False
        score = 0

        if p_fair is not None:
            model_edge = float(p_model - p_fair)

        if best_odds is not None:
            dec = american_to_decimal(int(best_odds))
            if p_fair is not None:
                market_ev = float((p_fair * dec) - 1.0)
            else:
                market_ev = float((p_model * dec) - 1.0)

            if model_edge is not None and market_ev is not None:
                confluence = bool(
                    model_edge >= 0.015 and market_ev >= 0.025 and p_model > (p_fair or 0.5)
                )

            ev_for_score = market_ev if market_ev is not None else 0.0
            nb_for_score = n_books if n_books is not None else 3
            try:
                score = calc_beezy_score(ev_for_score, nb_for_score, p_fair or p_model)
            except Exception:
                score = 0

        return PricedMarket(
            system=system,
            market_label=market_label,
            side=side,
            line=line,
            p_model=p_model,
            push_prob=push_prob,
            p_fair=p_fair,
            p_blended=p_blended,
            best_odds=best_odds,
            best_book=best_book,
            n_books=n_books,
            model_edge=model_edge,
            market_ev=market_ev,
            confluence=confluence,
            beezy_score=score,
        )
