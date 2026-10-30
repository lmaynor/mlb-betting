"""
nhl.game.matrix_pricer -- Dixon-Coles Bivariate Poisson + Empty-Net Goal (ENG) +
Overtime/Shootout Exact Score Matrix Pricer.

Transforms expected regulation scoring rates (lambda_H, lambda_A) into an exact
13x13 joint probability distribution over final scores:
  Stage 1: Pre-empty-net 57-minute base regulation grid via Dixon-Coles (rho_DC ~ -0.06).
  Stage 2: Late-game Empty-Net Goal (ENG) Markov operator (p_ENG = 0.145, p_6v5_tie = 0.080).
  Stage 3: OT & Shootout resolution on diagonal x == y:
           P(Home Wins OT/SO | Tie) = sigmoid(0.08 + 0.35 * (ln lambda_H - ln lambda_A)).
           Diag cleared to 0.0; winner receives +1 goal at (k+1, k) or (k, k+1).

Computes 100% mathematically coherent probabilities for:
  * Moneyline (NHL_ML)
  * Puck Line / Spread (NHL_SPREAD, -1.5 / +1.5 and alternates)
  * Game Totals (NHL_TOTAL, 5.5 / 6.0 / 6.5, with integer push handling)
  * Confluence detection and market-anchored posterior blending.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.stats import poisson

from mlb_core.alerts.beezy_score import beezy_score as calc_beezy_score
from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob, devig_two_way
from nhl.config import team_abbrev
from nhl.game.features import MatchupFeatures
from nhl.game.kalman_ratings import KalmanGamePrediction

log = logging.getLogger("nhl.game.matrix_pricer")

GRID_SIZE: int = 13  # scores 0 to 12
RHO_DIXON_COLES: float = -0.06
P_ENG_LEAD_1: float = 0.145
P_6V5_TIE_LEAD_1: float = 0.080
SHOOTOUT_SHARE_OF_TIES: float = 0.32  # 32% SO, 68% 3v3 OT


@dataclass
class ScoreMatrixResult:
    lambda_h: float
    lambda_a: float
    m_stage1: np.ndarray  # (13, 13)
    m_stage2: np.ndarray  # (13, 13)
    m_final: np.ndarray   # (13, 13)
    p_home_ml: float
    p_away_ml: float
    p_tie_reg: float
    p_home_reg: float
    p_away_reg: float
    p_home_ot_so_win: float

    def puck_line(self, line_home: float) -> Tuple[float, float, float]:
        """
        Evaluate Home / Away cover probabilities for line_home (e.g. -1.5, +1.5).
        Returns: (p_home_cover, p_away_cover, p_push)
        """
        line = float(line_home)
        p_home = 0.0
        p_away = 0.0
        p_push = 0.0

        for x in range(GRID_SIZE):
            for y in range(GRID_SIZE):
                prob = float(self.m_final[x, y])
                margin = (x - y) + line
                if margin > 1e-9:
                    p_home += prob
                elif margin < -1e-9:
                    p_away += prob
                else:
                    p_push += prob

        return p_home, p_away, p_push

    def total(self, line: float, exclude_shootout: bool = False) -> Tuple[float, float, float, float]:
        """
        Evaluate Game Total for line (e.g. 5.5, 6.0, 6.5).
        Returns: (p_over_raw, p_under_raw, p_push, p_over_fair)
        """
        l_val = float(line)
        p_over = 0.0
        p_under = 0.0
        p_push = 0.0

        target_matrix = self.m_final
        if exclude_shootout:
            # Reconstruct matrix where 32% of tie-deciders do not add +1 goal to game total
            target_matrix = self.m_stage2.copy()
            # OT wins still add 1 goal for the 68% that finish in 3v3 OT
            p_ot = 1.0 - SHOOTOUT_SHARE_OF_TIES
            for k in range(GRID_SIZE):
                t_prob = float(self.m_stage2[k, k])
                if t_prob > 0:
                    target_matrix[k, k] = t_prob * SHOOTOUT_SHARE_OF_TIES
                    if k + 1 < GRID_SIZE:
                        target_matrix[k + 1, k] += t_prob * p_ot * self.p_home_ot_so_win
                        target_matrix[k, k + 1] += t_prob * p_ot * (1.0 - self.p_home_ot_so_win)

        for x in range(GRID_SIZE):
            for y in range(GRID_SIZE):
                prob = float(target_matrix[x, y])
                tot = x + y
                if tot > l_val + 1e-9:
                    p_over += prob
                elif tot < l_val - 1e-9:
                    p_under += prob
                else:
                    p_push += prob

        two_way = p_over + p_under
        p_over_fair = (p_over / two_way) if two_way > 0 else 0.5
        return p_over, p_under, p_push, p_over_fair


@dataclass
class PricedMarket:
    system: str  # 'NHL_ML', 'NHL_SPREAD', 'NHL_TOTAL'
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
    lambda_h: float
    lambda_a: float
    exp_total: float
    sigma_margin: float
    sigma_total: float
    markets: List[PricedMarket] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_key": self.game_key,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "game_date": self.game_date,
            "lambda_h": round(self.lambda_h, 3),
            "lambda_a": round(self.lambda_a, 3),
            "exp_total": round(self.exp_total, 3),
            "sigma_margin": round(self.sigma_margin, 2),
            "sigma_total": round(self.sigma_total, 2),
            "markets": [m.to_dict() for m in self.markets],
        }


class DistributionalNHLMatrixEngine:
    """Quantitative Dixon-Coles + ENG + OT/SO Matrix Pricer for NHL."""

    def __init__(
        self,
        rho_dc: float = RHO_DIXON_COLES,
        p_eng: float = P_ENG_LEAD_1,
        p_6v5_tie: float = P_6V5_TIE_LEAD_1,
    ) -> None:
        self.rho_dc = float(rho_dc)
        self.p_eng = float(p_eng)
        self.p_6v5_tie = float(p_6v5_tie)

    def compute_scoring_rates(
        self,
        kalman_pred: KalmanGamePrediction,
        matchup: MatchupFeatures,
    ) -> Tuple[float, float]:
        """Synthesize 60-minute regulation scoring intensities lambda_H and lambda_A."""
        lh = float(kalman_pred.exp_home_goals)
        la = float(kalman_pred.exp_away_goals)

        # 1. Goalie GSAx adjustment: each +1.0 GSAx/60 lowers opponent scoring rate by ~0.85 goals
        lh = lh - 0.85 * float(matchup.away_goalie_gsax_60)
        la = la - 0.85 * float(matchup.home_goalie_gsax_60)

        # 2. Z-score differential adjustments
        z = matchup.z_diff
        delta_h = (
            0.15 * z.get("xg_share", 0.0)
            + 0.08 * z.get("sog_share", 0.0)
            + 0.08 * z.get("pp_goals_per_game", 0.0)
            - 0.12 * z.get("sh_pct_luck", 0.0)
            + 0.04 * z.get("blocks_per_60", 0.0)
        )
        lh += delta_h
        la -= delta_h

        # 3. Schedule fatigue and road B2B penalty
        fatigue = matchup.fatigue
        la += fatigue.road_b2b_penalty  # -0.18 when Away on road B2B

        # 4. Roster injury drag
        lh -= max(0.0, float(matchup.home_injury_drag))
        la -= max(0.0, float(matchup.away_injury_drag))

        # Boundary guard: regulation scoring intensity must be positive
        lh = float(np.clip(lh, 1.20, 6.00))
        la = float(np.clip(la, 1.20, 6.00))
        return lh, la

    def compute_score_matrix(
        self,
        lambda_h: float,
        lambda_a: float,
        enable_eng: bool = True,
    ) -> ScoreMatrixResult:
        """
        Generate exact 3-stage score matrix.
        Stage 1: Pre-Empty-Net Dixon-Coles
        Stage 2: Late-game Empty-Net Goal Markov operator
        Stage 3: Overtime & Shootout resolution on diagonal
        """
        lh, la = float(lambda_h), float(lambda_a)

        # Stage 1: Base Poisson + Dixon-Coles low-score adjustment
        p_h = [poisson.pmf(x, lh) for x in range(GRID_SIZE)]
        p_a = [poisson.pmf(y, la) for y in range(GRID_SIZE)]

        m_s1 = np.zeros((GRID_SIZE, GRID_SIZE), dtype=float)
        for x in range(GRID_SIZE):
            for y in range(GRID_SIZE):
                tau = 1.0
                if x == 0 and y == 0:
                    tau = 1.0 - lh * la * self.rho_dc
                elif x == 0 and y == 1:
                    tau = 1.0 + la * self.rho_dc
                elif x == 1 and y == 0:
                    tau = 1.0 + lh * self.rho_dc
                elif x == 1 and y == 1:
                    tau = 1.0 - self.rho_dc
                m_s1[x, y] = p_h[x] * p_a[y] * max(0.0, tau)

        # Normalize Stage 1
        s1_sum = float(np.sum(m_s1))
        if s1_sum > 0:
            m_s1 = m_s1 / s1_sum

        # Stage 2: Empty-Net Goal Markov Operator
        m_s2 = m_s1.copy()
        if enable_eng:
            for x in range(GRID_SIZE):
                for y in range(GRID_SIZE):
                    # Home leads by 1 (x - y == 1)
                    if x - y == 1:
                        cell_mass = float(m_s1[x, y])
                        eng_mass = cell_mass * self.p_eng
                        tie_mass = cell_mass * self.p_6v5_tie
                        m_s2[x, y] -= (eng_mass + tie_mass)
                        # Home scores ENG -> (x+1, y)
                        if x + 1 < GRID_SIZE:
                            m_s2[x + 1, y] += eng_mass
                        else:
                            m_s2[x, y] += eng_mass
                        # Away ties 6v5 -> (x, y+1)
                        if y + 1 < GRID_SIZE:
                            m_s2[x, y + 1] += tie_mass
                        else:
                            m_s2[x, y] += tie_mass

                    # Away leads by 1 (y - x == 1)
                    elif y - x == 1:
                        cell_mass = float(m_s1[x, y])
                        eng_mass = cell_mass * self.p_eng
                        tie_mass = cell_mass * self.p_6v5_tie
                        m_s2[x, y] -= (eng_mass + tie_mass)
                        # Away scores ENG -> (x, y+1)
                        if y + 1 < GRID_SIZE:
                            m_s2[x, y + 1] += eng_mass
                        else:
                            m_s2[x, y] += eng_mass
                        # Home ties 6v5 -> (x+1, y)
                        if x + 1 < GRID_SIZE:
                            m_s2[x + 1, y] += tie_mass
                        else:
                            m_s2[x, y] += tie_mass

        # Normalize Stage 2
        s2_sum = float(np.sum(m_s2))
        if s2_sum > 0:
            m_s2 = m_s2 / s2_sum

        # Stage 3: OT & Shootout Resolution on diagonal x == y
        m_final = m_s2.copy()
        # Conditional OT/SO win probability
        log_ratio = math.log(lh) - math.log(la)
        p_home_ot_so = 1.0 / (1.0 + math.exp(-(0.08 + 0.35 * log_ratio)))

        p_tie_reg = float(sum(m_s2[k, k] for k in range(GRID_SIZE)))
        p_home_reg = float(sum(m_s2[x, y] for x in range(GRID_SIZE) for y in range(GRID_SIZE) if x > y))
        p_away_reg = float(sum(m_s2[x, y] for x in range(GRID_SIZE) for y in range(GRID_SIZE) if y > x))

        for k in range(GRID_SIZE):
            tie_mass = float(m_s2[k, k])
            if tie_mass > 0:
                m_final[k, k] = 0.0
                h_win_mass = tie_mass * p_home_ot_so
                a_win_mass = tie_mass * (1.0 - p_home_ot_so)

                if k + 1 < GRID_SIZE:
                    m_final[k + 1, k] += h_win_mass
                    m_final[k, k + 1] += a_win_mass
                else:
                    m_final[k, k - 1] += h_win_mass
                    m_final[k - 1, k] += a_win_mass

        # Normalize Stage 3
        final_sum = float(np.sum(m_final))
        if final_sum > 0:
            m_final = m_final / final_sum

        p_home_ml = float(sum(m_final[x, y] for x in range(GRID_SIZE) for y in range(GRID_SIZE) if x > y))
        p_away_ml = float(sum(m_final[x, y] for x in range(GRID_SIZE) for y in range(GRID_SIZE) if y > x))

        return ScoreMatrixResult(
            lambda_h=lh,
            lambda_a=la,
            m_stage1=m_s1,
            m_stage2=m_s2,
            m_final=m_final,
            p_home_ml=p_home_ml,
            p_away_ml=p_away_ml,
            p_tie_reg=p_tie_reg,
            p_home_reg=p_home_reg,
            p_away_reg=p_away_reg,
            p_home_ot_so_win=p_home_ot_so,
        )

    def blend_with_market(
        self,
        p_model: float,
        p_fair: Optional[float],
        n_books: Optional[int],
        w_model: float = 0.20,
    ) -> float:
        """Blend standalone model probability with de-vigged market consensus."""
        if p_fair is None:
            return float(np.clip(p_model, 0.01, 0.99))

        nb = n_books if n_books is not None else 1
        if nb < 3:
            return float(np.clip(p_model, 0.01, 0.99))

        blended = (w_model * p_model) + ((1.0 - w_model) * p_fair)
        return float(np.clip(blended, 0.01, 0.99))

    def price_game(
        self,
        kalman_pred: KalmanGamePrediction,
        matchup: MatchupFeatures,
        game_key: str,
        market_odds: Optional[Dict[str, Any]] = None,
    ) -> PricedGame:
        """
        Synthesize game predictions and price Moneyline, Puck Line, and Game Totals
        against live market odds.
        """
        lh, la = self.compute_scoring_rates(kalman_pred, matchup)
        matrix = self.compute_score_matrix(lh, la)

        exp_total = lh + la
        # Dispersion for Kalshi RFQ / spreads
        sigma_m = float(kalman_pred.std_margin if hasattr(kalman_pred, "std_margin") else 2.3)
        sigma_t = float(kalman_pred.std_total if hasattr(kalman_pred, "std_total") else 2.5)

        priced = PricedGame(
            game_key=game_key,
            home_team=matchup.home_team,
            away_team=matchup.away_team,
            game_date=matchup.game_date,
            lambda_h=lh,
            lambda_a=la,
            exp_total=exp_total,
            sigma_margin=sigma_m,
            sigma_total=sigma_t,
            markets=[],
        )

        mkt = market_odds or {}

        # 1. Moneyline (NHL_ML)
        h2h_info = mkt.get("h2h", {})
        h_fair = h2h_info.get("p_home_fair")
        a_fair = h2h_info.get("p_away_fair")
        n_b_h2h = h2h_info.get("n_books")

        # Home ML
        m_home_ml = self._build_priced_market(
            system="NHL_ML",
            market_label="Moneyline",
            side="home",
            line=None,
            p_model=matrix.p_home_ml,
            push_prob=0.0,
            p_fair=h_fair,
            best_odds=h2h_info.get("best_home_odds"),
            best_book=h2h_info.get("best_home_book"),
            n_books=n_b_h2h,
        )
        priced.markets.append(m_home_ml)

        # Away ML
        m_away_ml = self._build_priced_market(
            system="NHL_ML",
            market_label="Moneyline",
            side="away",
            line=None,
            p_model=matrix.p_away_ml,
            push_prob=0.0,
            p_fair=a_fair,
            best_odds=h2h_info.get("best_away_odds"),
            best_book=h2h_info.get("best_away_book"),
            n_books=n_b_h2h,
        )
        priced.markets.append(m_away_ml)

        # 2. Puck Line / Spread (NHL_SPREAD, -1.5 / +1.5 standard)
        spread_info = mkt.get("spreads", {})
        spread_line = spread_info.get("line")
        if spread_line is None:
            # Standard NHL Puck Line is -1.5 for favorite, +1.5 for underdog
            spread_line = -1.5 if matrix.p_home_ml >= 0.50 else 1.5

        p_h_cover, p_a_cover, p_sp_push = matrix.puck_line(float(spread_line))
        h_sp_fair = spread_info.get("p_home_fair")
        a_sp_fair = spread_info.get("p_away_fair")
        n_b_sp = spread_info.get("n_books")

        # Home Puck Line
        m_home_sp = self._build_priced_market(
            system="NHL_SPREAD",
            market_label=f"Puck Line {spread_line:+g}",
            side="home",
            line=float(spread_line),
            p_model=p_h_cover,
            push_prob=p_sp_push,
            p_fair=h_sp_fair,
            best_odds=spread_info.get("best_home_odds"),
            best_book=spread_info.get("best_home_book"),
            n_books=n_b_sp,
        )
        priced.markets.append(m_home_sp)

        # Away Puck Line
        m_away_sp = self._build_priced_market(
            system="NHL_SPREAD",
            market_label=f"Puck Line {-spread_line:+g}",
            side="away",
            line=-float(spread_line),
            p_model=p_a_cover,
            push_prob=p_sp_push,
            p_fair=a_sp_fair,
            best_odds=spread_info.get("best_away_odds"),
            best_book=spread_info.get("best_away_book"),
            n_books=n_b_sp,
        )
        priced.markets.append(m_away_sp)

        # 3. Game Totals (NHL_TOTAL, 5.5 / 6.0 / 6.5 standard)
        totals_info = mkt.get("totals", {})
        total_line = totals_info.get("line")
        if total_line is None:
            # Nearest half-point to expected total (e.g. 5.5, 6.0, 6.5)
            total_line = round(exp_total * 2.0) / 2.0

        p_over_raw, p_under_raw, p_tot_push, p_over_fair_m = matrix.total(float(total_line))
        over_fair = totals_info.get("p_over_fair")
        under_fair = totals_info.get("p_under_fair")
        n_b_tot = totals_info.get("n_books")

        # Over
        m_over = self._build_priced_market(
            system="NHL_TOTAL",
            market_label=f"Over {total_line:g}",
            side="over",
            line=float(total_line),
            p_model=p_over_fair_m,
            push_prob=p_tot_push,
            p_fair=over_fair,
            best_odds=totals_info.get("best_over_odds"),
            best_book=totals_info.get("best_over_book"),
            n_books=n_b_tot,
        )
        priced.markets.append(m_over)

        # Under
        m_under = self._build_priced_market(
            system="NHL_TOTAL",
            market_label=f"Under {total_line:g}",
            side="under",
            line=float(total_line),
            p_model=1.0 - p_over_fair_m,
            push_prob=p_tot_push,
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
