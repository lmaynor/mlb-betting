"""
nhl.game.features -- Possession, special-teams, and shot-share metrics,
cross-sectional league Z-scores, schedule fatigue tensor, and roster injury/absence deltas.

Inspired by gschwaeb/NHL_Game_Prediction, extended to rolling EWM10 / EWM25,
prior-season shrinkage (0.75^games_played), and strict zero-leakage enforcement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from nhl.config import TEAMS, team_abbrev

log = logging.getLogger("nhl.game.features")

LEAGUE_BASELINES: Dict[str, float] = {
    "gf_60": 2.85,
    "ga_60": 2.85,
    "sog_for_60": 30.5,
    "sog_against_60": 30.5,
    "sog_share": 0.500,
    "xgf_60": 2.85,
    "xga_60": 2.85,
    "xg_share": 0.500,
    "sh_pct_luck": 0.0,
    "pp_goals_per_game": 0.60,
    "pp_share": 0.200,
    "pk_goals_against_per_game": 0.60,
    "pim_per_60": 8.0,
    "blocks_per_60": 14.0,
    "hits_per_60": 20.0,
    "takeaway_diff_per_60": 0.0,
    "fo_win_pct": 0.500,
}

FEATURE_METRICS = list(LEAGUE_BASELINES.keys())


@dataclass
class TeamGameStats:
    game_id: str
    game_date: str
    season_year: int
    team: str
    opp: str
    is_home: bool
    goals: float
    goals_against: float
    minutes: float
    sog: float
    shots_against: float
    gf_60: float
    ga_60: float
    sog_for_60: float
    sog_against_60: float
    sog_share: float
    xgf_60: float
    xga_60: float
    xg_share: float
    sh_pct_luck: float
    pp_goals_per_game: float
    pp_share: float
    pk_goals_against_per_game: float
    pim_per_60: float
    blocks_per_60: float
    hits_per_60: float
    takeaway_diff_per_60: float
    fo_win_pct: float


@dataclass
class ScheduleFatigue:
    home_rest_days: int
    away_rest_days: int
    rest_diff: int
    is_b2b_home: bool
    is_b2b_road: bool
    is_3_in_4_home: bool
    is_3_in_4_away: bool
    is_4_in_6_home: bool
    is_4_in_6_away: bool
    road_b2b_penalty: float = 0.0


@dataclass
class MatchupFeatures:
    home_team: str
    away_team: str
    game_date: str
    home_metrics_ewm10: Dict[str, float]
    away_metrics_ewm10: Dict[str, float]
    home_metrics_ewm25: Dict[str, float]
    away_metrics_ewm25: Dict[str, float]
    z_diff: Dict[str, float]
    z_sum: Dict[str, float]
    fatigue: ScheduleFatigue
    home_goalie_gsax_60: float = 0.0
    away_goalie_gsax_60: float = 0.0
    home_injury_drag: float = 0.0
    away_injury_drag: float = 0.0


def compute_xg_proxy(ev_sog: float, pp_sog: float) -> float:
    """Empirical shot- and power-play-weighted expected goals proxy."""
    return float(0.088 * max(0.0, ev_sog) + 0.145 * max(0.0, pp_sog))


def build_team_game_stats(
    game_id: str,
    game_date: str,
    season_year: int,
    team: str,
    opp: str,
    is_home: bool,
    goals: float,
    goals_against: float,
    sog: float,
    shots_against: float,
    minutes: float = 60.0,
    pp_goals: float = 0.0,
    opp_pp_goals: float = 0.0,
    pim: float = 8.0,
    blocks: float = 14.0,
    hits: float = 20.0,
    takeaways: float = 5.0,
    giveaways: float = 5.0,
    fo_wins: float = 25.0,
    fo_losses: float = 25.0,
    ev_sog: Optional[float] = None,
    pp_sog: Optional[float] = None,
    opp_ev_sog: Optional[float] = None,
    opp_pp_sog: Optional[float] = None,
) -> TeamGameStats:
    """Calculate per-60, possession, and special-teams rate stats for one team in a game."""
    mins = max(50.0, float(minutes))
    scale_60 = 60.0 / mins

    gf_60 = float(goals) * scale_60
    ga_60 = float(goals_against) * scale_60
    sog_for_60 = float(sog) * scale_60
    sog_against_60 = float(shots_against) * scale_60

    tot_sog = sog_for_60 + sog_against_60
    sog_share = (sog_for_60 / tot_sog) if tot_sog > 0 else 0.500

    # xG proxy
    if ev_sog is None or pp_sog is None:
        # Default split: ~85% EV, ~15% PP
        ev_sog_val = sog * 0.85
        pp_sog_val = sog * 0.15
    else:
        ev_sog_val = ev_sog
        pp_sog_val = pp_sog

    if opp_ev_sog is None or opp_pp_sog is None:
        opp_ev_sog_val = shots_against * 0.85
        opp_pp_sog_val = shots_against * 0.15
    else:
        opp_ev_sog_val = opp_ev_sog
        opp_pp_sog_val = opp_pp_sog

    xgf = compute_xg_proxy(ev_sog_val, pp_sog_val)
    xga = compute_xg_proxy(opp_ev_sog_val, opp_pp_sog_val)

    xgf_60 = xgf * scale_60
    xga_60 = xga * scale_60
    tot_xg = xgf_60 + xga_60
    xg_share = (xgf_60 / tot_xg) if tot_xg > 0 else 0.500

    sh_pct_luck = gf_60 - xgf_60

    tot_fo = fo_wins + fo_losses
    fo_win_pct = (fo_wins / tot_fo) if tot_fo > 0 else 0.500

    takeaway_diff_60 = (float(takeaways) - float(giveaways)) * scale_60

    return TeamGameStats(
        game_id=str(game_id),
        game_date=str(game_date)[:10],
        season_year=int(season_year),
        team=team_abbrev(team) or str(team).upper(),
        opp=team_abbrev(opp) or str(opp).upper(),
        is_home=bool(is_home),
        goals=float(goals),
        goals_against=float(goals_against),
        minutes=mins,
        sog=float(sog),
        shots_against=float(shots_against),
        gf_60=gf_60,
        ga_60=ga_60,
        sog_for_60=sog_for_60,
        sog_against_60=sog_against_60,
        sog_share=sog_share,
        xgf_60=xgf_60,
        xga_60=xga_60,
        xg_share=xg_share,
        sh_pct_luck=sh_pct_luck,
        pp_goals_per_game=float(pp_goals),
        pp_share=float(pp_goals) / max(1.0, float(goals)) if goals > 0 else 0.200,
        pk_goals_against_per_game=float(opp_pp_goals),
        pim_per_60=float(pim) * scale_60,
        blocks_per_60=float(blocks) * scale_60,
        hits_per_60=float(hits) * scale_60,
        takeaway_diff_per_60=takeaway_diff_60,
        fo_win_pct=fo_win_pct,
    )


def compute_schedule_fatigue(
    team_history: List[TeamGameStats],
    is_home: bool,
    game_date: str,
) -> Tuple[int, bool, bool, bool]:
    """Calculate rest days, B2B status, 3-in-4, and 4-in-6 nights strictly from prior games."""
    date_str = str(game_date)[:10]
    prior = [g for g in team_history if g.game_date < date_str]
    if not prior:
        return 3, False, False, False

    try:
        curr_d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return 3, False, False, False

    dates: List[datetime.date] = []
    for g in prior:
        try:
            dates.append(datetime.strptime(str(g.game_date)[:10], "%Y-%m-%d").date())
        except Exception:
            continue

    if not dates:
        return 3, False, False, False

    dates.sort()
    last_d = dates[-1]
    rest_days = max(0, (curr_d - last_d).days - 1)
    is_b2b = rest_days == 0

    # 3 in 4: 2 games played in the 3 days preceding today
    d_3_ago = curr_d - timedelta(days=3)
    games_last_3_days = sum(1 for d in dates if d_3_ago <= d < curr_d)
    is_3_in_4 = games_last_3_days >= 2

    # 4 in 6: 3 games played in the 5 days preceding today
    d_5_ago = curr_d - timedelta(days=5)
    games_last_5_days = sum(1 for d in dates if d_5_ago <= d < curr_d)
    is_4_in_6 = games_last_5_days >= 3

    return min(rest_days, 7), is_b2b, is_3_in_4, is_4_in_6


class FeatureStore:
    """Historical team feature store with zero-leakage pre-game rolling metrics."""

    def __init__(self) -> None:
        self.team_games: Dict[str, List[TeamGameStats]] = {}

    def add_game_stat(self, stat: TeamGameStats) -> None:
        self.team_games.setdefault(stat.team, []).append(stat)

    def get_team_games_before(self, team: str, before_date: str) -> List[TeamGameStats]:
        t_code = team_abbrev(team) or str(team).upper()
        games = self.team_games.get(t_code, [])
        date_str = str(before_date)[:10]
        return [g for g in games if g.game_date < date_str]

    def compute_team_profile(
        self,
        team: str,
        before_date: str,
        halflife: float = 10.0,
    ) -> Dict[str, float]:
        """Compute rolling EWM features with prior-season Bayesian shrinkage."""
        t_code = team_abbrev(team) or str(team).upper()
        games = self.get_team_games_before(t_code, before_date)
        if not games:
            return dict(LEAGUE_BASELINES)

        # Sort chronologically
        games = sorted(games, key=lambda g: g.game_date)
        n = len(games)
        weights = np.array([0.5 ** ((n - 1 - i) / halflife) for i in range(n)], dtype=float)
        w_sum = float(np.sum(weights))

        profile: Dict[str, float] = {}
        for m in FEATURE_METRICS:
            vals = np.array([getattr(g, m) for g in games], dtype=float)
            ewm_val = float(np.sum(weights * vals) / w_sum) if w_sum > 0 else LEAGUE_BASELINES[m]

            # Prior-season shrinkage during first 12 games of a season
            shrinkage = 0.75 ** min(n, 12)
            profile[m] = float((1.0 - shrinkage) * ewm_val + shrinkage * LEAGUE_BASELINES[m])

        return profile

    def compute_league_distributions(
        self,
        before_date: str,
        halflife: float = 10.0,
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        """Calculate cross-sectional mean and std across all 32 teams prior to before_date."""
        profiles: List[Dict[str, float]] = []
        for code, *_ in TEAMS:
            prof = self.compute_team_profile(code, before_date, halflife=halflife)
            profiles.append(prof)

        means: Dict[str, float] = {}
        stds: Dict[str, float] = {}
        for m in FEATURE_METRICS:
            vals = [p[m] for p in profiles]
            means[m] = float(np.mean(vals))
            stds[m] = float(max(1e-4, np.std(vals)))

        return means, stds

    def build_matchup_features(
        self,
        home: str,
        away: str,
        game_date: str,
        home_goalie_gsax_60: float = 0.0,
        away_goalie_gsax_60: float = 0.0,
        home_injury_drag: float = 0.0,
        away_injury_drag: float = 0.0,
    ) -> MatchupFeatures:
        """Synthesize all pre-game matchup features, Z-score differentials, and schedule tensor."""
        h_code = team_abbrev(home) or str(home).upper()
        a_code = team_abbrev(away) or str(away).upper()
        date_str = str(game_date)[:10]

        h_ewm10 = self.compute_team_profile(h_code, date_str, halflife=10.0)
        a_ewm10 = self.compute_team_profile(a_code, date_str, halflife=10.0)

        h_ewm25 = self.compute_team_profile(h_code, date_str, halflife=25.0)
        a_ewm25 = self.compute_team_profile(a_code, date_str, halflife=25.0)

        lg_means, lg_stds = self.compute_league_distributions(date_str, halflife=10.0)

        z_diff: Dict[str, float] = {}
        z_sum: Dict[str, float] = {}
        for m in FEATURE_METRICS:
            z_h = (h_ewm10[m] - lg_means[m]) / lg_stds[m]
            z_a = (a_ewm10[m] - lg_means[m]) / lg_stds[m]
            z_diff[m] = float(z_h - z_a)
            z_sum[m] = float(z_h + z_a)

        # Fatigue tensor
        h_history = self.team_games.get(h_code, [])
        a_history = self.team_games.get(a_code, [])

        h_rest, h_b2b, h_3_4, h_4_6 = compute_schedule_fatigue(h_history, is_home=True, game_date=date_str)
        a_rest, a_b2b, a_3_4, a_4_6 = compute_schedule_fatigue(a_history, is_home=False, game_date=date_str)

        # Road B2B disadvantage
        road_b2b_penalty = -0.18 if a_b2b else 0.0

        fatigue = ScheduleFatigue(
            home_rest_days=h_rest,
            away_rest_days=a_rest,
            rest_diff=h_rest - a_rest,
            is_b2b_home=h_b2b,
            is_b2b_road=a_b2b,
            is_3_in_4_home=h_3_4,
            is_3_in_4_away=a_3_4,
            is_4_in_6_home=h_4_6,
            is_4_in_6_away=a_4_6,
            road_b2b_penalty=road_b2b_penalty,
        )

        return MatchupFeatures(
            home_team=h_code,
            away_team=a_code,
            game_date=date_str,
            home_metrics_ewm10=h_ewm10,
            away_metrics_ewm10=a_ewm10,
            home_metrics_ewm25=h_ewm25,
            away_metrics_ewm25=a_ewm25,
            z_diff=z_diff,
            z_sum=z_sum,
            fatigue=fatigue,
            home_goalie_gsax_60=home_goalie_gsax_60,
            away_goalie_gsax_60=away_goalie_gsax_60,
            home_injury_drag=home_injury_drag,
            away_injury_drag=away_injury_drag,
        )


def build_team_game_stats_from_raw(df_teams: pd.DataFrame) -> List[TeamGameStats]:
    """Parse team_boxscores_master or flattened boxscores into TeamGameStats."""
    if df_teams is None or df_teams.empty:
        return []

    stats: List[TeamGameStats] = []
    df = df_teams.copy()
    if "date" in df.columns:
        df["date_str"] = df["date"].astype(str).str[:10]
        df = df.sort_values("date_str")

    for _, row in df.iterrows():
        try:
            stat = build_team_game_stats(
                game_id=str(row.get("game_id", "")),
                game_date=str(row.get("date_str") or row.get("date") or "")[:10],
                season_year=int(row.get("season_year", 2026)),
                team=str(row.get("team_abbr") or row.get("team") or ""),
                opp=str(row.get("opp_abbr") or row.get("opp") or ""),
                is_home=bool(row.get("is_home", True)),
                goals=float(row.get("goals", 0.0) or 0.0),
                goals_against=float(row.get("goals_against", 0.0) or 0.0),
                sog=float(row.get("sog", 30.0) or 30.0),
                shots_against=float(row.get("shots_against", 30.0) or 30.0),
                minutes=float(row.get("minutes", 60.0) or 60.0),
                pp_goals=float(row.get("power_play_goals", 0.0) or 0.0),
                pim=float(row.get("pim", 8.0) or 8.0),
                blocks=float(row.get("blocked_shots", 14.0) or 14.0),
                hits=float(row.get("hits", 20.0) or 20.0),
                takeaways=float(row.get("takeaways", 5.0) or 5.0),
                giveaways=float(row.get("giveaways", 5.0) or 5.0),
            )
            stats.append(stat)
        except Exception:
            continue

    return stats
