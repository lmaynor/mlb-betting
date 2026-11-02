"""nba.props.team_context -- game-environment factors built ONLY from the player box-score rows already in the serving index.

Two factors, both ratios of "what tonight's game should look like" to "what the player's own recent games looked like":

  * rebound pool factor : expected missed-shot pool of tonight's game (both teams' misses, free-throw misses weighted 0.44)
                          / the EWM of the pool in the player's last games.   Scales REBOUNDS.
  * shot-making factor  : expected field goals made by the player's team tonight / the EWM of his team's makes in his last
                          games.   Scales ASSISTS (an assist needs a made basket).

Expected values for a team T vs opponent O use each side's own EWM output and the other's EWM allowed:
    E[T output vs O] = 0.5 * (off(T) + def(O)),     E[pool] = E[T misses vs O] + E[O misses vs T].
Everything is strictly prior to the target day (bisect on the date strings), so a replay cannot leak. Rows are the
cockpit_form shape; a team-game is the sum of that team's players' rows for one date (one game per team per date).
This module is pure (no I/O). Constants are priors that the walk-forward in nba/props/engine_eval.py is meant to test.
"""
from __future__ import annotations

from bisect import bisect_left

import numpy as np

FT_MISS_WEIGHT = 0.44            # a missed free throw only starts a rebound opportunity ~44% as often as a missed field goal
TEAM_HALFLIFE = 10.0             # games
MIN_TEAM_GAMES = 5
PLAYER_POOL_GAMES = 20
RATIO_CLIP = (0.80, 1.25)


def _num(row: dict, col: str) -> float:
    try:
        v = float(row.get(col))
    except (TypeError, ValueError):
        return float("nan")
    return v


class TeamGames:
    """Per-team chronological game table: date, own FGM/FGA/FTM/FTA, opponent code. Built from {player: rows}."""

    def __init__(self, table: dict):
        agg: dict = {}
        for name, rows in table.items():
            if str(name).startswith("id:"):
                continue
            for r in rows:
                team, d = r.get("team_abbr"), r.get("_date") or r.get("_sort")
                if not team or not d:
                    continue
                vals = [_num(r, c) for c in ("field_goals_made", "field_goals_attempted", "free_throws_made",
                                              "free_throws_attempted")]
                if any(v != v for v in vals):
                    continue
                g = agg.setdefault((team, str(d)[:10]), [0.0, 0.0, 0.0, 0.0, None])
                for i, v in enumerate(vals):
                    g[i] += v
                if g[4] is None and r.get("_opp"):
                    g[4] = r.get("_opp")
        self._by_team: dict = {}
        for (team, d), g in agg.items():
            self._by_team.setdefault(team, []).append((d, g))
        self.dates: dict = {}
        self.fgm: dict = {}
        self.miss: dict = {}
        self.opp_fgm: dict = {}
        self.opp_miss: dict = {}
        self._agg = agg
        for team, lst in self._by_team.items():
            lst.sort(key=lambda x: x[0])
            self.dates[team] = [d for d, _ in lst]
            self.fgm[team] = np.array([g[0] for _, g in lst], dtype=float)
            self.miss[team] = np.array([(g[1] - g[0]) + FT_MISS_WEIGHT * (g[3] - g[2]) for _, g in lst], dtype=float)
            opp_f, opp_m = [], []
            for d, g in lst:
                o = agg.get((g[4], d)) if g[4] else None
                opp_f.append(np.nan if o is None else o[0])
                opp_m.append(np.nan if o is None else (o[1] - o[0]) + FT_MISS_WEIGHT * (o[3] - o[2]))
            self.opp_fgm[team] = np.array(opp_f, dtype=float)
            self.opp_miss[team] = np.array(opp_m, dtype=float)

    # -- helpers ------------------------------------------------------------------------------------------------
    def _prior_idx(self, team: str, day: str) -> int:
        return bisect_left(self.dates.get(team, []), day)

    @staticmethod
    def _ewm(x: np.ndarray, halflife: float = TEAM_HALFLIFE) -> float | None:
        x = x[np.isfinite(x)]
        if len(x) < MIN_TEAM_GAMES:
            return None
        w = 0.5 ** ((len(x) - 1 - np.arange(len(x))) / halflife)
        return float(np.sum(w * x) / np.sum(w))

    def _off_def(self, team: str, day: str, own: dict, allowed: dict):
        k = self._prior_idx(team, day)
        return self._ewm(own[team][:k]) if team in own else None, self._ewm(allowed[team][:k]) if team in allowed else None

    def expected(self, team: str, opp: str, day: str, own: dict, allowed: dict) -> float | None:
        """E[team output vs opp] = 0.5 * (off(team) + def(opp)); None when either side lacks MIN_TEAM_GAMES."""
        off_t, _ = self._off_def(team, day, own, allowed)
        _, def_o = self._off_def(opp, day, own, allowed)
        if off_t is None or def_o is None:
            return None
        return 0.5 * (off_t + def_o)

    def expected_pool(self, team: str, opp: str, day: str) -> float | None:
        a = self.expected(team, opp, day, self.miss, self.opp_miss)
        b = self.expected(opp, team, day, self.miss, self.opp_miss)
        return None if a is None or b is None else a + b

    def expected_makes(self, team: str, opp: str, day: str) -> float | None:
        return self.expected(team, opp, day, self.fgm, self.opp_fgm)

    def _player_games(self, rows: list, day: str, n: int) -> list:
        out = []
        for r in rows:
            d = r.get("_date") or r.get("_sort")
            if d is not None and str(d)[:10] < day and r.get("team_abbr"):
                out.append((r["team_abbr"], str(d)[:10]))
        return out[-n:]

    def _typical(self, rows: list, day: str, kind: str) -> float | None:
        vals = []
        for team, d in self._player_games(rows, day, PLAYER_POOL_GAMES):
            i = bisect_left(self.dates.get(team, []), d)
            if i >= len(self.dates.get(team, [])) or self.dates[team][i] != d:
                continue
            if kind == "pool":
                vals.append(self.miss[team][i] + self.opp_miss[team][i])
            else:
                vals.append(self.fgm[team][i])
        v = np.array(vals, dtype=float)
        v = v[np.isfinite(v)]
        if len(v) < MIN_TEAM_GAMES:
            return None
        w = 0.5 ** ((len(v) - 1 - np.arange(len(v))) / 8.0)
        return float(np.sum(w * v) / np.sum(w))

    def pool_factor(self, rows: list, team: str, opp: str, day: str, gamma: float = 1.0) -> float:
        """(tonight's expected missed-shot pool / the player's recent pool) ** gamma, clipped; 1.0 when unknown."""
        tonight, typical = self.expected_pool(team, opp, day), self._typical(rows, day, "pool")
        return _ratio(tonight, typical, gamma)

    def shotmaking_factor(self, rows: list, team: str, opp: str, day: str, gamma: float = 1.0) -> float:
        """(tonight's expected team FGM / his team's recent FGM) ** gamma, clipped; 1.0 when unknown."""
        tonight, typical = self.expected_makes(team, opp, day), self._typical(rows, day, "makes")
        return _ratio(tonight, typical, gamma)


def _ratio(tonight, typical, gamma: float) -> float:
    if tonight is None or typical is None or typical <= 0 or tonight <= 0:
        return 1.0
    r = float(np.clip((tonight / typical) ** gamma, RATIO_CLIP[0], RATIO_CLIP[1]))
    return r
