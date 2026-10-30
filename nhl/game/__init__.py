"""
nhl.game -- Quantitative state-space and Dixon-Coles + ENG matrix NHL game-line prediction engine.

Provides institutional pricing for:
  * NHL Moneyline (NHL_ML)
  * NHL Puck Line / Spread (NHL_SPREAD, -1.5 / +1.5)
  * NHL Game Total (NHL_TOTAL, 5.5 / 6.0 / 6.5)
"""

from nhl.game.features import FeatureStore, MatchupFeatures, ScheduleFatigue, TeamGameStats
from nhl.game.goalie_model import GoalieTracker, TeamGoalieProjection
from nhl.game.kalman_ratings import KalmanGamePrediction, KalmanTeamTracker, TeamState
from nhl.game.matrix_pricer import (
    DistributionalNHLMatrixEngine,
    PricedGame,
    PricedMarket,
    ScoreMatrixResult,
)
from nhl.game.run_daily import get_nhl_game_predictions, run_nhl_game_predictions

__all__ = [
    "DistributionalNHLMatrixEngine",
    "FeatureStore",
    "GoalieTracker",
    "KalmanGamePrediction",
    "KalmanTeamTracker",
    "MatchupFeatures",
    "PricedGame",
    "PricedMarket",
    "ScheduleFatigue",
    "ScoreMatrixResult",
    "TeamGameStats",
    "TeamGoalieProjection",
    "TeamState",
    "get_nhl_game_predictions",
    "run_nhl_game_predictions",
]
