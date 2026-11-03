"""
nfl.game.kalman_ratings -- Opponent-adjusted Kalman Filter State-Space team ratings for NFL.

Maintains a 5-dimensional latent state per team:
    theta_i = [OffPts, DefPts, PassEPA, RushEPA, Pace]

Updated after each completed game, with:
    - Blended EPA + actual score observations (55% EPA, 45% actual, capped at +/-24 pts margin)
    - Time-varying process noise proportional to days elapsed
    - Offseason regression: carryover factor 0.55

Modern NFL HCA is ~1.8 points (diminished from the historical ~3 pts).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import numpy as np

log = logging.getLogger("nfl.game.kalman_ratings")

# -- Dimensions ------------------------------------------------------------------
N_DIM: int = 5   # OffPts, DefPts, PassEPA, RushEPA, Pace

# -- Prior standard deviations (cold start) -------------------------------------
PRIOR_SIGMA: Tuple[float, ...] = (5.0, 5.0, 0.20, 0.12, 2.0)

# -- Daily process noise variances -----------------------------------------------
DAILY_Q: Tuple[float, ...] = (0.12, 0.12, 0.004, 0.003, 0.05)

# -- Observation noise (per-game single-game variance in each observed quantity) -
OBS_R_PTS: float = 120.0       # single-game variance of pts_per_game observations
OBS_R_EPA: float = 0.025       # single-game EPA variance (~0.16 sd per game)
OBS_R_PACE: float = 5.0        # plays-per-game variance

# -- Offseason parameters --------------------------------------------------------
OFFSEASON_RETENTION: float = 0.55
OFFSEASON_VARIANCE_INFLATION: Tuple[float, ...] = (8.0, 8.0, 0.04, 0.025, 2.0)

# -- Home-field advantage --------------------------------------------------------
DEFAULT_HCA_PTS: float = 1.8   # modern NFL HCA in points on the spread
ALTITUDE_BONUS: float = 0.4    # DEN at home
NEUTRAL_SITE_HCA: float = 0.0  # London / Frankfurt / international games

ALTITUDE_TEAMS = {"DEN"}
NEUTRAL_SITE_GAMES = {"london", "frankfurt", "international"}   # matched in game_id or location


@dataclass
class NFLTeamState:
    team: str
    theta: np.ndarray   # shape (5,): [OffPts, DefPts, PassEPA, RushEPA, Pace]
    cov: np.ndarray     # shape (5, 5)
    last_date: Optional[str] = None
    games_played: int = 0
    current_season: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "team": self.team,
            "theta": self.theta.tolist(),
            "cov": self.cov.tolist(),
            "last_date": self.last_date,
            "games_played": self.games_played,
            "current_season": self.current_season,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NFLTeamState":
        return cls(
            team=d["team"],
            theta=np.array(d["theta"], dtype=float),
            cov=np.array(d["cov"], dtype=float),
            last_date=d.get("last_date"),
            games_played=int(d.get("games_played", 0)),
            current_season=d.get("current_season"),
        )


@dataclass
class KalmanGamePrediction:
    home_team: str
    away_team: str
    game_date: str
    season: int
    week: int
    hca_pts: float
    exp_margin: float         # positive = home favored
    exp_total: float
    var_margin: float
    var_total: float
    std_margin: float
    std_total: float
    home_off_pts: float       # theta[0] (OffPts delta from league avg)
    home_def_pts: float       # theta[1] (DefPts delta from league avg; positive = worse defense)
    home_pass_epa: float
    home_rush_epa: float
    away_off_pts: float
    away_def_pts: float
    away_pass_epa: float
    away_rush_epa: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "home_team": self.home_team,
            "away_team": self.away_team,
            "game_date": self.game_date,
            "season": self.season,
            "week": self.week,
            "hca_pts": round(self.hca_pts, 2),
            "exp_margin": round(self.exp_margin, 2),
            "exp_total": round(self.exp_total, 2),
            "var_margin": round(self.var_margin, 2),
            "var_total": round(self.var_total, 2),
            "std_margin": round(self.std_margin, 2),
            "std_total": round(self.std_total, 2),
            "home_off_pts": round(self.home_off_pts, 3),
            "home_def_pts": round(self.home_def_pts, 3),
            "away_off_pts": round(self.away_off_pts, 3),
            "away_def_pts": round(self.away_def_pts, 3),
        }


def _prior_cov() -> np.ndarray:
    return np.diag([s ** 2 for s in PRIOR_SIGMA])


def _default_state(team: str) -> NFLTeamState:
    return NFLTeamState(team=team, theta=np.zeros(N_DIM, dtype=float), cov=_prior_cov())


def _nfl_season_of(date_str: str) -> int:
    """NFL season year from a date string (YYYY-MM-DD). Rolls over on the Wednesday after Labor Day."""
    try:
        from datetime import date as _date
        d = _date.fromisoformat(str(date_str)[:10])
        labor_day = next(_date(d.year, 9, day) for day in range(1, 8)
                         if _date(d.year, 9, day).weekday() == 0)
        season_start = _date(d.year, 9, labor_day.day + 2)
        return d.year if d >= season_start else d.year - 1
    except Exception:
        return 0


class KalmanNFLTracker:
    """Opponent-adjusted Kalman Filter tracker for all 32 NFL teams."""

    NFL_TEAMS = [
        "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
        "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
        "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG",
        "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
    ]

    LEAGUE_PTS_PER_GAME: float = 23.5
    LEAGUE_TOTAL: float = 47.0

    def __init__(
        self,
        hca_pts: float = DEFAULT_HCA_PTS,
    ) -> None:
        self.hca_pts = float(hca_pts)
        self.teams: Dict[str, NFLTeamState] = {}
        self.current_season: Optional[int] = None
        for t in self.NFL_TEAMS:
            self.teams[t] = _default_state(t)

    def _get_or_create(self, team: str) -> NFLTeamState:
        t = str(team).upper().strip()
        # Normalize common aliases
        _ALIAS = {"JAC": "JAX", "LA": "LAR", "STL": "LAR", "OAK": "LV", "SD": "LAC",
                  "WSH": "WAS", "HON": "HOU", "GBP": "GB"}
        t = _ALIAS.get(t, t)
        if t not in self.teams:
            log.debug("Unknown team %r; creating cold-start state", t)
            self.teams[t] = _default_state(t)
        return self.teams[t]

    def _elapsed_days(self, state: NFLTeamState, current_date: str) -> int:
        if not state.last_date:
            return 7
        try:
            d1 = datetime.strptime(str(state.last_date)[:10], "%Y-%m-%d").date()
            d2 = datetime.strptime(str(current_date)[:10], "%Y-%m-%d").date()
            return max(1, min(21, (d2 - d1).days))
        except Exception:
            return 7

    def _drifted_cov(self, state: NFLTeamState, current_date: str) -> np.ndarray:
        """Process noise accumulation since last game (pure: returns a new array)."""
        days = self._elapsed_days(state, current_date)
        q = np.diag([q_i * days for q_i in DAILY_Q])
        return state.cov + q

    def roll_season(self, game_date: str) -> bool:
        """Apply offseason regression when `game_date` falls in a new NFL season. Returns True if decay applied."""
        season = _nfl_season_of(game_date)
        if season <= 0:
            return False
        prev = self.current_season
        self.current_season = season if prev is None else max(prev, season)
        if prev is not None and season > prev:
            self._apply_offseason_decay()
            return True
        return False

    def _apply_offseason_decay(self) -> None:
        gamma = OFFSEASON_RETENTION
        q_off = np.diag(list(OFFSEASON_VARIANCE_INFLATION))
        for state in self.teams.values():
            state.theta = gamma * state.theta
            state.cov = (gamma ** 2) * state.cov + q_off
            state.cov = 0.5 * (state.cov + state.cov.T)

    def _compute_hca(self, home: str) -> float:
        h = str(home).upper()
        if h in ALTITUDE_TEAMS:
            return DEFAULT_HCA_PTS + ALTITUDE_BONUS
        return DEFAULT_HCA_PTS

    def predict_game(
        self,
        home: str,
        away: str,
        season: int,
        week: int,
        game_date: str,
    ) -> KalmanGamePrediction:
        """Pre-game Kalman prediction: expected margin and total with parameter uncertainty."""
        h = self._get_or_create(home)
        a = self._get_or_create(away)

        h_cov = self._drifted_cov(h, game_date)
        a_cov = self._drifted_cov(a, game_date)

        hca = self._compute_hca(home)

        # Expected scoring:
        # Home pts = league_avg + OffPts_h - DefPts_a + hca/2
        # Away pts = league_avg + OffPts_a - DefPts_h - hca/2
        exp_home_pts = self.LEAGUE_PTS_PER_GAME + h.theta[0] - a.theta[1] + (hca / 2.0)
        exp_away_pts = self.LEAGUE_PTS_PER_GAME + a.theta[0] - h.theta[1] - (hca / 2.0)

        exp_home_pts = float(np.clip(exp_home_pts, 7.0, 55.0))
        exp_away_pts = float(np.clip(exp_away_pts, 7.0, 55.0))

        exp_margin = exp_home_pts - exp_away_pts
        exp_total = exp_home_pts + exp_away_pts

        # Uncertainty propagation
        var_h = float(h_cov[0, 0] + a_cov[1, 1])
        var_a = float(a_cov[0, 0] + h_cov[1, 1])
        var_margin = var_h + var_a
        var_total = var_h + var_a

        return KalmanGamePrediction(
            home_team=home,
            away_team=away,
            game_date=game_date,
            season=season,
            week=week,
            hca_pts=hca,
            exp_margin=exp_margin,
            exp_total=exp_total,
            var_margin=var_margin,
            var_total=var_total,
            std_margin=float(np.sqrt(max(0.1, var_margin))),
            std_total=float(np.sqrt(max(0.1, var_total))),
            home_off_pts=float(h.theta[0]),
            home_def_pts=float(h.theta[1]),
            home_pass_epa=float(h.theta[2]),
            home_rush_epa=float(h.theta[3]),
            away_off_pts=float(a.theta[0]),
            away_def_pts=float(a.theta[1]),
            away_pass_epa=float(a.theta[2]),
            away_rush_epa=float(a.theta[3]),
        )

    def update_game(
        self,
        home: str,
        away: str,
        home_score: float,
        away_score: float,
        home_pass_epa: float,
        away_pass_epa: float,
        home_rush_epa: float,
        away_rush_epa: float,
        home_plays: float,
        away_plays: float,
        kalman_margin: float,
        game_date: str,
    ) -> None:
        """Post-game Kalman measurement update with Joseph-form covariance stabilization."""
        h = self._get_or_create(home)
        a = self._get_or_create(away)

        # Apply time drift
        h.cov = self._drifted_cov(h, game_date)
        a.cov = self._drifted_cov(a, game_date)

        # Observation vector (3 elements):
        # y[0] = blended home margin observation (55% EPA-adj + 45% actual)
        # y[1] = home pass EPA
        # y[2] = home rush EPA
        y_obs = np.array([
            float(np.clip(kalman_margin, -24.0, 24.0)),
            float(np.clip(home_pass_epa - away_pass_epa, -0.5, 0.5)),
            float(np.clip(home_rush_epa - away_rush_epa, -0.3, 0.3)),
        ], dtype=float)

        # Joint state [O_h, D_h, P_h, R_h, Pace_h, O_a, D_a, P_a, R_a, Pace_a]
        theta_joint = np.concatenate([h.theta, a.theta])
        cov_joint = np.zeros((10, 10), dtype=float)
        cov_joint[:5, :5] = h.cov
        cov_joint[5:, 5:] = a.cov

        # Measurement matrix H (3 x 10)
        # y[0] = (O_h - D_a) + (D_h - O_a) ... simplified to margin = O_h - D_a + stuff
        # We use: margin ~ OffPts_h - DefPts_a - (OffPts_a - DefPts_h)
        #                 = OffPts_h - DefPts_a - OffPts_a + DefPts_h
        # y[1] = PassEPA_h - PassEPA_a
        # y[2] = RushEPA_h - RushEPA_a
        h_mat = np.zeros((3, 10), dtype=float)
        h_mat[0, 0] = 1.0   # OffPts_h
        h_mat[0, 1] = 1.0   # DefPts_h (home defense => helps h margin vs a offense)
        h_mat[0, 5] = -1.0  # OffPts_a
        h_mat[0, 6] = -1.0  # DefPts_a
        h_mat[1, 2] = 1.0   # PassEPA_h
        h_mat[1, 7] = -1.0  # PassEPA_a
        h_mat[2, 3] = 1.0   # RushEPA_h
        h_mat[2, 8] = -1.0  # RushEPA_a

        # Observation noise
        r_mat = np.diag([OBS_R_PTS, OBS_R_EPA, OBS_R_EPA])

        y_pred = h_mat @ theta_joint
        innovation = y_obs - y_pred

        s_mat = h_mat @ cov_joint @ h_mat.T + r_mat
        try:
            k_gain = cov_joint @ h_mat.T @ np.linalg.inv(s_mat)
        except np.linalg.LinAlgError:
            log.warning("Singular S matrix in NFL Kalman update for %s vs %s; skipping", home, away)
            return

        theta_updated = theta_joint + k_gain @ innovation

        i_kh = np.eye(10) - k_gain @ h_mat
        cov_updated = i_kh @ cov_joint @ i_kh.T + k_gain @ r_mat @ k_gain.T
        cov_updated = 0.5 * (cov_updated + cov_updated.T)

        h.theta = theta_updated[:5]
        h.cov = cov_updated[:5, :5]
        h.last_date = game_date
        h.games_played += 1

        a.theta = theta_updated[5:]
        a.cov = cov_updated[5:, 5:]
        a.last_date = game_date
        a.games_played += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hca_pts": self.hca_pts,
            "current_season": self.current_season,
            "teams": {k: v.to_dict() for k, v in self.teams.items()},
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KalmanNFLTracker":
        tracker = cls(hca_pts=d.get("hca_pts", DEFAULT_HCA_PTS))
        tracker.current_season = d.get("current_season")
        for k, v in d.get("teams", {}).items():
            tracker.teams[k] = NFLTeamState.from_dict(v)
        return tracker
