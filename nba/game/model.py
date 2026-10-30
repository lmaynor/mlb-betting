"""
nba.game.model -- Heteroscedastic joint margin and total distributional pricing engine.

Models:
  * Margin M ~ Student-t(nu=10, loc=mu_M, scale=sigma_M)
  * Total  T ~ Student-t(nu=12, loc=mu_T, scale=sigma_T)

Features:
  1. Mean synthesis fusing Kalman filter state-space priors, pace-neutral Four Factors
     cross-sectional Z-score differentials, active roster injury impact, and schedule fatigue.
  2. Heteroscedastic scale model adjusting margin and total dispersion based on 3-point
     attempt rate (3PAr), expected pace, and Kalman filter parameter uncertainty.
  3. Analytical pricing of Moneyline (NBA_ML), Spreads (NBA_SPREAD), and Totals (NBA_TOTAL),
     including integer push probabilities and alternate rungs.
  4. Market-anchored posterior blending and confluence detection.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.stats import t as student_t

from mlb_core.alerts.beezy_score import beezy_score as calc_beezy_score
from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob, devig_two_way
from nba.game.features import MatchupFeatures
from nba.game.kalman_ratings import KalmanGamePrediction

log = logging.getLogger("nba.game.model")

NU_MARGIN: float = 10.0
NU_TOTAL: float = 12.0

BASE_SIGMA_MARGIN: float = 12.0
BASE_SIGMA_TOTAL: float = 18.0


@dataclass
class PricedMarket:
    system: str  # 'NBA_ML', 'NBA_SPREAD', 'NBA_TOTAL'
    market_label: str
    side: str  # 'home', 'away', 'over', 'under'
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
    sigma_margin: float
    mu_total: float
    sigma_total: float
    exp_pace: float
    markets: List[PricedMarket] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_key": self.game_key,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "game_date": self.game_date,
            "mu_margin": round(self.mu_margin, 2),
            "sigma_margin": round(self.sigma_margin, 2),
            "mu_total": round(self.mu_total, 2),
            "sigma_total": round(self.sigma_total, 2),
            "exp_pace": round(self.exp_pace, 2),
            "markets": [m.to_dict() for m in self.markets],
        }


class DistributionalGameEngine:
    """Institutional-grade joint margin and total distribution and pricing engine."""

    def __init__(
        self,
        nu_margin: float = NU_MARGIN,
        nu_total: float = NU_TOTAL,
        base_sigma_margin: float = BASE_SIGMA_MARGIN,
        base_sigma_total: float = BASE_SIGMA_TOTAL,
    ) -> None:
        self.nu_margin = float(nu_margin)
        self.nu_total = float(nu_total)
        self.base_sigma_margin = float(base_sigma_margin)
        self.base_sigma_total = float(base_sigma_total)

    def compute_means(
        self,
        kalman_pred: KalmanGamePrediction,
        matchup: MatchupFeatures,
    ) -> Tuple[float, float, float]:
        """
        Synthesize expected margin, total, and pace:
          * Margin combines Kalman prediction, Four Factors Z-differential,
            injury impact, rest advantage, and road B2B fatigue penalty.
          * Total combines Kalman pace & efficiency, Z-pace & offense sum,
            and scoring drag from missing players.
        Returns: (mu_margin, mu_total, exp_pace)
        """
        # Z-score margin component: home court advantage + four factors differentials
        mu_m_z = (
            2.40  # baseline HCA
            + 2.80 * matchup.z_net_rtg_diff
            + 1.20 * matchup.z_efg_diff
            + 0.80 * matchup.z_tov_diff
            + 0.50 * matchup.z_oreb_diff
            + 0.40 * matchup.z_ftr_diff
            + 0.60 * matchup.z_ts_diff
        )

        # Fatigue adjustments
        rest_adj = 0.35 * matchup.fatigue.rest_diff
        b2b_road_adj = -2.20 if matchup.fatigue.b2b_road_disadvantage else 0.0

        # Weighted blend of Kalman and Z-score (0.55 Kalman, 0.45 Z-score)
        mu_margin = (
            0.55 * kalman_pred.exp_margin
            + 0.45 * mu_m_z
            + matchup.injury_margin_delta
            + rest_adj
            + b2b_road_adj
        )

        # Z-score total component
        mu_t_z = (
            227.0
            + 3.50 * matchup.z_off_sum
            + 4.20 * matchup.z_pace_sum
        )

        pace_adj = 1.15 * (matchup.raw_pace_est - 99.0)

        mu_total = (
            0.55 * kalman_pred.exp_total
            + 0.45 * mu_t_z
            + matchup.injury_total_delta
            + pace_adj
        )

        exp_pace = float(0.55 * kalman_pred.exp_pace + 0.45 * matchup.raw_pace_est)

        return float(mu_margin), float(mu_total), float(exp_pace)

    def compute_dispersions(
        self,
        kalman_pred: KalmanGamePrediction,
        matchup: MatchupFeatures,
        exp_pace: float,
    ) -> Tuple[float, float]:
        """
        Heteroscedastic scale estimation:
          * Margin variance widens with high 3-point attempt rate (3PAr),
            faster game pace, and Kalman state parameter uncertainty.
          * Total variance widens with faster pace and state uncertainty.
        Returns: (sigma_margin, sigma_total)
        """
        pace_ratio = (exp_pace - 99.0) / 99.0
        threepar_diff = matchup.combined_3par - 0.395

        # Margin scale
        sigma_m = self.base_sigma_margin * (
            1.0
            + 0.40 * threepar_diff
            + 0.15 * pace_ratio
            + 0.035 * max(0.0, kalman_pred.std_margin - 4.5)
        )
        sigma_m = float(np.clip(sigma_m, 8.5, 17.5))

        # Total scale
        sigma_t = self.base_sigma_total * (
            1.0
            + 0.25 * pace_ratio
            + 0.035 * max(0.0, kalman_pred.std_total - 4.5)
        )
        sigma_t = float(np.clip(sigma_t, 13.0, 26.0))

        return sigma_m, sigma_t

    def price_moneyline(
        self,
        mu_margin: float,
        sigma_margin: float,
    ) -> Tuple[float, float]:
        """
        P(Home Win) = P(M > 0) = 1 - CDF_t(0, df=nu_m, loc=mu_m, scale=sigma_m)
        P(Away Win) = CDF_t(0, df=nu_m, loc=mu_m, scale=sigma_m)
        """
        dist = student_t(df=self.nu_margin, loc=mu_margin, scale=sigma_margin)
        p_away = float(dist.cdf(0.0))
        p_home = float(1.0 - p_away)
        p_home = float(np.clip(p_home, 0.01, 0.99))
        p_away = float(1.0 - p_home)
        return p_home, p_away

    def price_spread(
        self,
        mu_margin: float,
        sigma_margin: float,
        line: float,
    ) -> Tuple[float, float, float]:
        """
        Price spread line from Home team's perspective (e.g. line = -3.5 or +4.0).
        Home covers if Margin > -line (threshold k = -line).
        Returns: (p_home_cover, p_away_cover, push_prob)
        """
        k = -float(line)
        dist = student_t(df=self.nu_margin, loc=mu_margin, scale=sigma_margin)

        # Check if line is an integer (e.g. -3.0 or +4.0)
        is_integer = abs(line - round(line)) < 1e-4

        if not is_integer:
            # Half-point line: push probability is zero
            p_away = float(dist.cdf(k))
            p_home = float(1.0 - p_away)
            return float(np.clip(p_home, 0.01, 0.99)), float(np.clip(p_away, 0.01, 0.99)), 0.0

        # Integer line: continuity correction window [k - 0.5, k + 0.5]
        push_prob = float(dist.cdf(k + 0.5) - dist.cdf(k - 0.5))
        p_home_win = float(1.0 - dist.cdf(k + 0.5))
        p_away_win = float(dist.cdf(k - 0.5))

        # Push-adjusted win probability (excluding pushes)
        denom = max(1e-6, p_home_win + p_away_win)
        p_home_adj = float(p_home_win / denom)
        p_away_adj = float(p_away_win / denom)

        return float(np.clip(p_home_adj, 0.01, 0.99)), float(np.clip(p_away_adj, 0.01, 0.99)), float(push_prob)

    def price_total(
        self,
        mu_total: float,
        sigma_total: float,
        line: float,
    ) -> Tuple[float, float, float]:
        """
        Price game total line (e.g. 224.5 or 225.0).
        Returns: (p_over, p_under, push_prob)
        """
        pt = float(line)
        dist = student_t(df=self.nu_total, loc=mu_total, scale=sigma_total)

        is_integer = abs(pt - round(pt)) < 1e-4

        if not is_integer:
            p_under = float(dist.cdf(pt))
            p_over = float(1.0 - p_under)
            return float(np.clip(p_over, 0.01, 0.99)), float(np.clip(p_under, 0.01, 0.99)), 0.0

        push_prob = float(dist.cdf(pt + 0.5) - dist.cdf(pt - 0.5))
        p_over_win = float(1.0 - dist.cdf(pt + 0.5))
        p_under_win = float(dist.cdf(pt - 0.5))

        denom = max(1e-6, p_over_win + p_under_win)
        p_over_adj = float(p_over_win / denom)
        p_under_adj = float(p_under_win / denom)

        return float(np.clip(p_over_adj, 0.01, 0.99)), float(np.clip(p_under_adj, 0.01, 0.99)), float(push_prob)

    def blend_with_market(
        self,
        p_model: float,
        p_fair_mkt: Optional[float],
        n_books: Optional[int] = None,
    ) -> float:
        """
        Market-anchored posterior blending.
        Weight on model scales inversely with consensus thickness (n_books).
        """
        if p_fair_mkt is None or p_fair_mkt <= 0.0 or p_fair_mkt >= 1.0:
            return float(p_model)

        nb = max(1, int(n_books or 4))
        # Thickness factor: 4 books = 0.35 weight; 8+ books = 0.20 weight
        w_model = float(np.clip(0.35 * (4.0 / max(4.0, float(nb))), 0.15, 0.45))
        blended = w_model * p_model + (1.0 - w_model) * p_fair_mkt
        return float(np.clip(blended, 0.01, 0.99))

    def price_full_game(
        self,
        game_key: str,
        kalman_pred: KalmanGamePrediction,
        matchup: MatchupFeatures,
        market_odds: Optional[Dict[str, Any]] = None,
    ) -> PricedGame:
        """
        Synthesize full game predictions and evaluate Moneyline, Spread, and Totals
        against market odds.
        """
        mu_m, mu_t, exp_pace = self.compute_means(kalman_pred, matchup)
        sigma_m, sigma_t = self.compute_dispersions(kalman_pred, matchup, exp_pace)

        priced = PricedGame(
            game_key=game_key,
            home_team=matchup.home_team,
            away_team=matchup.away_team,
            game_date=matchup.game_date,
            mu_margin=mu_m,
            sigma_margin=sigma_m,
            mu_total=mu_t,
            sigma_total=sigma_t,
            exp_pace=exp_pace,
            markets=[],
        )

        mkt = market_odds or {}

        # 1. Moneyline (NBA_ML)
        p_home_ml, p_away_ml = self.price_moneyline(mu_m, sigma_m)
        h2h_info = mkt.get("h2h", {})
        h_fair = h2h_info.get("p_home_fair")
        a_fair = h2h_info.get("p_away_fair")
        n_b_h2h = h2h_info.get("n_books")

        # Home ML
        m_home_ml = self._build_priced_market(
            system="NBA_ML",
            market_label="Moneyline",
            side="home",
            line=None,
            p_model=p_home_ml,
            push_prob=0.0,
            p_fair=h_fair,
            best_odds=h2h_info.get("best_home_odds"),
            best_book=h2h_info.get("best_home_book"),
            n_books=n_b_h2h,
        )
        priced.markets.append(m_home_ml)

        # Away ML
        m_away_ml = self._build_priced_market(
            system="NBA_ML",
            market_label="Moneyline",
            side="away",
            line=None,
            p_model=p_away_ml,
            push_prob=0.0,
            p_fair=a_fair,
            best_odds=h2h_info.get("best_away_odds"),
            best_book=h2h_info.get("best_away_book"),
            n_books=n_b_h2h,
        )
        priced.markets.append(m_away_ml)

        # 2. Spread (NBA_SPREAD)
        spread_info = mkt.get("spreads", {})
        spread_line = spread_info.get("line")
        if spread_line is None:
            # Model fair spread (nearest half-point to -mu_m)
            spread_line = round(-mu_m * 2.0) / 2.0

        p_home_sp, p_away_sp, push_sp = self.price_spread(mu_m, sigma_m, float(spread_line))
        h_sp_fair = spread_info.get("p_home_fair")
        a_sp_fair = spread_info.get("p_away_fair")
        n_b_sp = spread_info.get("n_books")

        # Home Spread
        m_home_sp = self._build_priced_market(
            system="NBA_SPREAD",
            market_label=f"Spread {spread_line:+g}",
            side="home",
            line=float(spread_line),
            p_model=p_home_sp,
            push_prob=push_sp,
            p_fair=h_sp_fair,
            best_odds=spread_info.get("best_home_odds"),
            best_book=spread_info.get("best_home_book"),
            n_books=n_b_sp,
        )
        priced.markets.append(m_home_sp)

        # Away Spread
        m_away_sp = self._build_priced_market(
            system="NBA_SPREAD",
            market_label=f"Spread {-spread_line:+g}",
            side="away",
            line=-float(spread_line),
            p_model=p_away_sp,
            push_prob=push_sp,
            p_fair=a_sp_fair,
            best_odds=spread_info.get("best_away_odds"),
            best_book=spread_info.get("best_away_book"),
            n_books=n_b_sp,
        )
        priced.markets.append(m_away_sp)

        # 3. Game Total (NBA_TOTAL)
        totals_info = mkt.get("totals", {})
        total_line = totals_info.get("line")
        if total_line is None:
            # Model fair total (nearest half-point to mu_t)
            total_line = round(mu_t * 2.0) / 2.0

        p_over, p_under, push_tot = self.price_total(mu_t, sigma_t, float(total_line))
        over_fair = totals_info.get("p_over_fair")
        under_fair = totals_info.get("p_under_fair")
        n_b_tot = totals_info.get("n_books")

        # Over
        m_over = self._build_priced_market(
            system="NBA_TOTAL",
            market_label=f"Over {total_line:g}",
            side="over",
            line=float(total_line),
            p_model=p_over,
            push_prob=push_tot,
            p_fair=over_fair,
            best_odds=totals_info.get("best_over_odds"),
            best_book=totals_info.get("best_over_book"),
            n_books=n_b_tot,
        )
        priced.markets.append(m_over)

        # Under
        m_under = self._build_priced_market(
            system="NBA_TOTAL",
            market_label=f"Under {total_line:g}",
            side="under",
            line=float(total_line),
            p_model=p_under,
            push_prob=push_tot,
            p_fair=under_fair,
            best_odds=totals_info.get("best_under_odds"),
            best_book=totals_info.get("best_under_book"),
            n_books=n_b_tot,
        )
        priced.markets.append(m_under)

        return priced

    def _build_priced_market(
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
        p_blended = self.blend_with_market(p_model, p_fair, n_books)

        model_edge = None
        market_ev = None
        confluence = False
        score = 0

        if p_fair is not None:
            model_edge = float(p_model - p_fair)

        if best_odds is not None:
            dec = american_to_decimal(int(best_odds))
            # Market EV against de-vigged fair
            if p_fair is not None:
                market_ev = float((p_fair * dec) - 1.0)
            else:
                market_ev = float((p_model * dec) - 1.0)

            # Confluence check: model and market both signal edge on same side
            if model_edge is not None and market_ev is not None:
                confluence = bool(
                    model_edge >= 0.015
                    and market_ev >= 0.025
                    and p_model > p_fair
                )

            # Score calculation via lib/beezy_score
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
