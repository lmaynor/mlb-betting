"""
nba.game.evaluate -- Expanding-window walk-forward historical evaluator.

Evaluates Moneyline, Spread, and Total predictions strictly out-of-sample:
  * Team ratings and feature stores update only after predictions are recorded for date t.
  * Reports ML Brier score, log loss, win accuracy, and Expected Calibration Error (ECE).
  * Reports Spread and Total MAE, RMSE, and against-the-spread (ATS) / over-under hit rates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from nba.game.features import FeatureStore, TeamGameStats
from nba.game.kalman_ratings import KalmanTeamTracker
from nba.game.model import DistributionalGameEngine

log = logging.getLogger("nba.game.evaluate")


@dataclass
class EvaluationRecord:
    game_id: str
    game_date: str
    home_team: str
    away_team: str
    pred_margin: float
    pred_total: float
    pred_p_home_ml: float
    actual_home_pts: float
    actual_away_pts: float
    actual_margin: float
    actual_total: float
    actual_home_win: int
    closing_spread: Optional[float] = None
    closing_total: Optional[float] = None
    pred_home_cover: Optional[float] = None
    pred_over: Optional[float] = None


@dataclass
class EvaluationReport:
    n_games: int
    ml_accuracy: float
    ml_brier_score: float
    ml_log_loss: float
    ml_ece: float
    margin_mae: float
    margin_rmse: float
    total_mae: float
    total_rmse: float
    ats_accuracy: Optional[float] = None
    totals_accuracy: Optional[float] = None
    records: List[EvaluationRecord] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"NBA Game Engine Walk-Forward Evaluation ({self.n_games} games)",
            "=" * 60,
            f"Moneyline Accuracy   : {self.ml_accuracy:.2%}",
            f"Moneyline Brier Score: {self.ml_brier_score:.4f}",
            f"Moneyline Log Loss   : {self.ml_log_loss:.4f}",
            f"Moneyline ECE        : {self.ml_ece:.4f}",
            f"Margin MAE           : {self.margin_mae:.2f} pts",
            f"Margin RMSE          : {self.margin_rmse:.2f} pts",
            f"Total MAE            : {self.total_mae:.2f} pts",
            f"Total RMSE           : {self.total_rmse:.2f} pts",
        ]
        if self.ats_accuracy is not None:
            lines.append(f"ATS Win Rate         : {self.ats_accuracy:.2%}")
        if self.totals_accuracy is not None:
            lines.append(f"Totals Win Rate      : {self.totals_accuracy:.2%}")
        return "\n".join(lines)


def evaluate_walk_forward(
    games: List[TeamGameStats],
    closing_odds: Optional[Dict[str, Dict[str, float]]] = None,
    warmup_games: int = 50,
) -> EvaluationReport:
    """
    Run expanding-window walk-forward evaluation on a chronological list of games.
    """
    if len(games) <= warmup_games:
        raise ValueError(f"Need > {warmup_games} games for evaluation; got {len(games)}")

    closing_odds = closing_odds or {}

    # Sort strictly chronologically
    sorted_games = sorted(games, key=lambda g: (g.game_date, g.game_id))

    # Group paired rows into distinct games: (game_id, home_stat, away_stat)
    paired: Dict[str, Tuple[Optional[TeamGameStats], Optional[TeamGameStats]]] = {}
    for g in sorted_games:
        entry = paired.setdefault(g.game_id, (None, None))
        if g.is_home:
            paired[g.game_id] = (g, entry[1])
        else:
            paired[g.game_id] = (entry[0], g)

    game_list: List[Tuple[TeamGameStats, TeamGameStats]] = []
    for gid, (h_stat, a_stat) in paired.items():
        if h_stat is not None and a_stat is not None:
            game_list.append((h_stat, a_stat))

    game_list.sort(key=lambda pair: (pair[0].game_date, pair[0].game_id))

    kalman = KalmanTeamTracker()
    feature_store = FeatureStore()
    engine = DistributionalGameEngine()

    records: List[EvaluationRecord] = []

    # Warmup phase
    for i, (h, a) in enumerate(game_list):
        if i < warmup_games:
            # Update trackers without recording predictions
            kalman.update_game(
                h.team, a.team, h.points, a.points, h.possessions, h.minutes, h.game_date
            )
            feature_store.add_games([h, a])
            continue

        # Out-of-sample prediction before game update
        k_pred = kalman.predict_game(h.team, a.team, h.game_date)
        m_feat = feature_store.extract_matchup_features(h.team, a.team, h.game_date)

        # Look up optional closing market lines
        odds_for_g = closing_odds.get(h.game_id, {})
        spread_line = odds_for_g.get("spread")
        total_line = odds_for_g.get("total")

        market_dict: Dict[str, Any] = {}
        if spread_line is not None:
            market_dict["spreads"] = {"line": spread_line}
        if total_line is not None:
            market_dict["totals"] = {"line": total_line}

        priced = engine.price_full_game(
            f"nba:{h.game_date}:{a.team}@{h.team}",
            k_pred,
            m_feat,
            market_odds=market_dict,
        )

        # Actual outcomes
        act_margin = float(h.points - a.points)
        act_total = float(h.points + a.points)
        act_home_win = 1 if act_margin > 0 else 0

        # Retrieve ML prob
        ml_home = next(m for m in priced.markets if m.system == "NBA_ML" and m.side == "home")
        pred_p_home = ml_home.p_model

        # Spread cover prob
        pred_sp_cover = None
        if spread_line is not None:
            sp_m = next((m for m in priced.markets if m.system == "NBA_SPREAD" and m.side == "home"), None)
            if sp_m:
                pred_sp_cover = sp_m.p_model

        # Over prob
        pred_over = None
        if total_line is not None:
            tot_m = next((m for m in priced.markets if m.system == "NBA_TOTAL" and m.side == "over"), None)
            if tot_m:
                pred_over = tot_m.p_model

        rec = EvaluationRecord(
            game_id=h.game_id,
            game_date=h.game_date,
            home_team=h.team,
            away_team=a.team,
            pred_margin=priced.mu_margin,
            pred_total=priced.mu_total,
            pred_p_home_ml=pred_p_home,
            actual_home_pts=h.points,
            actual_away_pts=a.points,
            actual_margin=act_margin,
            actual_total=act_total,
            actual_home_win=act_home_win,
            closing_spread=spread_line,
            closing_total=total_line,
            pred_home_cover=pred_sp_cover,
            pred_over=pred_over,
        )
        records.append(rec)

        # Post-game update
        kalman.update_game(
            h.team, a.team, h.points, a.points, h.possessions, h.minutes, h.game_date
        )
        feature_store.add_games([h, a])

    # Compute metrics across records
    n_eval = len(records)
    if n_eval == 0:
        raise ValueError("No records evaluated")

    p_preds = np.array([r.pred_p_home_ml for r in records])
    y_actuals = np.array([r.actual_home_win for r in records])

    # ML Accuracy (predict home win if p >= 0.5)
    ml_acc = float(np.mean((p_preds >= 0.5) == y_actuals))

    # Brier score
    brier = float(np.mean((p_preds - y_actuals) ** 2))

    # Log loss (clipped to avoid log(0))
    p_clipped = np.clip(p_preds, 1e-6, 1.0 - 1e-6)
    log_loss = float(-np.mean(y_actuals * np.log(p_clipped) + (1 - y_actuals) * np.log(1 - p_clipped)))

    # ECE (Expected Calibration Error across 10 deciles)
    bins = np.linspace(0.0, 1.0, 11)
    bin_indices = np.digitize(p_preds, bins) - 1
    ece = 0.0
    for b in range(10):
        mask = bin_indices == b
        if np.sum(mask) > 0:
            bin_acc = np.mean(y_actuals[mask])
            bin_conf = np.mean(p_preds[mask])
            ece += (np.sum(mask) / n_eval) * abs(bin_acc - bin_conf)

    # Margin errors
    pred_margins = np.array([r.pred_margin for r in records])
    act_margins = np.array([r.actual_margin for r in records])
    margin_mae = float(np.mean(np.abs(pred_margins - act_margins)))
    margin_rmse = float(np.sqrt(np.mean((pred_margins - act_margins) ** 2)))

    # Total errors
    pred_totals = np.array([r.pred_total for r in records])
    act_totals = np.array([r.actual_total for r in records])
    total_mae = float(np.mean(np.abs(pred_totals - act_totals)))
    total_rmse = float(np.sqrt(np.mean((pred_totals - act_totals) ** 2)))

    # ATS Accuracy if closing spreads exist
    ats_games = [r for r in records if r.closing_spread is not None]
    ats_acc = None
    if ats_games:
        ats_wins = 0
        ats_valid = 0
        for r in ats_games:
            line = r.closing_spread
            if r.actual_margin == -line:
                continue  # push
            home_covered = r.actual_margin > -line
            pred_home = r.pred_margin > -line
            if pred_home == home_covered:
                ats_wins += 1
            ats_valid += 1
        if ats_valid > 0:
            ats_acc = float(ats_wins / ats_valid)

    # Totals Accuracy if closing totals exist
    tot_games = [r for r in records if r.closing_total is not None]
    totals_acc = None
    if tot_games:
        tot_wins = 0
        tot_valid = 0
        for r in tot_games:
            line = r.closing_total
            if r.actual_total == line:
                continue
            act_over = r.actual_total > line
            pred_over = r.pred_total > line
            if pred_over == act_over:
                tot_wins += 1
            tot_valid += 1
        if tot_valid > 0:
            totals_acc = float(tot_wins / tot_valid)

    return EvaluationReport(
        n_games=n_eval,
        ml_accuracy=ml_acc,
        ml_brier_score=brier,
        ml_log_loss=log_loss,
        ml_ece=float(ece),
        margin_mae=margin_mae,
        margin_rmse=margin_rmse,
        total_mae=total_mae,
        total_rmse=total_rmse,
        ats_accuracy=ats_acc,
        totals_accuracy=totals_acc,
        records=records,
    )
