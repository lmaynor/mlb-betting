"""
nba.game -- Quantitative state-space and distributional NBA game-line prediction engine.

Provides institutional pricing for:
  * NBA Moneyline (NBA_ML)
  * NBA Point Spread (NBA_SPREAD)
  * NBA Game Total (NBA_TOTAL)
"""

from nba.game.features import FeatureStore, MatchupFeatures, ScheduleFatigue, TeamGameStats
from nba.game.kalman_ratings import KalmanGamePrediction, KalmanTeamTracker, TeamState
from nba.game.model import (
    DistributionalGameEngine,
    PricedGame,
    PricedMarket,
)
from nba.game.run_daily import get_nba_game_predictions, run_nba_game_predictions

__all__ = [
    "DistributionalGameEngine",
    "FeatureStore",
    "KalmanGamePrediction",
    "KalmanTeamTracker",
    "MatchupFeatures",
    "PricedGame",
    "PricedMarket",
    "ScheduleFatigue",
    "TeamGameStats",
    "TeamState",
    "get_nba_game_predictions",
    "run_nba_game_predictions",
]
