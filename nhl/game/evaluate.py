"""
nhl.game.evaluate -- Expanding-window walk-forward historical evaluator for NHL game lines.

Evaluates Moneyline, Puck Line, and Game Totals strictly out-of-sample:
  * Team ratings, Goalie GSAx, and feature stores update only after predictions are recorded for date t.
  * Reports ML Brier score, log loss, win accuracy, and Expected Calibration Error (ECE).
  * Reports Puck Line and Game Totals hit rates, MAE, and RMSE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from nhl.game.features import FeatureStore, TeamGameStats
from nhl.game.goalie_model import GoalieTracker
from nhl.game.kalman_ratings import KalmanTeamTracker
from nhl.game.matrix_pricer import DistributionalNHLMatrixEngine

log = logging.getLogger("nhl.game.evaluate")


@dataclass
class NHLEvaluationRecord:
    game_id: str
    game_date: str
    home_team: str
    away_team: str
    lambda_h: float
    lambda_a: float
    pred_exp_total: float
    pred_p_home_ml: float
    actual_home_goals: float
    actual_away_goals: float
    actual_total: float
    actual_home_win: int
    closing_puck_line: float = -1.5
    closing_total: float = 6.0
    pred_home_cover: Optional[float] = None
    pred_over: Optional[float] = None
    actual_home_covered: Optional[int] = None
    actual_over: Optional[int] = None


@dataclass
class NHLEvaluationReport:
    n_games: int
    ml_accuracy: float
    ml_brier_score: float
    ml_log_loss: float
    ml_ece: float
    total_mae: float
    total_rmse: float
    puck_line_accuracy: Optional[float] = None
    totals_accuracy: Optional[float] = None
    records: List[NHLEvaluationRecord] = field(default_factory=list)

    def summary(self) -> str:
        pl_str = f"{self.puck_line_accuracy:.3f}" if self.puck_line_accuracy is not None else "N/A"
        tot_str = f"{self.totals_accuracy:.3f}" if self.totals_accuracy is not None else "N/A"
        return (
            f"NHL Game Evaluation Report (N={self.n_games} games)\n"
            f"  Moneyline: Accuracy={self.ml_accuracy:.3f}, Brier={self.ml_brier_score:.4f}, "
            f"LogLoss={self.ml_log_loss:.4f}, ECE={self.ml_ece:.4f}\n"
            f"  Puck Line (-1.5/+1.5): Accuracy={pl_str}\n"
            f"  Totals: Accuracy={tot_str}, MAE={self.total_mae:.2f}, RMSE={self.total_rmse:.2f}"
        )


def compute_ece(probs: np.ndarray, outcomes: np.ndarray, n_bins: int = 10) -> float:
    """Expected Calibration Error across n_bins equal-width buckets."""
    if len(probs) == 0:
        return 0.0
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    n_total = len(probs)
    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        mask = (probs >= lo) & (probs < hi) if i < n_bins - 1 else (probs >= lo) & (probs <= hi)
        if np.any(mask):
            bin_acc = float(np.mean(outcomes[mask]))
            bin_conf = float(np.mean(probs[mask]))
            bin_weight = float(np.sum(mask)) / float(n_total)
            ece += bin_weight * abs(bin_acc - bin_conf)
    return float(ece)


class WalkForwardNHLEvaluator:
    """Walk-forward expanding window historical backtester for NHL game lines."""

    def __init__(
        self,
        kalman_tracker: Optional[KalmanTeamTracker] = None,
        feature_store: Optional[FeatureStore] = None,
        goalie_tracker: Optional[GoalieTracker] = None,
        matrix_engine: Optional[DistributionalNHLMatrixEngine] = None,
    ) -> None:
        self.kalman = kalman_tracker or KalmanTeamTracker()
        self.features = feature_store or FeatureStore()
        self.goalies = goalie_tracker or GoalieTracker()
        self.engine = matrix_engine or DistributionalNHLMatrixEngine()
        self.records: List[NHLEvaluationRecord] = []

    def evaluate_game(
        self,
        home: str,
        away: str,
        game_date: str,
        game_id: str,
        actual_home_goals: float,
        actual_away_goals: float,
        actual_home_sog: float = 30.0,
        actual_away_sog: float = 30.0,
        closing_puck_line: float = -1.5,
        closing_total: float = 6.0,
    ) -> NHLEvaluationRecord:
        """
        Record prediction strictly pre-game, then execute post-game update.
        """
        # 1. Pre-game prediction
        kalman_pred = self.kalman.predict_game(home, away, game_date=game_date)

        h_proj = self.goalies.project_team_goalie(home, game_date)
        a_proj = self.goalies.project_team_goalie(away, game_date)

        matchup = self.features.build_matchup_features(
            home=home,
            away=away,
            game_date=game_date,
            home_goalie_gsax_60=h_proj.projected_gsax_60,
            away_goalie_gsax_60=a_proj.projected_gsax_60,
        )

        lh, la = self.engine.compute_scoring_rates(kalman_pred, matchup)
        matrix = self.engine.compute_score_matrix(lh, la)

        pred_p_home_ml = matrix.p_home_ml
        pred_total = lh + la

        # Puck Line (-1.5)
        p_h_cov, p_a_cov, _ = matrix.puck_line(closing_puck_line)

        # Total
        p_over_raw, p_under_raw, p_push, p_over_fair = matrix.total(closing_total)

        # Actual outcomes
        actual_home_win = 1 if actual_home_goals > actual_away_goals else 0
        actual_total = actual_home_goals + actual_away_goals

        # Cover check: (home_goals - away_goals) + line > 0
        margin = (actual_home_goals - actual_away_goals) + closing_puck_line
        actual_cover = 1 if margin > 1e-9 else (0 if margin < -1e-9 else None)

        actual_over = 1 if actual_total > closing_total + 1e-9 else (0 if actual_total < closing_total - 1e-9 else None)

        record = NHLEvaluationRecord(
            game_id=game_id,
            game_date=str(game_date)[:10],
            home_team=home,
            away_team=away,
            lambda_h=lh,
            lambda_a=la,
            pred_exp_total=pred_total,
            pred_p_home_ml=pred_p_home_ml,
            actual_home_goals=actual_home_goals,
            actual_away_goals=actual_away_goals,
            actual_total=actual_total,
            actual_home_win=actual_home_win,
            closing_puck_line=closing_puck_line,
            closing_total=closing_total,
            pred_home_cover=p_h_cov,
            pred_over=p_over_fair,
            actual_home_covered=actual_cover,
            actual_over=actual_over,
        )
        self.records.append(record)

        # 2. Post-game updates
        self.kalman.update_game(
            home=home,
            away=away,
            home_goals=actual_home_goals,
            away_goals=actual_away_goals,
            home_sog=actual_home_sog,
            away_sog=actual_away_sog,
            game_date=game_date,
        )

        stat_h = TeamGameStats(
            game_id=game_id,
            game_date=str(game_date)[:10],
            season_year=2026,
            team=home,
            opp=away,
            is_home=True,
            goals=actual_home_goals,
            goals_against=actual_away_goals,
            minutes=60.0,
            sog=actual_home_sog,
            shots_against=actual_away_sog,
            gf_60=actual_home_goals,
            ga_60=actual_away_goals,
            sog_for_60=actual_home_sog,
            sog_against_60=actual_away_sog,
            sog_share=actual_home_sog / (actual_home_sog + actual_away_sog),
            xgf_60=actual_home_goals,
            xga_60=actual_away_goals,
            xg_share=actual_home_goals / (actual_home_goals + actual_away_goals),
            sh_pct_luck=0.0,
            pp_goals_per_game=0.6,
            pp_share=0.2,
            pk_goals_against_per_game=0.6,
            pim_per_60=8.0,
            blocks_per_60=14.0,
            hits_per_60=20.0,
            takeaway_diff_per_60=0.0,
            fo_win_pct=0.5,
        )
        self.features.add_game_stat(stat_h)

        return record

    def summarize(self) -> NHLEvaluationReport:
        """Compile comprehensive metrics across all evaluated records."""
        if not self.records:
            return NHLEvaluationReport(
                n_games=0,
                ml_accuracy=0.0,
                ml_brier_score=0.0,
                ml_log_loss=0.0,
                ml_ece=0.0,
                total_mae=0.0,
                total_rmse=0.0,
                puck_line_accuracy=None,
                totals_accuracy=None,
                records=[],
            )

        n = len(self.records)
        pred_probs = np.array([r.pred_p_home_ml for r in self.records], dtype=float)
        actual_wins = np.array([r.actual_home_win for r in self.records], dtype=float)

        # ML metrics
        ml_preds = (pred_probs >= 0.5).astype(int)
        ml_acc = float(np.mean(ml_preds == actual_wins))
        brier = float(np.mean((pred_probs - actual_wins) ** 2))
        eps = 1e-15
        p_clipped = np.clip(pred_probs, eps, 1.0 - eps)
        log_loss = float(-np.mean(actual_wins * np.log(p_clipped) + (1.0 - actual_wins) * np.log(1.0 - p_clipped)))
        ece = compute_ece(pred_probs, actual_wins)

        # Total MAE and RMSE
        pred_totals = np.array([r.pred_exp_total for r in self.records], dtype=float)
        actual_totals = np.array([r.actual_total for r in self.records], dtype=float)
        total_mae = float(np.mean(np.abs(pred_totals - actual_totals)))
        total_rmse = float(np.sqrt(np.mean((pred_totals - actual_totals) ** 2)))

        # Puck Line ATS
        pl_records = [r for r in self.records if r.actual_home_covered is not None and r.pred_home_cover is not None]
        pl_acc = None
        if pl_records:
            pl_preds = np.array([1 if r.pred_home_cover >= 0.5 else 0 for r in pl_records])
            pl_actual = np.array([r.actual_home_covered for r in pl_records])
            pl_acc = float(np.mean(pl_preds == pl_actual))

        # Totals OU
        ou_records = [r for r in self.records if r.actual_over is not None and r.pred_over is not None]
        tot_acc = None
        if ou_records:
            ou_preds = np.array([1 if r.pred_over >= 0.5 else 0 for r in ou_records])
            ou_actual = np.array([r.actual_over for r in ou_records])
            tot_acc = float(np.mean(ou_preds == ou_actual))

        return NHLEvaluationReport(
            n_games=n,
            ml_accuracy=ml_acc,
            ml_brier_score=brier,
            ml_log_loss=log_loss,
            ml_ece=ece,
            total_mae=total_mae,
            total_rmse=total_rmse,
            puck_line_accuracy=pl_acc,
            totals_accuracy=tot_acc,
            records=list(self.records),
        )
