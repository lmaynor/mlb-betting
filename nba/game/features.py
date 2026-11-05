"""
nba.game.features -- Pace-neutral per-100 possession metrics, Four Factors,
cross-sectional league Z-score differentials, schedule fatigue tensor, and active roster injury deltas.

All feature calculations enforce strict zero-leakage: for any game date t, only data from
strictly prior to t is used (prior-date filtering / shift(1)).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from bisect import insort
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from nba.teams import TEAMS, team_code

log = logging.getLogger("nba.game.features")

LEAGUE_BASELINES: Dict[str, float] = {
    "off_rtg": 114.5,
    "def_rtg": 114.5,
    "net_rtg": 0.0,
    "pace": 99.0,
    "efg_pct": 0.545,
    "opp_efg_pct": 0.545,
    "tov_pct": 0.135,
    "opp_tov_pct": 0.135,
    "oreb_pct": 0.270,
    "dreb_pct": 0.730,
    "ftr": 0.245,
    "opp_ftr": 0.245,
    "ts_pct": 0.580,
    "threepar": 0.395,
}

FEATURE_METRICS = list(LEAGUE_BASELINES.keys())

SHRINK_GAMES = 15            # games into a season at which a profile stops shrinking toward the league baseline
SEASON_CARRYOVER = 0.5       # weight of the prior-season profile on a team with no/few games this season


def _season_of(date_str: Any) -> int:
    """NBA season (start year) of a calendar date: Aug-Dec -> that year, Jan-Jul -> year - 1."""
    d = str(date_str)[:10]
    try:
        y, m = int(d[:4]), int(d[5:7])
    except ValueError:
        return -1
    return y if m >= 8 else y - 1


def calc_possessions(
    fga: float,
    oreb: float,
    tov: float,
    fta: float,
) -> float:
    """Standard NBA possession formula: FGA - OREB + TOV + 0.44 * FTA."""
    return float(fga - oreb + tov + 0.44 * fta)


@dataclass
class TeamGameStats:
    game_id: str
    game_date: str
    season_year: int
    team: str
    opp: str
    is_home: bool
    points: float
    opp_points: float
    minutes: float
    possessions: float
    off_rtg: float
    def_rtg: float
    net_rtg: float
    pace: float
    efg_pct: float
    opp_efg_pct: float
    tov_pct: float
    opp_tov_pct: float
    oreb_pct: float
    dreb_pct: float
    ftr: float
    opp_ftr: float
    ts_pct: float
    threepar: float


@dataclass
class ScheduleFatigue:
    rest_home: int
    rest_away: int
    rest_diff: int
    b2b_home: bool
    b2b_away: bool
    b2b_road_disadvantage: bool
    three_in_four_home: bool
    three_in_four_away: bool
    four_in_six_home: bool
    four_in_six_away: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rest_home": self.rest_home,
            "rest_away": self.rest_away,
            "rest_diff": self.rest_diff,
            "b2b_home": int(self.b2b_home),
            "b2b_away": int(self.b2b_away),
            "b2b_road_disadvantage": int(self.b2b_road_disadvantage),
            "three_in_four_home": int(self.three_in_four_home),
            "three_in_four_away": int(self.three_in_four_away),
            "four_in_six_home": int(self.four_in_six_home),
            "four_in_six_away": int(self.four_in_six_away),
        }


@dataclass
class MatchupFeatures:
    home_team: str
    away_team: str
    game_date: str
    # Z-score differentials (Home - Away)
    z_net_rtg_diff: float
    z_off_def_matchup: float
    z_efg_diff: float
    z_tov_diff: float
    z_oreb_diff: float
    z_ftr_diff: float
    z_ts_diff: float
    # Totals sums (Home + Away)
    z_pace_sum: float
    z_off_sum: float
    # Three-point attempt rate combined
    combined_3par: float
    # Raw pace expected
    raw_pace_est: float
    # Fatigue tensor
    fatigue: ScheduleFatigue
    # Injury deltas
    injury_margin_delta: float
    injury_total_delta: float
    home_points_lost: float
    away_points_lost: float

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "home_team": self.home_team,
            "away_team": self.away_team,
            "game_date": self.game_date,
            "z_net_rtg_diff": self.z_net_rtg_diff,
            "z_off_def_matchup": self.z_off_def_matchup,
            "z_efg_diff": self.z_efg_diff,
            "z_tov_diff": self.z_tov_diff,
            "z_oreb_diff": self.z_oreb_diff,
            "z_ftr_diff": self.z_ftr_diff,
            "z_ts_diff": self.z_ts_diff,
            "z_pace_sum": self.z_pace_sum,
            "z_off_sum": self.z_off_sum,
            "combined_3par": self.combined_3par,
            "raw_pace_est": self.raw_pace_est,
            "injury_margin_delta": self.injury_margin_delta,
            "injury_total_delta": self.injury_total_delta,
            "home_points_lost": self.home_points_lost,
            "away_points_lost": self.away_points_lost,
        }
        d.update(self.fatigue.to_dict())
        return d


class FeatureStore:
    """Historical team boxscore index with strict zero-leakage rolling calculations."""

    def __init__(self, team_games: Optional[List[TeamGameStats]] = None) -> None:
        self.games: List[TeamGameStats] = list(team_games or [])
        # Index games by team and sort chronologically
        self._by_team: Dict[str, List[TeamGameStats]] = {}
        self._reindex()

    @staticmethod
    def _key(g: TeamGameStats) -> Tuple[str, str]:
        return (g.game_date, g.game_id)

    def _reindex(self) -> None:
        self._by_team.clear()
        for g in self.games:
            self._by_team.setdefault(g.team, []).append(g)
        for t in self._by_team:
            self._by_team[t].sort(key=self._key)

    def add_games(self, new_games: List[TeamGameStats]) -> None:
        """Incremental insert: the usual chronological replay appends in O(1); out-of-order games bisect in
        (the old implementation re-sorted every team's whole history per added game: O(N^2) in a replay)."""
        for g in new_games:
            self.games.append(g)
            lst = self._by_team.setdefault(g.team, [])
            if not lst or self._key(lst[-1]) <= self._key(g):
                lst.append(g)
            else:
                insort(lst, g, key=self._key)

    def get_prior_games(self, team: str, before_date: str) -> List[TeamGameStats]:
        """Strictly prior-to-date games for a team."""
        code = team_code(team)
        if not code or code not in self._by_team:
            return []
        dt_thresh = str(before_date)[:10]
        return [g for g in self._by_team[code] if str(g.game_date)[:10] < dt_thresh]

    def compute_team_profile(
        self,
        team: str,
        before_date: str,
    ) -> Dict[str, float]:
        """
        Compute blended multi-horizon profile (EWM12 + L10 + STD) for a team before date.
        If sample size is small (< 15 games), shrinks toward league baseline.
        """
        prior = self.get_prior_games(team, before_date)
        if not prior:
            return dict(LEAGUE_BASELINES)

        n = len(prior)
        metrics: Dict[str, float] = {}

        cur_season = _season_of(before_date)
        n_season = sum(1 for g in prior if _season_of(g.game_date) == cur_season)
        w_eff = min(1.0, n_season / float(SHRINK_GAMES))
        if n_season < n and w_eff < 1.0:
            w_eff = w_eff + (1.0 - w_eff) * SEASON_CARRYOVER

        # Last 10 games
        l10_slice = prior[-10:]

        # EWM weights: span=12, alpha = 2/(12+1) = 2/13
        alpha = 2.0 / 13.0
        weights = [(1.0 - alpha) ** i for i in range(n)][::-1]
        sum_w = sum(weights)

        for m in FEATURE_METRICS:
            vals = [getattr(g, m) for g in prior]
            std_val = float(np.mean(vals))
            l10_val = float(np.mean([getattr(g, m) for g in l10_slice]))
            ewm_val = float(sum(v * w for v, w in zip(vals, weights)) / max(1e-6, sum_w))

            # Blended estimate
            blended = 0.50 * ewm_val + 0.30 * l10_val + 0.20 * std_val

            # Bayesian shrinkage keyed on games THIS season (career n never shrinks a season opener). Prior-season
            # games still carry over with weight SEASON_CARRYOVER, the rest regresses to the league baseline.
            if w_eff < 1.0:
                blended = w_eff * blended + (1.0 - w_eff) * LEAGUE_BASELINES[m]

            metrics[m] = float(blended)

        return metrics

    def compute_league_z_scores(
        self,
        before_date: str,
    ) -> Dict[str, Dict[str, float]]:
        """
        Compute cross-sectional Z-score for all teams on each metric prior to date.
        Returns: {team_code: {metric: z_score}}
        """
        profiles: Dict[str, Dict[str, float]] = {}
        for code, *_ in TEAMS:
            profiles[code] = self.compute_team_profile(code, before_date)

        z_scores: Dict[str, Dict[str, float]] = {c: {} for c in profiles}

        for m in FEATURE_METRICS:
            vals = [profiles[c][m] for c in profiles]
            mean_v = float(np.mean(vals))
            std_v = float(np.std(vals))
            if std_v < 1e-4:
                std_v = 1.0

            for c in profiles:
                z_scores[c][m] = float((profiles[c][m] - mean_v) / std_v)

        return z_scores

    def compute_schedule_fatigue(
        self,
        home: str,
        away: str,
        game_date: str,
    ) -> ScheduleFatigue:
        """
        Calculates rest days, back-to-back status, 3-in-4, and 4-in-6 nights.
        """
        h_code = team_code(home)
        a_code = team_code(away)
        h_games = self.get_prior_games(h_code, game_date) if h_code else []
        a_games = self.get_prior_games(a_code, game_date) if a_code else []

        try:
            curr_d = datetime.strptime(str(game_date)[:10], "%Y-%m-%d").date()
        except Exception:
            curr_d = datetime.now().date()

        def analyze_team(games: List[TeamGameStats]) -> Tuple[int, bool, bool, bool]:
            if not games:
                return 3, False, False, False  # well rested default
            try:
                last_d = datetime.strptime(str(games[-1].game_date)[:10], "%Y-%m-%d").date()
                rest = max(1, min(7, (curr_d - last_d).days))
            except Exception:
                rest = 2
            b2b = rest == 1

            # Count games in last 4 days and last 6 days
            d_4_ago = curr_d - timedelta(days=4)
            d_6_ago = curr_d - timedelta(days=6)
            recent_4 = 0
            recent_6 = 0
            for g in games:
                try:
                    gd = datetime.strptime(str(g.game_date)[:10], "%Y-%m-%d").date()
                    if gd >= d_4_ago:
                        recent_4 += 1
                    if gd >= d_6_ago:
                        recent_6 += 1
                except Exception:
                    pass

            three_in_four = recent_4 >= 2 and b2b
            four_in_six = recent_6 >= 3 and b2b
            return rest, b2b, three_in_four, four_in_six

        h_rest, h_b2b, h_3in4, h_4in6 = analyze_team(h_games)
        a_rest, a_b2b, a_3in4, a_4in6 = analyze_team(a_games)

        # Away team playing back-to-back on the road has strong fatigue penalty
        b2b_road = bool(a_b2b)

        return ScheduleFatigue(
            rest_home=h_rest,
            rest_away=a_rest,
            rest_diff=h_rest - a_rest,
            b2b_home=h_b2b,
            b2b_away=a_b2b,
            b2b_road_disadvantage=b2b_road,
            three_in_four_home=h_3in4,
            three_in_four_away=a_3in4,
            four_in_six_home=h_4in6,
            four_in_six_away=a_4in6,
        )

    def extract_matchup_features(
        self,
        home: str,
        away: str,
        game_date: str,
        injury_report: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> MatchupFeatures:
        """
        Build complete zero-leakage feature tensor for Home vs Away on game_date.
        """
        h_code = team_code(home)
        a_code = team_code(away)
        if not h_code or not a_code:
            raise ValueError(f"Unknown team code for home='{home}' or away='{away}'")

        z_scores = self.compute_league_z_scores(game_date)
        zh = z_scores.get(h_code, {m: 0.0 for m in FEATURE_METRICS})
        za = z_scores.get(a_code, {m: 0.0 for m in FEATURE_METRICS})

        h_prof = self.compute_team_profile(h_code, game_date)
        a_prof = self.compute_team_profile(a_code, game_date)

        # Four Factors & Net Rating Z-Differentials
        z_net_rtg_diff = float(zh["net_rtg"] - za["net_rtg"])
        # Sign convention: every term is (home offense vs away defense) - (away offense vs home defense), so a positive
        # value always favours HOME. Defense metrics split in two families:
        #   opp_efg_pct / opp_ftr / def_rtg = what the team CONCEDES (high = bad defense, helps the opponent's offense)
        #   opp_tov_pct = turnovers the team FORCES (high = good defense, hurts the opponent's offense)
        z_off_def_matchup = float(0.5 * ((zh["off_rtg"] + za["def_rtg"]) - (za["off_rtg"] + zh["def_rtg"])))
        z_efg_diff = float((zh["efg_pct"] + za["opp_efg_pct"]) - (za["efg_pct"] + zh["opp_efg_pct"]))
        z_tov_diff = float((za["tov_pct"] + zh["opp_tov_pct"]) - (zh["tov_pct"] + za["opp_tov_pct"]))
        z_oreb_diff = float((zh["oreb_pct"] - za["dreb_pct"]) - (za["oreb_pct"] - zh["dreb_pct"]))
        z_ftr_diff = float((zh["ftr"] + za["opp_ftr"]) - (za["ftr"] + zh["opp_ftr"]))
        z_ts_diff = float(zh["ts_pct"] - za["ts_pct"])

        # Totals Z-Sums
        z_pace_sum = float(zh["pace"] + za["pace"])
        z_off_sum = float(zh["off_rtg"] + za["off_rtg"])

        combined_3par = float(0.5 * (h_prof["threepar"] + a_prof["threepar"]))
        raw_pace_est = float(0.5 * (h_prof["pace"] + a_prof["pace"]))

        # Fatigue
        fatigue = self.compute_schedule_fatigue(h_code, a_code, game_date)

        # Injury deltas
        h_pts_lost = 0.0
        a_pts_lost = 0.0
        if injury_report:
            h_pts_lost = self._calc_team_injury_impact(h_code, injury_report.get(h_code, []))
            a_pts_lost = self._calc_team_injury_impact(a_code, injury_report.get(a_code, []))

        # Margin delta: if Home loses points, margin goes down (negative)
        injury_margin_delta = float(a_pts_lost - h_pts_lost)
        # Total delta: missing offensive stars reduce total points
        injury_total_delta = float(-0.75 * (h_pts_lost + a_pts_lost))

        return MatchupFeatures(
            home_team=h_code,
            away_team=a_code,
            game_date=game_date,
            z_net_rtg_diff=z_net_rtg_diff,
            z_off_def_matchup=z_off_def_matchup,
            z_efg_diff=z_efg_diff,
            z_tov_diff=z_tov_diff,
            z_oreb_diff=z_oreb_diff,
            z_ftr_diff=z_ftr_diff,
            z_ts_diff=z_ts_diff,
            z_pace_sum=z_pace_sum,
            z_off_sum=z_off_sum,
            combined_3par=combined_3par,
            raw_pace_est=raw_pace_est,
            fatigue=fatigue,
            injury_margin_delta=injury_margin_delta,
            injury_total_delta=injury_total_delta,
            home_points_lost=h_pts_lost,
            away_points_lost=a_pts_lost,
        )

    def _calc_team_injury_impact(
        self,
        team: str,
        injuries: List[Dict[str, Any]],
    ) -> float:
        """
        Estimate points lost above replacement for a team from inactive rotation players.
        Each player impact:
          points_lost = max(0.5, value_per_game) * status_probability
        """
        if not injuries:
            return 0.0
        status_weights = {
            "Out": 1.0,
            "Doubtful": 0.75,
            "Questionable": 0.35,
            "Day-To-Day": 0.25,
            "Probable": 0.0,
        }
        total_lost = 0.0
        for inj in injuries:
            status = inj.get("status", "Out")
            weight = status_weights.get(status, 0.0)
            if weight <= 0:
                continue

            # Player value: if provided in dict or estimated
            val = float(inj.get("pts_above_replacement", 0.0))
            if val <= 0:
                # Fallback estimation based on minutes or points per game
                ppg = float(inj.get("ppg", 0.0))
                mpg = float(inj.get("mpg", 0.0))
                if ppg > 0:
                    val = max(1.0, ppg * 0.35)
                elif mpg > 0:
                    val = max(0.8, (mpg / 36.0) * 3.5)
                else:
                    # Generic rotation player absence
                    val = 1.5

            total_lost += float(val * weight)

        # Cap total injury impact to reasonable maximum (e.g. max 14 pts)
        return float(min(14.0, total_lost))


# SportsBlaze abbreviations (and a few historical tricodes) that nba.teams.team_code cannot resolve on its own.
# Known trap: PHO vs PHX, NOR vs NOP, UTH vs UTA. A game whose team cannot be resolved is dropped LOUDLY (counted + logged).
SB_ABBR_FIX: Dict[str, str] = {
    "PHO": "PHX", "NOR": "NOP", "NOH": "NOP", "NOK": "NOP", "UTH": "UTA", "UTAH": "UTA",
    "BRK": "BKN", "BK": "BKN", "CHO": "CHA", "CHH": "CHA", "PHL": "PHI", "GS": "GSW", "GSW": "GSW",
    "NY": "NYK", "SA": "SAS", "NO": "NOP", "WSH": "WAS", "WAS": "WAS",
}


def sb_team_code(abbr: Any) -> Optional[str]:
    """SportsBlaze team abbreviation (team_abbr/opp_abbr in the masters) -> our tricode, or None."""
    a = abbr.strip().upper() if isinstance(abbr, str) else ""
    if not a:
        return None
    return team_code(SB_ABBR_FIX.get(a, a)) or team_code(a)


def _et_game_day(date_val: Any, tip: Any = None) -> str:
    """ET game day (YYYY-MM-DD). The masters' `date` is the UTC DATE of the tip (docs/solutions/integration-issues/
    sportsblaze-master-date-is-utc.md); with the UTC tip timestamp we convert exactly, without it we can only keep the date."""
    if tip is not None and not pd.isna(tip):
        t = pd.Timestamp(tip)
        t = t.tz_localize("UTC") if t.tzinfo is None else t
        return t.tz_convert("America/New_York").strftime("%Y-%m-%d")
    return str(date_val)[:10]


def _num(row: Any, col: str) -> float:
    """Float cell, NaN when missing/unparseable (float(NaN) bypasses .get defaults, so check explicitly)."""
    try:
        return float(row.get(col))
    except (TypeError, ValueError):
        return float("nan")


_REQUIRED_BOX_COLS = ("game_id", "team_abbr", "opp_abbr", "date", "points")
# fields whose NaN would poison possessions / off_rtg / tov_pct (team box has NaN rebounds + turnovers for 2019-24)
_REQUIRED_STAT_FIELDS = (
    "points", "field_goals_made", "field_goals_attempted", "three_points_made", "three_points_attempted",
    "free_throws_attempted", "rebounds_offensive", "rebounds_defensive", "turnovers",
)
# the subset of required fields the player box master can backfill (it is complete for 2019-24)
_BACKFILL_STAT_FIELDS = ("rebounds_offensive", "rebounds_defensive", "turnovers")


def _player_sum_backfill(df: pd.DataFrame, df_players: Optional[pd.DataFrame]):
    """Fill NaN team-box rebounds/turnovers from player-box sums per (game_id, team_abbr).

    The join nba/sim/fit.py::team_table (PR #186) uses, ported per the Argon review of #183: the team
    master has NaN rebounds + turnovers for 2019-24 while the player master is complete, so summing the
    played player rows reconstructs the team totals instead of dropping those team-games as nan_stats.
    Only NaN cells are filled (a real team value is never overwritten). Returns (df, team-rows touched).
    Fail-soft: missing player frame / columns / any error returns the input frame unchanged.
    """
    if df_players is None or len(df_players) == 0:
        return df, 0
    need = {"game_id", "team_abbr", *_BACKFILL_STAT_FIELDS}
    if not need.issubset(df_players.columns) or not need.issubset(df.columns):
        return df, 0
    try:
        p = df_players
        if "played" in p.columns:
            p = p[p["played"].astype(str).str.lower().isin(["true", "1", "1.0"])]
        p = p.copy()
        for c in _BACKFILL_STAT_FIELDS:
            p[c] = pd.to_numeric(p[c], errors="coerce")
        sums = p.groupby([p["game_id"].astype(str), p["team_abbr"].astype(str)])[
            list(_BACKFILL_STAT_FIELDS)].sum()
        out = df.copy()
        keys = list(zip(out["game_id"].astype(str), out["team_abbr"].astype(str)))
        touched = pd.Series(False, index=out.index)
        for c in _BACKFILL_STAT_FIELDS:
            lookup = sums[c].to_dict()
            fill = pd.Series([lookup.get(k, np.nan) for k in keys], index=out.index, dtype=float)
            mask = out[c].isna() & fill.notna()
            out.loc[mask, c] = fill[mask]
            touched |= mask
        return out, int(touched.sum())
    except Exception as exc:  # noqa: BLE001 - a backfill failure must never break the loader
        log.warning("player-box backfill failed (%r); team box used as-is", exc)
        return df, 0


def build_team_game_stats_from_raw(
    df_boxscores: Optional[pd.DataFrame],
    tips: Optional[pd.Series] = None,
    df_player_boxscores: Optional[pd.DataFrame] = None,
) -> List[TeamGameStats]:
    """
    Parse the team boxscores master (NBA/team_boxscores_master.csv, schema in nba/data/flatten.py) into TeamGameStats.
    Grain: (game_id, team). Columns used: game_id, season_year, season_type, date, team_abbr, opp_abbr, is_home + stat fields.

    tips: optional Series game_id -> UTC tip timestamp (nba.props.data.gather_tips). Converts the master's UTC `date`
    to the ET game day so rest/back-to-back are right. Games without a tip keep the master date (counted + logged).
    df_player_boxscores: optional player boxscores master (same data source as the team master). When given,
    NaN rebounds/turnovers in the 2019-24 team box are backfilled from played-player sums per
    (game_id, team_abbr) -- see _player_sum_backfill -- instead of dropping those team-games. When absent,
    behavior is unchanged (fail-soft).
    Dropped rows are never silent: unresolved tricodes, NaN box stats (2019-24 team box has no rebounds/turnovers),
    preseason and malformed games are each counted and logged.
    """
    if df_boxscores is None or df_boxscores.empty:
        return []
    missing = [c for c in _REQUIRED_BOX_COLS if c not in df_boxscores.columns]
    if missing:
        raise ValueError(f"team boxscores master missing columns {missing}; have {list(df_boxscores.columns)}")

    games: List[TeamGameStats] = []
    drops = {"preseason": 0, "not_two_rows": 0, "unknown_team": 0, "nan_stats": 0}
    approx_date = 0
    unknown_abbrs: Dict[str, int] = {}

    df = df_boxscores
    if "season_type" in df.columns:
        pre = df["season_type"].astype(str).str.lower().eq("preseason")
        drops["preseason"] = int(pre.sum())
        df = df[~pre]

    df, n_backfilled = _player_sum_backfill(df, df_player_boxscores)
    if n_backfilled:
        log.info("NBA history loader: backfilled rebounds/turnovers for %d team-rows from the player "
                 "box master (the 2019-24 team box is NaN there)", n_backfilled)

    for game_id, g_df in df.groupby("game_id", sort=False):
        if len(g_df) != 2:
            drops["not_two_rows"] += 1
            continue
        row_a = g_df.iloc[0]
        row_b = g_df.iloc[1]
        tip = tips.get(game_id) if tips is not None and len(tips) else None
        tip_known = tip is not None and not pd.isna(tip)
        if not tip_known:
            approx_date += 1

        for team_row, opp_row in [(row_a, row_b), (row_b, row_a)]:
            t_code = sb_team_code(team_row.get("team_abbr"))
            opp_code = sb_team_code(opp_row.get("team_abbr"))
            if not t_code or not opp_code or t_code == opp_code:
                drops["unknown_team"] += 1
                for r in (team_row, opp_row):
                    if not sb_team_code(r.get("team_abbr")):
                        k = str(r.get("team_abbr"))
                        unknown_abbrs[k] = unknown_abbrs.get(k, 0) + 1
                continue
            if any(np.isnan(_num(r, c)) for r in (team_row, opp_row) for c in _REQUIRED_STAT_FIELDS):
                drops["nan_stats"] += 1
                continue

            pts = _num(team_row, "points")
            opp_pts = _num(opp_row, "points")
            mins = _num(team_row, "minutes")
            if np.isnan(mins):
                mins = 240.0

            fga = _num(team_row, "field_goals_attempted")
            fgm = _num(team_row, "field_goals_made")
            tpa = _num(team_row, "three_points_attempted")
            tpm = _num(team_row, "three_points_made")
            fta = _num(team_row, "free_throws_attempted")
            oreb = _num(team_row, "rebounds_offensive")
            dreb = _num(team_row, "rebounds_defensive")
            tov = _num(team_row, "turnovers")

            opp_fga = _num(opp_row, "field_goals_attempted")
            opp_fgm = _num(opp_row, "field_goals_made")
            opp_tpm = _num(opp_row, "three_points_made")
            opp_fta = _num(opp_row, "free_throws_attempted")
            opp_oreb = _num(opp_row, "rebounds_offensive")
            opp_dreb = _num(opp_row, "rebounds_defensive")
            opp_tov = _num(opp_row, "turnovers")

            t_poss = calc_possessions(fga, oreb, tov, fta)
            opp_poss = calc_possessions(opp_fga, opp_oreb, opp_tov, opp_fta)
            poss = max(50.0, 0.5 * (t_poss + opp_poss))

            off_rtg = (pts / poss) * 100.0
            def_rtg = (opp_pts / poss) * 100.0
            net_rtg = off_rtg - def_rtg
            pace = poss * (240.0 / max(180.0, mins))

            efg_pct = (fgm + 0.5 * tpm) / max(1.0, fga)
            opp_efg_pct = (opp_fgm + 0.5 * opp_tpm) / max(1.0, opp_fga)

            tov_pct = tov / max(1.0, poss)
            opp_tov_pct = opp_tov / max(1.0, poss)

            oreb_pct = oreb / max(1.0, oreb + opp_dreb)
            dreb_pct = dreb / max(1.0, dreb + opp_oreb)

            ftr = fta / max(1.0, fga)
            opp_ftr = opp_fta / max(1.0, opp_fga)

            ts_pct = pts / max(1.0, 2.0 * (fga + 0.44 * fta))
            threepar = tpa / max(1.0, fga)

            is_home = bool(team_row.get("is_home", False))
            game_date = _et_game_day(team_row.get("date"), tip if tip_known else None)
            try:
                season_year = int(team_row.get("season_year"))
            except (TypeError, ValueError):
                season_year = int(game_date[:4]) if game_date[5:7] >= "08" else int(game_date[:4]) - 1

            games.append(
                TeamGameStats(
                    game_id=str(game_id),
                    game_date=game_date,
                    season_year=season_year,
                    team=t_code,
                    opp=opp_code,
                    is_home=is_home,
                    points=pts,
                    opp_points=opp_pts,
                    minutes=mins,
                    possessions=poss,
                    off_rtg=off_rtg,
                    def_rtg=def_rtg,
                    net_rtg=net_rtg,
                    pace=pace,
                    efg_pct=efg_pct,
                    opp_efg_pct=opp_efg_pct,
                    tov_pct=tov_pct,
                    opp_tov_pct=opp_tov_pct,
                    oreb_pct=oreb_pct,
                    dreb_pct=dreb_pct,
                    ftr=ftr,
                    opp_ftr=opp_ftr,
                    ts_pct=ts_pct,
                    threepar=threepar,
                )
            )

    n_drop = sum(v for k, v in drops.items() if k != "preseason")
    if n_drop or drops["preseason"] or approx_date:
        log.warning(
            "NBA history loader: kept %d team-games; dropped rows %s (unknown abbrs %s); %d games used the UTC master "
            "date because no tip time was available (rest/B2B approximate)",
            len(games), drops, unknown_abbrs or "-", approx_date,
        )
    else:
        log.info("NBA history loader: kept %d team-games", len(games))
    return games
