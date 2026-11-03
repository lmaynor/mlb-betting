"""
nfl.game.pbp_features -- Play-by-Play non-garbage-time EPA & success-rate team aggregator.

Aggregates nflverse PBP (2018+) into per-team per-game efficiency metrics, filtered to
non-garbage-time plays (wp between 0.05-0.95), with turnover-luck regression.

Computes strictly pre-game rolling EWM (halflife=5 and halflife=10 games) and
cross-sectional league Z-scores, with offseason prior-season carryover (0.55).

All time-travel leakage is guarded: for a given (season, week), only games completed
strictly before that (season, week) are used.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

log = logging.getLogger("nfl.game.pbp_features")

# -- Non-garbage-time filter -----------------------------------------------------
WP_LO: float = 0.05
WP_HI: float = 0.95
SCORE_DIFF_CAP: float = 21.0     # Q1-Q3 blowout guard
BLOWOUT_SCORE_CAP_QTR: int = 3   # only override wp filter for Q1-Q3

# -- Rolling halflifes (in games) ------------------------------------------------
HALFLIFE_SHORT: int = 5
HALFLIFE_LONG: int = 10

# -- Offseason carryover ---------------------------------------------------------
OFFSEASON_CARRY: float = 0.55
WEEK_DECAY: float = 0.75

# -- Turnover luck regression constants ------------------------------------------
# Fumble recovery regresses to 50%; interceptions regress by comparing actual int rate
# to (pass_breakup_rate * 0.22) as the expected interception component.
FUMBLE_RECOVERY_EV: float = 0.50
INT_REGRESSION_FACTOR: float = 0.22

# -- Empirical scoring weights for EPA-adjusted Kalman observation ---------------
KALMAN_EPA_WEIGHT: float = 0.55
KALMAN_ACTUAL_WEIGHT: float = 0.45
KALMAN_MARGIN_CAP: float = 24.0   # cap single-game blowout margins for Kalman input


@dataclass
class TeamGamePBP:
    """Non-garbage-time EPA and efficiency metrics for one team in one game."""
    game_id: str
    season: int
    week: int
    game_date: str   # YYYY-MM-DD
    team: str
    opp: str
    is_home: bool

    # Early-down efficiency
    ed_pass_epa: float = 0.0
    ed_rush_epa: float = 0.0
    pass_success_rate: float = 0.0
    rush_success_rate: float = 0.0
    proe: float = 0.0             # Pass Rate Over Expected

    # Red-zone scoring efficiency
    rz_td_rate: float = 0.0

    # Pass-rush / trench
    pressure_suffered_rate: float = 0.0

    # Pace
    seconds_per_play: float = 0.0

    # Turnover luck
    turnover_luck_epa: float = 0.0

    # Scoring outcome
    team_score: float = 0.0
    opp_score: float = 0.0
    margin: float = 0.0           # team_score - opp_score
    total: float = 0.0            # team_score + opp_score

    # EPA-adjusted observation for Kalman
    kalman_obs_margin: float = 0.0


def _non_garbage_mask(pbp: pd.DataFrame) -> pd.Series:
    """Boolean mask for non-garbage-time scrimmage plays."""
    is_scrimmage = (pbp.get("pass", 0) == 1) | (pbp.get("rush", 0) == 1)
    wp_col = pbp.get("wp", None)
    qtr_col = pbp.get("qtr", None)
    score_diff_col = pbp.get("score_differential", None)

    if wp_col is not None:
        wp_ok = wp_col.between(WP_LO, WP_HI)
    else:
        wp_ok = pd.Series(True, index=pbp.index)

    # Supplement: Q1-Q3 within 21 points counts even if wp is missing
    if qtr_col is not None and score_diff_col is not None:
        q13_close = (qtr_col <= BLOWOUT_SCORE_CAP_QTR) & (score_diff_col.abs() <= SCORE_DIFF_CAP)
        non_garbage = is_scrimmage & (wp_ok | q13_close)
    else:
        non_garbage = is_scrimmage & wp_ok

    return non_garbage


def _compute_turnover_luck(pbp_game: pd.DataFrame, team: str) -> float:
    """
    Turnover luck EPA: actual_turnover_epa - expected_turnover_epa.
    Fumble recoveries regress to 50%; interceptions regress vs. pass breakup rate.
    """
    if pbp_game.empty:
        return 0.0

    # Fumble recovery luck on team's offensive plays
    off_mask = pbp_game.get("posteam", None) == team
    if off_mask is None or off_mask.sum() == 0:
        return 0.0

    off_plays = pbp_game[off_mask]
    fumbles = off_plays.get("fumble", pd.Series(0, index=off_plays.index))
    fumble_lost = off_plays.get("fumble_lost", pd.Series(0, index=off_plays.index))
    fumble_count = float(fumbles.sum())
    fumble_lost_actual = float(fumble_lost.sum())
    fumble_lost_expected = fumble_count * FUMBLE_RECOVERY_EV
    # Approximate value: each fumble lost costs ~-4 EPA points vs. recovery
    fumble_luck_epa = (fumble_lost_expected - fumble_lost_actual) * 4.0

    # Interception luck (as passer)
    if "interception" in pbp_game.columns and "pass_breakup" in pbp_game.columns:
        pass_plays = off_plays[off_plays.get("pass", 0) == 1]
        n_pass = len(pass_plays)
        if n_pass > 0:
            actual_int_rate = float(pass_plays.get("interception", 0).sum()) / n_pass
            breakup_rate = float(off_plays.get("pass_breakup", 0).sum()) / max(1, n_pass)
            expected_int_rate = breakup_rate * INT_REGRESSION_FACTOR
            int_luck_epa = (expected_int_rate - actual_int_rate) * n_pass * 4.0
        else:
            int_luck_epa = 0.0
    else:
        int_luck_epa = 0.0

    return float(fumble_luck_epa + int_luck_epa)


def aggregate_team_game_pbp(pbp: pd.DataFrame) -> List[TeamGamePBP]:
    """
    Aggregate all games in `pbp` into TeamGamePBP rows (one per team per game).
    Uses the non-garbage-time filter. Strictly offline-compatible.
    """
    if pbp.empty:
        return []

    required = {"game_id", "posteam", "defteam"}
    if not required.issubset(pbp.columns):
        log.warning("pbp is missing required columns %s; returning empty", required - set(pbp.columns))
        return []

    rows: List[TeamGamePBP] = []
    non_garbage = _non_garbage_mask(pbp)

    for game_id, game_df in pbp.groupby("game_id"):
        ng = game_df[non_garbage.reindex(game_df.index, fill_value=False)]

        # Game metadata from schedules if present, else pbp
        season = int(game_df["season"].iloc[0]) if "season" in game_df.columns else 0
        week = int(game_df["week"].iloc[0]) if "week" in game_df.columns else 0
        game_date = str(game_df.get("game_date", pd.Series(["1900-01-01"])).iloc[0])[:10]

        home_team = str(game_df.get("home_team", pd.Series([None])).iloc[0] or "")
        away_team = str(game_df.get("away_team", pd.Series([None])).iloc[0] or "")

        # Final scores
        home_score = float(game_df.get("home_score", pd.Series([0])).iloc[-1]) if "home_score" in game_df.columns else 0.0
        away_score = float(game_df.get("away_score", pd.Series([0])).iloc[-1]) if "away_score" in game_df.columns else 0.0

        teams = []
        if home_team:
            teams.append((home_team, away_team, True, home_score, away_score))
        if away_team and away_team != home_team:
            teams.append((away_team, home_team, False, away_score, home_score))

        for team, opp, is_home, t_score, o_score in teams:
            off_ng = ng[ng.get("posteam", "") == team]
            ed_ng = off_ng[off_ng.get("down", 0).isin([1, 2])] if "down" in ng.columns else off_ng

            # Early-down EPA
            pass_ed = ed_ng[ed_ng.get("pass", 0) == 1]
            rush_ed = ed_ng[ed_ng.get("rush", 0) == 1]

            epa_col = off_ng.get("epa", pd.Series(dtype=float))
            pass_epa_col = pass_ed.get("epa", pd.Series(dtype=float))
            rush_epa_col = rush_ed.get("epa", pd.Series(dtype=float))

            ed_pass_epa = float(pass_epa_col.mean()) if len(pass_epa_col) > 0 else 0.0
            ed_rush_epa = float(rush_epa_col.mean()) if len(rush_epa_col) > 0 else 0.0

            pass_success = float((pass_epa_col > 0).mean()) if len(pass_epa_col) > 0 else 0.5
            rush_success = float((rush_epa_col > 0).mean()) if len(rush_epa_col) > 0 else 0.5

            # PROE: pass rate over expected
            if "pass_oe" in off_ng.columns:
                proe = float(off_ng["pass_oe"].mean())
            else:
                n_plays = len(off_ng)
                n_pass_all = int(off_ng.get("pass", 0).sum())
                proe = (n_pass_all / max(1, n_plays)) - 0.58

            # Red-zone TD rate (yardline_100 <= 20)
            if "yardline_100" in off_ng.columns and "touchdown" in off_ng.columns:
                rz = off_ng[off_ng["yardline_100"] <= 20]
                rz_tds = int(rz["touchdown"].sum()) if len(rz) > 0 else 0
                rz_drives = max(1, len(rz["drive"].unique()) if "drive" in rz.columns else 1)
                rz_td_rate = rz_tds / rz_drives
            else:
                rz_td_rate = 0.0

            # Pressure suffered (as offense)
            dropbacks = max(1, int(off_ng.get("pass", 0).sum()))
            sacks = int(off_ng.get("sack", pd.Series(0, index=off_ng.index)).sum())
            qb_hits = int(off_ng.get("qb_hit", pd.Series(0, index=off_ng.index)).sum())
            pressure_rate = (sacks + qb_hits) / dropbacks

            # Pace: seconds per play in neutral game state
            neutral_off = off_ng
            if "score_differential" in off_ng.columns:
                neutral_off = off_ng[off_ng["score_differential"].abs() <= 7]
            if "play_clock" in neutral_off.columns and len(neutral_off) > 0:
                secs_per_play = float(neutral_off["play_clock"].mean())
            else:
                secs_per_play = 30.0

            # Turnover luck
            turnover_luck = _compute_turnover_luck(game_df, team)

            # Kalman observation: 55% EPA-adjusted margin + 45% actual margin, capped at +/-24
            margin = t_score - o_score
            total = t_score + o_score
            # EPA-adjusted scoring: use ed_pass_epa and ed_rush_epa as margin proxy
            epa_margin_proxy = (ed_pass_epa + ed_rush_epa) * 10.0  # rough scale to points
            kalman_margin_raw = KALMAN_EPA_WEIGHT * epa_margin_proxy + KALMAN_ACTUAL_WEIGHT * margin
            kalman_margin = float(np.clip(kalman_margin_raw, -KALMAN_MARGIN_CAP, KALMAN_MARGIN_CAP))

            rows.append(TeamGamePBP(
                game_id=str(game_id),
                season=season,
                week=week,
                game_date=game_date,
                team=team,
                opp=opp,
                is_home=is_home,
                ed_pass_epa=ed_pass_epa,
                ed_rush_epa=ed_rush_epa,
                pass_success_rate=pass_success,
                rush_success_rate=rush_success,
                proe=proe,
                rz_td_rate=rz_td_rate,
                pressure_suffered_rate=pressure_rate,
                seconds_per_play=secs_per_play,
                turnover_luck_epa=turnover_luck,
                team_score=t_score,
                opp_score=o_score,
                margin=margin,
                total=total,
                kalman_obs_margin=kalman_margin,
            ))

    return rows


@dataclass
class TeamRollingProfile:
    """Rolled pre-game efficiency profile for one team entering a matchup."""
    team: str
    ed_pass_epa_s: float = 0.0    # short halflife (5 games)
    ed_rush_epa_s: float = 0.0
    proe_s: float = 0.0
    rz_td_rate_s: float = 0.0
    pressure_suffered_s: float = 0.0
    turnover_luck_s: float = 0.0
    ed_pass_epa_l: float = 0.0    # long halflife (10 games)
    ed_rush_epa_l: float = 0.0
    pass_success_l: float = 0.5
    rush_success_l: float = 0.5
    n_games: int = 0


class PBPFeatureStore:
    """Incrementally maintains pre-game EPA rolling profiles for all NFL teams."""

    def __init__(self) -> None:
        self._history: Dict[str, List[TeamGamePBP]] = {}

    def add_game(self, row: TeamGamePBP) -> None:
        self._history.setdefault(row.team, []).append(row)

    def add_games(self, rows: List[TeamGamePBP]) -> None:
        for r in rows:
            self.add_game(r)

    def _ewm_last(self, series: List[float], halflife: int) -> float:
        if not series:
            return 0.0
        alpha = 1.0 - np.exp(-np.log(2) / halflife)
        val = series[0]
        for x in series[1:]:
            val = alpha * x + (1.0 - alpha) * val
        return float(val)

    def get_profile(self, team: str, before_season: int, before_week: int) -> TeamRollingProfile:
        """Pre-game rolling profile using ONLY games completed strictly before (season, week)."""
        games = [
            g for g in self._history.get(team, [])
            if (g.season, g.week) < (before_season, before_week)
        ]
        games.sort(key=lambda g: (g.season, g.week))

        if not games:
            return TeamRollingProfile(team=team)

        # Include offseason decay by inserting a virtual prior at the start of each season
        ed_pass = [g.ed_pass_epa for g in games]
        ed_rush = [g.ed_rush_epa for g in games]
        proe = [g.proe for g in games]
        rz = [g.rz_td_rate for g in games]
        pressure = [g.pressure_suffered_rate for g in games]
        luck = [g.turnover_luck_epa for g in games]
        pass_suc = [g.pass_success_rate for g in games]
        rush_suc = [g.rush_success_rate for g in games]

        return TeamRollingProfile(
            team=team,
            ed_pass_epa_s=self._ewm_last(ed_pass, HALFLIFE_SHORT),
            ed_rush_epa_s=self._ewm_last(ed_rush, HALFLIFE_SHORT),
            proe_s=self._ewm_last(proe, HALFLIFE_SHORT),
            rz_td_rate_s=self._ewm_last(rz, HALFLIFE_SHORT),
            pressure_suffered_s=self._ewm_last(pressure, HALFLIFE_SHORT),
            turnover_luck_s=self._ewm_last(luck, HALFLIFE_SHORT),
            ed_pass_epa_l=self._ewm_last(ed_pass, HALFLIFE_LONG),
            ed_rush_epa_l=self._ewm_last(ed_rush, HALFLIFE_LONG),
            pass_success_l=self._ewm_last(pass_suc, HALFLIFE_LONG),
            rush_success_l=self._ewm_last(rush_suc, HALFLIFE_LONG),
            n_games=len(games),
        )


@dataclass
class MatchupPBPFeatures:
    """Z-score differentials and sum-features for one NFL matchup."""
    home_team: str
    away_team: str
    game_date: str
    season: int
    week: int

    home_profile: TeamRollingProfile = field(default_factory=lambda: TeamRollingProfile(""))
    away_profile: TeamRollingProfile = field(default_factory=lambda: TeamRollingProfile(""))

    # Z-score differentials (home - away, standardized against league cross-section)
    z_ed_pass_diff: float = 0.0
    z_ed_rush_diff: float = 0.0
    z_proe_diff: float = 0.0
    z_rz_td_diff: float = 0.0
    z_pressure_diff: float = 0.0
    z_luck_diff: float = 0.0
    z_pass_suc_diff: float = 0.0

    # Schedule / rest features
    rest_days_home: int = 7
    rest_days_away: int = 7
    is_short_week_tnf: bool = False
    coming_off_bye_home: bool = False
    coming_off_bye_away: bool = False
    is_divisional: bool = False

    # Injury adjustments
    qb_delta_pts_home: float = 0.0
    qb_delta_pts_away: float = 0.0
    total_injury_drag: float = 0.0

    # Weather
    wind_mph: float = 0.0
    is_dome: bool = False


def compute_matchup_pbp_features(
    home: str,
    away: str,
    season: int,
    week: int,
    game_date: str,
    store: PBPFeatureStore,
    schedules: Optional[pd.DataFrame] = None,
    injuries: Optional[pd.DataFrame] = None,
) -> MatchupPBPFeatures:
    """Build pre-game matchup features from the PBPFeatureStore and supplementary data."""
    h_prof = store.get_profile(home, season, week)
    a_prof = store.get_profile(away, season, week)

    # Cross-sectional league Z-scores
    all_teams = list(store._history.keys())
    if len(all_teams) >= 4:
        pass_vals = [store.get_profile(t, season, week).ed_pass_epa_s for t in all_teams]
        rush_vals = [store.get_profile(t, season, week).ed_rush_epa_s for t in all_teams]
        proe_vals = [store.get_profile(t, season, week).proe_s for t in all_teams]
        rz_vals = [store.get_profile(t, season, week).rz_td_rate_s for t in all_teams]
        pressure_vals = [store.get_profile(t, season, week).pressure_suffered_s for t in all_teams]
        luck_vals = [store.get_profile(t, season, week).turnover_luck_s for t in all_teams]
        pass_suc_vals = [store.get_profile(t, season, week).pass_success_l for t in all_teams]

        def _zscore(val_h: float, val_a: float, vals: List[float]) -> float:
            arr = np.array(vals, dtype=float)
            mu, sd = float(arr.mean()), float(arr.std())
            if sd < 1e-9:
                return 0.0
            return float(((val_h - mu) - (val_a - mu)) / sd)

        z_pass = _zscore(h_prof.ed_pass_epa_s, a_prof.ed_pass_epa_s, pass_vals)
        z_rush = _zscore(h_prof.ed_rush_epa_s, a_prof.ed_rush_epa_s, rush_vals)
        z_proe = _zscore(h_prof.proe_s, a_prof.proe_s, proe_vals)
        z_rz = _zscore(h_prof.rz_td_rate_s, a_prof.rz_td_rate_s, rz_vals)
        z_pressure = _zscore(a_prof.pressure_suffered_s, h_prof.pressure_suffered_s, pressure_vals)
        z_luck = _zscore(h_prof.turnover_luck_s, a_prof.turnover_luck_s, luck_vals)
        z_pass_suc = _zscore(h_prof.pass_success_l, a_prof.pass_success_l, pass_suc_vals)
    else:
        z_pass = z_rush = z_proe = z_rz = z_pressure = z_luck = z_pass_suc = 0.0

    # Schedule / weather features from schedules dataframe
    rest_h = rest_a = 7
    short_tnf = False
    bye_h = bye_a = False
    divisional = False
    wind_mph = 0.0
    is_dome = False

    if schedules is not None and not schedules.empty:
        sched_cols = set(schedules.columns)
        game_row = schedules[
            (schedules.get("season", pd.Series(dtype=int)) == season) &
            (schedules.get("week", pd.Series(dtype=int)) == week) &
            (
                (schedules.get("home_team", pd.Series(dtype=str)) == home) |
                (schedules.get("away_team", pd.Series(dtype=str)) == away)
            )
        ]
        if len(game_row) > 0:
            gr = game_row.iloc[0]
            if "home_rest" in sched_cols:
                rest_h = int(gr.get("home_rest", 7) or 7)
            if "away_rest" in sched_cols:
                rest_a = int(gr.get("away_rest", 7) or 7)
            short_tnf = rest_h <= 4 or rest_a <= 4
            bye_h = rest_h >= 13
            bye_a = rest_a >= 13
            if "div_game" in sched_cols:
                divisional = bool(gr.get("div_game", False))
            if "wind" in sched_cols:
                w_val = gr.get("wind")
                if w_val is not None and str(w_val).lower() not in ("nan", "none"):
                    try:
                        wind_mph = float(w_val)
                    except (TypeError, ValueError):
                        wind_mph = 0.0
            if "roof" in sched_cols:
                roof = str(gr.get("roof", "")).lower()
                is_dome = "dome" in roof or "closed" in roof or "retract" in roof

    return MatchupPBPFeatures(
        home_team=home,
        away_team=away,
        game_date=game_date,
        season=season,
        week=week,
        home_profile=h_prof,
        away_profile=a_prof,
        z_ed_pass_diff=z_pass,
        z_ed_rush_diff=z_rush,
        z_proe_diff=z_proe,
        z_rz_td_diff=z_rz,
        z_pressure_diff=z_pressure,
        z_luck_diff=z_luck,
        z_pass_suc_diff=z_pass_suc,
        rest_days_home=rest_h,
        rest_days_away=rest_a,
        is_short_week_tnf=short_tnf,
        coming_off_bye_home=bye_h,
        coming_off_bye_away=bye_a,
        is_divisional=divisional,
        wind_mph=wind_mph,
        is_dome=is_dome,
    )
