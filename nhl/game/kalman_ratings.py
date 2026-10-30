"""
nhl.game.kalman_ratings -- Continuous-time Kalman filter state-space team rating tracker.

Maintains latent dynamic state vectors theta_i = [Off_i, Def_i, Pace_i]^T for all NHL teams:
  * Off_i: latent offensive scoring rate (relative to league baseline, in regulation goals per 60 min)
  * Def_i: latent defensive goals conceded rate (relative to league baseline, goals allowed per 60 min;
           lower/negative is better defense, positive is worse defense)
  * Pace_i: latent shot pace rating (shots on goal per 60 min relative to league baseline)

Updates use realized regulation goals and xG proxy (weighted 0.45 * ActualRegGoals + 0.55 * xG_proxy)
to prevent overreaction to 1-game shooting luck.
State covariance Sigma_i tracks parameter estimation uncertainty.
Process noise Q scales with elapsed rest days between games.
Offseason transition regresses ratings toward the league mean (factor gamma ~ 0.65)
and expands state uncertainty.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from nhl.config import TEAMS, team_abbrev

log = logging.getLogger("nhl.game.kalman_ratings")

DEFAULT_LEAGUE_GOALS: float = 2.85
DEFAULT_LEAGUE_SOG: float = 30.5
DEFAULT_HIA_GOALS: float = 0.18

# Prior standard deviations for a team entering cold (goals/60 and SOG/60)
PRIOR_SIGMA_OFF: float = 0.35
PRIOR_SIGMA_DEF: float = 0.35
PRIOR_SIGMA_PACE: float = 2.5

# Daily process noise variances: Var(drift per day)
DAILY_Q_OFF: float = 0.0005
DAILY_Q_DEF: float = 0.0005
DAILY_Q_PACE: float = 0.02

# Observation noise variances
# Realized signal blends actual regulation goals and xG proxy -> variance ~ 1.25
OBS_R_GOALS: float = 1.25
OBS_R_PACE: float = 25.0

OFFSEASON_RETENTION: float = 0.65
OFFSEASON_VARIANCE_INFLATION: Tuple[float, float, float] = (0.04, 0.04, 2.0)


@dataclass
class TeamState:
    team: str
    theta: np.ndarray  # shape (3,): [off_rtg, def_rtg, pace_rtg]
    cov: np.ndarray  # shape (3, 3)
    last_date: Optional[str] = None
    games_played: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "team": self.team,
            "theta": self.theta.tolist(),
            "cov": self.cov.tolist(),
            "last_date": self.last_date,
            "games_played": self.games_played,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TeamState:
        return cls(
            team=data["team"],
            theta=np.array(data["theta"], dtype=float),
            cov=np.array(data["cov"], dtype=float),
            last_date=data.get("last_date"),
            games_played=int(data.get("games_played", 0)),
        )


@dataclass
class KalmanGamePrediction:
    home_team: str
    away_team: str
    exp_margin: float
    exp_total: float
    exp_pace: float
    exp_home_goals: float
    exp_away_goals: float
    var_margin: float
    var_total: float
    var_pace: float
    std_margin: float
    std_total: float
    std_pace: float
    home_off_rtg: float
    home_def_rtg: float
    away_off_rtg: float
    away_def_rtg: float


class KalmanTeamTracker:
    """Continuous-time state-space Kalman filter tracking all 32 NHL teams."""

    def __init__(
        self,
        league_goals: float = DEFAULT_LEAGUE_GOALS,
        league_sog: float = DEFAULT_LEAGUE_SOG,
        hia_goals: float = DEFAULT_HIA_GOALS,
    ) -> None:
        self.league_goals = float(league_goals)
        self.league_sog = float(league_sog)
        self.hia_goals = float(hia_goals)
        self.teams: Dict[str, TeamState] = {}
        self._init_teams()

    def _init_teams(self) -> None:
        prior_cov = np.diag([
            PRIOR_SIGMA_OFF ** 2,
            PRIOR_SIGMA_DEF ** 2,
            PRIOR_SIGMA_PACE ** 2,
        ])
        for code, *_ in TEAMS:
            self.teams[code] = TeamState(
                team=code,
                theta=np.zeros(3, dtype=float),
                cov=prior_cov.copy(),
                last_date=None,
                games_played=0,
            )

    def get_state(self, team: str) -> TeamState:
        code = team_abbrev(team)
        if not code or code not in self.teams:
            prior_cov = np.diag([
                PRIOR_SIGMA_OFF ** 2,
                PRIOR_SIGMA_DEF ** 2,
                PRIOR_SIGMA_PACE ** 2,
            ])
            return TeamState(
                team=code or team,
                theta=np.zeros(3, dtype=float),
                cov=prior_cov.copy(),
            )
        return self.teams[code]

    def _apply_time_drift(self, state: TeamState, current_date: Optional[str]) -> None:
        """Inject process noise based on elapsed days since last game."""
        if not current_date or not state.last_date:
            return
        try:
            d_curr = datetime.strptime(str(current_date)[:10], "%Y-%m-%d").date()
            d_last = datetime.strptime(str(state.last_date)[:10], "%Y-%m-%d").date()
            delta_days = max(1, min(14, (d_curr - d_last).days))
        except Exception:
            delta_days = 1

        q_matrix = np.diag([
            DAILY_Q_OFF * delta_days,
            DAILY_Q_DEF * delta_days,
            DAILY_Q_PACE * delta_days,
        ])
        state.cov = state.cov + q_matrix

    def predict_game(
        self,
        home: str,
        away: str,
        game_date: Optional[str] = None,
        hia_goals: Optional[float] = None,
    ) -> KalmanGamePrediction:
        """
        Generate pre-game expected shot pace, offensive/defensive ratings, margin, and total.
        Propagates state uncertainty to predict margin and total standard deviations.
        """
        h_code = team_abbrev(home)
        a_code = team_abbrev(away)
        if not h_code or not a_code:
            raise ValueError(f"Unknown team code for home='{home}' or away='{away}'")

        h_state = self.get_state(h_code)
        a_state = self.get_state(a_code)

        # Apply process noise for elapsed rest
        self._apply_time_drift(h_state, game_date)
        self._apply_time_drift(a_state, game_date)

        h_theta, h_cov = h_state.theta, h_state.cov
        a_theta, a_cov = a_state.theta, a_state.cov

        hia = self.hia_goals if hia_goals is None else float(hia_goals)

        # Expected shot pace
        exp_pace = float(self.league_sog + h_theta[2] + a_theta[2])
        exp_pace = float(np.clip(exp_pace, 22.0, 42.0))

        # Expected regulation goals
        # Home offense against Away defense + half HIA
        exp_home_goals = float(max(1.1, self.league_goals + h_theta[0] + a_theta[1] + (hia / 2.0)))
        exp_away_goals = float(max(1.1, self.league_goals + a_theta[0] + h_theta[1] - (hia / 2.0)))

        exp_margin = float(exp_home_goals - exp_away_goals)
        exp_total = float(exp_home_goals + exp_away_goals)

        # Propagate uncertainty
        var_h = float(h_cov[0, 0] + a_cov[1, 1])
        var_a = float(a_cov[0, 0] + h_cov[1, 1])

        var_margin = float(var_h + var_a)
        var_total = float(var_h + var_a)
        var_pace = float(h_cov[2, 2] + a_cov[2, 2])

        std_margin = float(np.sqrt(max(0.01, var_margin)))
        std_total = float(np.sqrt(max(0.01, var_total)))
        std_pace = float(np.sqrt(max(0.01, var_pace)))

        return KalmanGamePrediction(
            home_team=h_code,
            away_team=a_code,
            exp_margin=exp_margin,
            exp_total=exp_total,
            exp_pace=exp_pace,
            exp_home_goals=exp_home_goals,
            exp_away_goals=exp_away_goals,
            var_margin=var_margin,
            var_total=var_total,
            var_pace=var_pace,
            std_margin=std_margin,
            std_total=std_total,
            std_pace=std_pace,
            home_off_rtg=float(h_theta[0]),
            home_def_rtg=float(h_theta[1]),
            away_off_rtg=float(a_theta[0]),
            away_def_rtg=float(a_theta[1]),
        )

    def update_game(
        self,
        home: str,
        away: str,
        home_goals: float,
        away_goals: float,
        home_sog: float = 30.0,
        away_sog: float = 30.0,
        home_xg_proxy: Optional[float] = None,
        away_xg_proxy: Optional[float] = None,
        minutes: float = 60.0,
        game_date: Optional[str] = None,
        hia_goals: Optional[float] = None,
    ) -> None:
        """
        Execute post-game Kalman measurement update with Joseph form covariance stabilization.
        Realized goals blend actual regulation goals and xG proxy:
          home_sig = 0.45 * actual + 0.55 * xg_proxy
        Joint observation vector y in R^3:
          y[0] = home_sig_per_60 - league_goals - hia/2
          y[1] = away_sig_per_60 - league_goals + hia/2
          y[2] = pace_per_60 - league_sog
        """
        h_code = team_abbrev(home)
        a_code = team_abbrev(away)
        if not h_code or not a_code:
            return

        h_state = self.teams.setdefault(
            h_code,
            TeamState(
                team=h_code,
                theta=np.zeros(3, dtype=float),
                cov=np.diag([PRIOR_SIGMA_OFF**2, PRIOR_SIGMA_DEF**2, PRIOR_SIGMA_PACE**2]),
            ),
        )
        a_state = self.teams.setdefault(
            a_code,
            TeamState(
                team=a_code,
                theta=np.zeros(3, dtype=float),
                cov=np.diag([PRIOR_SIGMA_OFF**2, PRIOR_SIGMA_DEF**2, PRIOR_SIGMA_PACE**2]),
            ),
        )

        self._apply_time_drift(h_state, game_date)
        self._apply_time_drift(a_state, game_date)

        hia = self.hia_goals if hia_goals is None else float(hia_goals)
        minutes = max(50.0, float(minutes))
        scale_60 = 60.0 / minutes

        h_xg = home_xg_proxy if home_xg_proxy is not None else float(home_goals)
        a_xg = away_xg_proxy if away_xg_proxy is not None else float(away_goals)

        home_sig = (0.45 * float(home_goals) + 0.55 * float(h_xg)) * scale_60
        away_sig = (0.45 * float(away_goals) + 0.55 * float(a_xg)) * scale_60
        pace_obs = ((float(home_sog) + float(away_sog)) / 2.0) * scale_60

        y_obs = np.array([
            home_sig - self.league_goals - (hia / 2.0),
            away_sig - self.league_goals + (hia / 2.0),
            pace_obs - self.league_sog,
        ], dtype=float)

        # Joint state vector in R^6: [O_h, D_h, P_h, O_a, D_a, P_a]
        theta_joint = np.concatenate([h_state.theta, a_state.theta])
        cov_joint = np.zeros((6, 6), dtype=float)
        cov_joint[:3, :3] = h_state.cov
        cov_joint[3:, 3:] = a_state.cov

        # Measurement matrix H in R^(3x6)
        # y[0] = O_h + D_a
        # y[1] = D_h + O_a
        # y[2] = P_h + P_a
        h_matrix = np.array([
            [1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            [0.0, 1.0, 0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        ], dtype=float)

        # Measurement noise covariance R in R^(3x3)
        r_matrix = np.diag([OBS_R_GOALS, OBS_R_GOALS, OBS_R_PACE])

        # Innovation
        y_pred = h_matrix @ theta_joint
        innovation = y_obs - y_pred

        # Innovation covariance S = H * Cov * H^T + R
        s_matrix = h_matrix @ cov_joint @ h_matrix.T + r_matrix

        try:
            s_inv = np.linalg.inv(s_matrix)
        except np.linalg.LinAlgError:
            s_inv = np.linalg.pinv(s_matrix)

        # Kalman gain K = Cov * H^T * S^(-1) in R^(6x3)
        kalman_gain = cov_joint @ h_matrix.T @ s_inv

        # State update
        theta_updated = theta_joint + kalman_gain @ innovation

        # Joseph form covariance update for numerical stability:
        # P = (I - K*H) * P * (I - K*H)^T + K * R * K^T
        eye6 = np.eye(6)
        i_kh = eye6 - kalman_gain @ h_matrix
        cov_updated = i_kh @ cov_joint @ i_kh.T + kalman_gain @ r_matrix @ kalman_gain.T

        # Symmetrize
        cov_updated = (cov_updated + cov_updated.T) / 2.0

        # Unpack
        h_state.theta = theta_updated[:3]
        h_state.cov = cov_updated[:3, :3]
        h_state.last_date = game_date
        h_state.games_played += 1

        a_state.theta = theta_updated[3:]
        a_state.cov = cov_updated[3:, 3:]
        a_state.last_date = game_date
        a_state.games_played += 1

    def apply_offseason_decay(self, retention: float = OFFSEASON_RETENTION) -> None:
        """Regress ratings toward zero (league mean) between seasons and expand uncertainty toward prior."""
        gamma = float(retention)
        prior_cov = np.diag([
            PRIOR_SIGMA_OFF ** 2,
            PRIOR_SIGMA_DEF ** 2,
            PRIOR_SIGMA_PACE ** 2,
        ])
        for state in self.teams.values():
            state.theta = state.theta * gamma
            state.cov = (state.cov * (gamma ** 2)) + ((1.0 - (gamma ** 2)) * prior_cov)
            state.last_date = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "league_goals": self.league_goals,
            "league_sog": self.league_sog,
            "hia_goals": self.hia_goals,
            "teams": {k: v.to_dict() for k, v in self.teams.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> KalmanTeamTracker:
        tracker = cls(
            league_goals=data.get("league_goals", DEFAULT_LEAGUE_GOALS),
            league_sog=data.get("league_sog", DEFAULT_LEAGUE_SOG),
            hia_goals=data.get("hia_goals", DEFAULT_HIA_GOALS),
        )
        if "teams" in data:
            tracker.teams = {k: TeamState.from_dict(v) for k, v in data["teams"].items()}
        return tracker
