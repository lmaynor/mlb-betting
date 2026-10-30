"""
nba.game.kalman_ratings -- Time-varying Kalman filter state-space team rating tracker.

Maintains latent dynamic state vectors theta_i = [O_i, D_i, P_i]^T for all NBA teams:
  * O_i: latent offensive rating (relative to league baseline, in points per 100 possessions)
  * D_i: latent defensive rating conceded (relative to league baseline, points allowed per 100 poss;
         lower/negative is better defense, positive is worse defense)
  * P_i: latent pace rating (possessions per 48 min relative to league baseline)

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

from nba.teams import TEAMS, team_code

log = logging.getLogger("nba.game.kalman_ratings")

DEFAULT_LEAGUE_PTS_100: float = 114.5
DEFAULT_LEAGUE_PACE: float = 99.0
DEFAULT_HCA_100: float = 2.4

# Prior standard deviations for a team entering cold
PRIOR_SIGMA_OFF: float = 4.5
PRIOR_SIGMA_DEF: float = 4.5
PRIOR_SIGMA_PACE: float = 2.5

# Daily process noise variances: Var(drift per day)
DAILY_Q_OFF: float = 0.04
DAILY_Q_DEF: float = 0.04
DAILY_Q_PACE: float = 0.02

# Observation noise variances: single-game variance in per-100 efficiency and pace
# Standard deviation ~ 9.5 pts/100, pace std ~ 4.0 poss
OBS_R_OFF: float = 90.0
OBS_R_DEF: float = 90.0
OBS_R_PACE: float = 16.0

OFFSEASON_RETENTION: float = 0.65
OFFSEASON_VARIANCE_INFLATION: Tuple[float, float, float] = (8.0, 8.0, 3.0)


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
    exp_home_pts: float
    exp_away_pts: float
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
    """State-space Kalman filter tracking all 30 NBA teams."""

    def __init__(
        self,
        league_pts_100: float = DEFAULT_LEAGUE_PTS_100,
        league_pace: float = DEFAULT_LEAGUE_PACE,
        hca_100: float = DEFAULT_HCA_100,
    ) -> None:
        self.league_pts_100 = float(league_pts_100)
        self.league_pace = float(league_pace)
        self.hca_100 = float(hca_100)
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
        code = team_code(team)
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
        hca_100: Optional[float] = None,
    ) -> KalmanGamePrediction:
        """
        Generate pre-game expected pace, offensive/defensive ratings, margin, and total.
        Propagates state uncertainty to predict margin and total standard deviations.
        """
        h_code = team_code(home)
        a_code = team_code(away)
        if not h_code or not a_code:
            raise ValueError(f"Unknown team code for home='{home}' or away='{away}'")

        h_state = self.get_state(h_code)
        a_state = self.get_state(a_code)

        # Apply process noise for elapsed rest
        self._apply_time_drift(h_state, game_date)
        self._apply_time_drift(a_state, game_date)

        h_theta, h_cov = h_state.theta, h_state.cov
        a_theta, a_cov = a_state.theta, a_state.cov

        hca = self.hca_100 if hca_100 is None else float(hca_100)

        # Expected pace
        # theta[2] is pace delta from league pace
        exp_pace = float(self.league_pace + h_theta[2] + a_theta[2])
        exp_pace = float(np.clip(exp_pace, 88.0, 112.0))

        # Expected efficiency per 100 poss
        # Home offense against Away defense + half HCA
        # theta[0] is offensive rating delta; theta[1] is defensive points conceded delta
        home_exp_100 = float(self.league_pts_100 + h_theta[0] + a_theta[1] + (hca / 2.0))
        away_exp_100 = float(self.league_pts_100 + a_theta[0] + h_theta[1] - (hca / 2.0))

        # Scale to game pace
        exp_home_pts = float(home_exp_100 * (exp_pace / 100.0))
        exp_away_pts = float(away_exp_100 * (exp_pace / 100.0))

        exp_margin = float(exp_home_pts - exp_away_pts)
        exp_total = float(exp_home_pts + exp_away_pts)

        # Propagate uncertainty
        pace_scale = (exp_pace / 100.0) ** 2
        var_h_100 = float(h_cov[0, 0] + a_cov[1, 1])
        var_a_100 = float(a_cov[0, 0] + h_cov[1, 1])

        var_margin = float((var_h_100 + var_a_100) * pace_scale)
        var_total = float((var_h_100 + var_a_100) * pace_scale)
        var_pace = float(h_cov[2, 2] + a_cov[2, 2])

        std_margin = float(np.sqrt(max(0.1, var_margin)))
        std_total = float(np.sqrt(max(0.1, var_total)))
        std_pace = float(np.sqrt(max(0.1, var_pace)))

        return KalmanGamePrediction(
            home_team=h_code,
            away_team=a_code,
            exp_margin=exp_margin,
            exp_total=exp_total,
            exp_pace=exp_pace,
            exp_home_pts=exp_home_pts,
            exp_away_pts=exp_away_pts,
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
        home_pts: float,
        away_pts: float,
        poss: float,
        minutes: float = 240.0,
        game_date: Optional[str] = None,
        hca_100: Optional[float] = None,
    ) -> None:
        """
        Execute post-game Kalman measurement update with Joseph form covariance stabilization.
        Joint observation vector y in R^3:
          y[0] = home_pts_per_100 - league_pts_100 - hca/2
          y[1] = away_pts_per_100 - league_pts_100 + hca/2
          y[2] = pace_per_48 - league_pace
        """
        h_code = team_code(home)
        a_code = team_code(away)
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

        hca = self.hca_100 if hca_100 is None else float(hca_100)
        poss = max(50.0, float(poss))
        minutes = max(180.0, float(minutes))

        # Observations
        home_100 = (float(home_pts) / poss) * 100.0
        away_100 = (float(away_pts) / poss) * 100.0
        pace_48 = poss * (240.0 / minutes)

        y_obs = np.array([
            home_100 - self.league_pts_100 - (hca / 2.0),
            away_100 - self.league_pts_100 + (hca / 2.0),
            pace_48 - self.league_pace,
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
        r_matrix = np.diag([OBS_R_OFF, OBS_R_DEF, OBS_R_PACE])

        # Innovation: y - H * theta
        y_pred = h_matrix @ theta_joint
        innovation = y_obs - y_pred

        # Innovation covariance: S = H * Cov * H^T + R
        s_matrix = h_matrix @ cov_joint @ h_matrix.T + r_matrix

        # Kalman gain: K = Cov * H^T * S^(-1)
        try:
            k_gain = cov_joint @ h_matrix.T @ np.linalg.inv(s_matrix)
        except np.linalg.LinAlgError:
            log.warning("Singular S matrix in Kalman update; skipping update")
            return

        # State update: theta = theta + K * innovation
        theta_updated = theta_joint + k_gain @ innovation

        # Covariance update via Joseph stabilized form:
        # Cov = (I - K*H) * Cov * (I - K*H)^T + K * R * K^T
        i_kh = np.eye(6) - k_gain @ h_matrix
        cov_updated = i_kh @ cov_joint @ i_kh.T + k_gain @ r_matrix @ k_gain.T

        # Symmetrize and clip
        cov_updated = 0.5 * (cov_updated + cov_updated.T)

        # Assign back to home and away
        h_state.theta = theta_updated[:3]
        h_state.cov = cov_updated[:3, :3]
        h_state.last_date = game_date
        h_state.games_played += 1

        a_state.theta = theta_updated[3:]
        a_state.cov = cov_updated[3:, 3:]
        a_state.last_date = game_date
        a_state.games_played += 1

    def apply_offseason_decay(
        self,
        retention: float = OFFSEASON_RETENTION,
        variance_inflation: Tuple[float, float, float] = OFFSEASON_VARIANCE_INFLATION,
    ) -> None:
        """
        Regress team ratings toward the league mean between seasons and expand variance.
        theta = retention * theta
        cov = retention^2 * cov + diag(variance_inflation)
        """
        gamma = float(retention)
        q_offseason = np.diag(list(variance_inflation))
        for state in self.teams.values():
            state.theta = gamma * state.theta
            state.cov = (gamma**2) * state.cov + q_offseason
            state.cov = 0.5 * (state.cov + state.cov.T)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "league_pts_100": self.league_pts_100,
            "league_pace": self.league_pace,
            "hca_100": self.hca_100,
            "teams": {k: v.to_dict() for k, v in self.teams.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> KalmanTeamTracker:
        tracker = cls(
            league_pts_100=data.get("league_pts_100", DEFAULT_LEAGUE_PTS_100),
            league_pace=data.get("league_pace", DEFAULT_LEAGUE_PACE),
            hca_100=data.get("hca_100", DEFAULT_HCA_100),
        )
        for k, v in data.get("teams", {}).items():
            tracker.teams[k] = TeamState.from_dict(v)
        return tracker
