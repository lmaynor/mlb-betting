"""
nhl.game.goalie_model -- Starting Goaltender Empirical-Bayes GSAx/60 and B2B Backup Model.

Tracks goaltender starts chronologically from NHL/goalie_boxscores_master.csv.
Computes:
  1. Trailing EWM (halflife = 18 starts) shots faced per 60 (SA_60) and goals allowed per 60 (GA_60).
  2. raw_GSAx_60 = (1.0 - baseline_sv_pct) * SA_60 - GA_60 (where baseline_sv_pct ~ 0.903).
  3. Empirical Bayes shrinkage toward 0.0:
     shrunk_GSAx_60 = (total_minutes / (total_minutes + 1200)) * raw_GSAx_60
  4. Starter selection and B2B backup inference:
     - Confirmed starter -> that goalie's shrunk GSAx
     - Unconfirmed + 2nd leg of B2B (rest_days == 0 and #1 Starter played yesterday) ->
       0.80 * Backup + 0.20 * Starter, plus -0.18 goal skater fatigue penalty.
     - Unconfirmed regular -> 0.75 * Starter + 0.25 * Backup.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from nhl.config import team_abbrev

log = logging.getLogger("nhl.game.goalie_model")

LEAGUE_BASELINE_SV_PCT: float = 0.903
DEFAULT_SA_60: float = 30.5
DEFAULT_GA_60: float = 2.95
K_GOALIE_MINUTES: float = 1200.0  # ~20 starts
HALFLIFE_STARTS: float = 18.0
B2B_SKATER_FATIGUE_PENALTY: float = 0.18  # goals against / fatigue drag


@dataclass
class GoalieStart:
    date: str
    game_id: str
    team: str
    toi_minutes: float
    shots_against: float
    goals_against: float
    sa_60: float
    ga_60: float
    xg_against: Optional[float] = None


@dataclass
class GoalieProfile:
    goalie_id: str
    name: str
    starts: List[GoalieStart] = field(default_factory=list)
    total_minutes: float = 0.0

    def add_start(self, start: GoalieStart) -> None:
        self.starts.append(start)
        self.total_minutes += max(0.0, start.toi_minutes)

    def get_gsax_60(self, before_date: Optional[str] = None) -> float:
        """Compute shrunk GSAx/60 strictly before the specified game date."""
        starts = self.starts
        if before_date:
            starts = [s for s in starts if s.date < str(before_date)[:10]]
        if not starts:
            return 0.0

        n = len(starts)
        weights = np.array([0.5 ** ((n - 1 - i) / HALFLIFE_STARTS) for i in range(n)], dtype=float)
        w_sum = float(np.sum(weights))
        if w_sum <= 0:
            return 0.0

        sa_vals = np.array([s.sa_60 for s in starts], dtype=float)
        ga_vals = np.array([s.ga_60 for s in starts], dtype=float)

        ewm_sa_60 = float(np.sum(weights * sa_vals) / w_sum)
        ewm_ga_60 = float(np.sum(weights * ga_vals) / w_sum)

        raw_gsax_60 = (1.0 - LEAGUE_BASELINE_SV_PCT) * ewm_sa_60 - ewm_ga_60

        total_mins = sum(s.toi_minutes for s in starts)
        shrinkage = total_mins / (total_mins + K_GOALIE_MINUTES)
        return float(shrinkage * raw_gsax_60)


@dataclass
class TeamGoalieProjection:
    team: str
    projected_gsax_60: float
    starter_name: Optional[str]
    starter_id: Optional[str]
    starter_gsax_60: float
    backup_name: Optional[str]
    backup_id: Optional[str]
    backup_gsax_60: float
    is_confirmed: bool
    is_b2b_backup: bool
    fatigue_penalty: float


class GoalieTracker:
    """Manages historical goalie starts, Empirical Bayes GSAx, and starter inference."""

    def __init__(self, baseline_sv_pct: float = LEAGUE_BASELINE_SV_PCT) -> None:
        self.baseline_sv_pct = float(baseline_sv_pct)
        self.goalies: Dict[str, GoalieProfile] = {}
        # team -> list of (date, game_id, goalie_id, goalie_name)
        self.team_starts: Dict[str, List[Tuple[str, str, str, str]]] = {}

    def record_start(
        self,
        goalie_id: str,
        name: str,
        team: str,
        date: str,
        game_id: str,
        toi_minutes: float,
        shots_against: float,
        goals_against: float,
        xg_against: Optional[float] = None,
    ) -> None:
        """Record an appearance / start chronologically."""
        gid = str(goalie_id)
        t_code = team_abbrev(team) or str(team).upper()

        if toi_minutes <= 0:
            return

        sa_60 = (float(shots_against) / toi_minutes) * 60.0
        ga_60 = (float(goals_against) / toi_minutes) * 60.0

        start = GoalieStart(
            date=str(date)[:10],
            game_id=str(game_id),
            team=t_code,
            toi_minutes=float(toi_minutes),
            shots_against=float(shots_against),
            goals_against=float(goals_against),
            sa_60=sa_60,
            ga_60=ga_60,
            xg_against=xg_against,
        )

        if gid not in self.goalies:
            self.goalies[gid] = GoalieProfile(goalie_id=gid, name=name)
        self.goalies[gid].add_start(start)

        # Record under team if substantial time played (start threshold >= 20 min)
        if toi_minutes >= 20.0:
            self.team_starts.setdefault(t_code, []).append((str(date)[:10], str(game_id), gid, name))

    def get_goalie_gsax(self, goalie_id_or_name: str, before_date: Optional[str] = None) -> float:
        """Retrieve shrunk GSAx/60 for a goaltender prior to before_date."""
        gid = str(goalie_id_or_name)
        if gid in self.goalies:
            return self.goalies[gid].get_gsax_60(before_date)

        # Fallback search by name
        norm_name = gid.lower().strip()
        for prof in self.goalies.values():
            if prof.name.lower().strip() == norm_name:
                return prof.get_gsax_60(before_date)

        return 0.0

    def get_team_goalies(
        self,
        team: str,
        before_date: Optional[str] = None,
        last_n_games: int = 10,
    ) -> Tuple[Optional[Tuple[str, str]], Optional[Tuple[str, str]]]:
        """
        Identify #1 Starter and #2 Backup based on start counts over trailing N games.
        Returns: ((starter_id, starter_name), (backup_id, backup_name))
        """
        t_code = team_abbrev(team) or str(team).upper()
        starts = self.team_starts.get(t_code, [])
        if before_date:
            starts = [s for s in starts if s[0] < str(before_date)[:10]]

        if not starts:
            return None, None

        recent = starts[-last_n_games:]
        counts: Dict[str, int] = {}
        names: Dict[str, str] = {}
        for _, _, gid, name in recent:
            counts[gid] = counts.get(gid, 0) + 1
            names[gid] = name

        sorted_goalies = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        starter = (sorted_goalies[0][0], names[sorted_goalies[0][0]]) if sorted_goalies else None
        backup = (sorted_goalies[1][0], names[sorted_goalies[1][0]]) if len(sorted_goalies) > 1 else None

        return starter, backup

    def project_team_goalie(
        self,
        team: str,
        game_date: str,
        confirmed_starter_id: Optional[str] = None,
        confirmed_starter_name: Optional[str] = None,
        rest_days: Optional[int] = None,
    ) -> TeamGoalieProjection:
        """
        Produce projected goalie GSAx and starter breakdown for a team on game_date.
        Handles confirmed starter override or B2B backup inference.
        """
        t_code = team_abbrev(team) or str(team).upper()
        date_str = str(game_date)[:10]

        # Check confirmed starter
        if confirmed_starter_id or confirmed_starter_name:
            gid = confirmed_starter_id or confirmed_starter_name or ""
            gsax = self.get_goalie_gsax(gid, before_date=date_str)
            name = confirmed_starter_name or (self.goalies[gid].name if gid in self.goalies else gid)
            return TeamGoalieProjection(
                team=t_code,
                projected_gsax_60=gsax,
                starter_name=name,
                starter_id=confirmed_starter_id,
                starter_gsax_60=gsax,
                backup_name=None,
                backup_id=None,
                backup_gsax_60=0.0,
                is_confirmed=True,
                is_b2b_backup=False,
                fatigue_penalty=0.0,
            )

        # Unconfirmed: look up team starter and backup
        starter_pair, backup_pair = self.get_team_goalies(t_code, before_date=date_str)
        s_id, s_name = starter_pair if starter_pair else (None, None)
        b_id, b_name = backup_pair if backup_pair else (None, None)

        s_gsax = self.get_goalie_gsax(s_id, before_date=date_str) if s_id else 0.0
        b_gsax = self.get_goalie_gsax(b_id, before_date=date_str) if b_id else 0.0

        # Check if team is on B2B (rest_days == 0) and starter started yesterday
        starts = [s for s in self.team_starts.get(t_code, []) if s[0] < date_str]
        last_starter_id = starts[-1][2] if starts else None

        is_b2b = (rest_days == 0) if rest_days is not None else False
        if not is_b2b and starts:
            try:
                d_curr = datetime.strptime(date_str, "%Y-%m-%d").date()
                d_last = datetime.strptime(starts[-1][0], "%Y-%m-%d").date()
                is_b2b = (d_curr - d_last).days == 1
            except Exception:
                is_b2b = False

        is_b2b_backup = False
        fatigue_penalty = 0.0

        if is_b2b and s_id and last_starter_id == s_id:
            # Starter played yesterday: high probability of backup
            is_b2b_backup = True
            fatigue_penalty = B2B_SKATER_FATIGUE_PENALTY
            proj_gsax = 0.80 * b_gsax + 0.20 * s_gsax
        else:
            # Standard blend: 75% starter, 25% backup
            proj_gsax = 0.75 * s_gsax + 0.25 * b_gsax

        return TeamGoalieProjection(
            team=t_code,
            projected_gsax_60=proj_gsax,
            starter_name=s_name,
            starter_id=s_id,
            starter_gsax_60=s_gsax,
            backup_name=b_name,
            backup_id=b_id,
            backup_gsax_60=b_gsax,
            is_confirmed=False,
            is_b2b_backup=is_b2b_backup,
            fatigue_penalty=fatigue_penalty,
        )


def build_goalie_tracker_from_df(df_goalies: pd.DataFrame) -> GoalieTracker:
    """Build GoalieTracker from goalie_boxscores_master DataFrame."""
    tracker = GoalieTracker()
    if df_goalies is None or df_goalies.empty:
        return tracker

    df = df_goalies.copy()
    if "date" in df.columns:
        df["date_str"] = df["date"].astype(str).str[:10]
        df = df.sort_values("date_str")

    for _, row in df.iterrows():
        try:
            pid = str(row.get("player_id", ""))
            name = str(row.get("name") or pid)
            team = str(row.get("team_abbr") or row.get("team") or "")
            date = str(row.get("date_str") or row.get("date") or "")[:10]
            gid = str(row.get("game_id", ""))

            toi_sec = row.get("toi_sec")
            if toi_sec is not None and not math.isnan(float(toi_sec)):
                toi_min = float(toi_sec) / 60.0
            else:
                toi_min = float(row.get("toi_minutes", 0.0))

            sa = float(row.get("shots_against", 0.0) or 0.0)
            ga = float(row.get("goals_against", 0.0) or 0.0)

            if toi_min > 0:
                tracker.record_start(
                    goalie_id=pid,
                    name=name,
                    team=team,
                    date=date,
                    game_id=gid,
                    toi_minutes=toi_min,
                    shots_against=sa,
                    goals_against=ga,
                )
        except Exception:
            continue

    return tracker
