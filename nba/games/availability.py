"""
nba.games.availability -- "who is missing" features for the game models, from the hoopR/ESPN player box.

CEILING, NOT LIVE: here "missing" = a rotation player who did not play in THAT game (known only afterwards), the same upper-bound
construction as the props pillar's `availability="history"`. The live path has only the pre-game injury list (banked forward by
`nba-injury-bank` since 2026-10-20, ESPN `NBA/injuries/espn/`), which recovers roughly 60-70% of this signal
(docs/analysis/nba_official_availability_2026-10-16.md). If the model still cannot match the line WITH this ceiling, a live injury
feed cannot rescue it; if it can, the live feed is worth wiring.

A team-game's rotation = players whose last appearance for the team was within the previous `WINDOW` team games, whose rolling
(last 10 played) minutes were >= `MIN_MINUTES` as of that appearance, and who have not since played for another team (all known
pre-game). Per team-game outputs (no leakage: the roster and rolling stats use games strictly before this one):

  av_missing_min     sum of rotation minutes missing
  av_new_missing_min the part that played the team's PREVIOUS game (a fresh absence the team's EWM has not absorbed yet)
  av_missing_pts     sum of the missing players' rolling points
  av_missing_usg     sum of their rolling usage proxy (FGA + 0.44 FTA + TOV)
  av_top_pts         largest rolling points among the missing (a star out)
  av_missing_n       count
"""
from __future__ import annotations

from collections import defaultdict, deque

import numpy as np
import pandas as pd

from nba.teams import team_code

WINDOW = 10
MIN_MINUTES = 15.0
ROLL = 10
COLS = ["av_missing_min", "av_new_missing_min", "av_missing_pts", "av_missing_usg", "av_top_pts", "av_missing_n"]


def _prep(pb: pd.DataFrame) -> pd.DataFrame:
    d = pb.copy()
    d["team"] = d["team_abbreviation"].map(team_code)
    d = d[d["team"].notna() & d["season_type"].isin([2, 3, 5])]
    d["game_id"] = d["game_id"].astype(str)
    d["minutes"] = pd.to_numeric(d["minutes"], errors="coerce").fillna(0.0)
    for c in ("points", "field_goals_attempted", "free_throws_attempted", "turnovers"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
    d["usg"] = d["field_goals_attempted"] + 0.44 * d["free_throws_attempted"] + d["turnovers"]
    d["tip"] = pd.to_datetime(d["game_date_time"], utc=True)
    return d


def build(player_box: pd.DataFrame) -> pd.DataFrame:
    """player box -> one row per (game_id, team) with the av_* columns. Process games chronologically."""
    d = _prep(player_box)
    order = d.drop_duplicates("game_id").sort_values(["tip", "game_id"])["game_id"].tolist()
    by_game = {gid: g for gid, g in d.groupby("game_id", sort=False)}
    hist = defaultdict(lambda: deque(maxlen=ROLL))        # player -> deque of (min, pts, usg) over games PLAYED
    last_team = {}                                        # player -> (team, game-no of that team's game)
    team_n = defaultdict(int)                             # team -> games processed so far
    roster = defaultdict(dict)                            # team -> {player: team_game_no of last appearance}
    team_season = {}                                      # team -> season of its last game (roster resets each season:
                                                          # an off-season departure must not read as a fresh absence)
    out = []
    for gid in order:
        g = by_game[gid]
        for team, tg in g.groupby("team"):
            t = team_n[team]
            if team_season.get(team) != tg["season"].iloc[0]:
                roster[team].clear()
                team_season[team] = tg["season"].iloc[0]
            played = set(tg.loc[tg["minutes"] > 0, "athlete_id"])
            miss = []
            for p, last_t in list(roster[team].items()):
                if t - last_t > WINDOW or last_team.get(p, (team,))[0] != team:
                    del roster[team][p]
                    continue
                h = hist[p]
                m = float(np.mean([x[0] for x in h]))
                if m >= MIN_MINUTES and p not in played:
                    miss.append((m, float(np.mean([x[1] for x in h])), float(np.mean([x[2] for x in h])), last_t == t - 1))
            out.append({"game_id": gid, "team": team,
                        "av_missing_min": sum(x[0] for x in miss),
                        "av_new_missing_min": sum(x[0] for x in miss if x[3]),
                        "av_missing_pts": sum(x[1] for x in miss), "av_missing_usg": sum(x[2] for x in miss),
                        "av_top_pts": max([x[1] for x in miss], default=0.0), "av_missing_n": len(miss)})
        # update AFTER the game: players who played join / refresh the roster and their rolling stats
        for team, tg in g.groupby("team"):
            for r in tg[tg["minutes"] > 0].itertuples():
                hist[r.athlete_id].append((r.minutes, r.points, r.usg))
                roster[team][r.athlete_id] = team_n[team]
                last_team[r.athlete_id] = (team, team_n[team])
            team_n[team] += 1
    return pd.DataFrame(out)


def attach(feat: pd.DataFrame, av: pd.DataFrame) -> pd.DataFrame:
    """Merge av_* onto the game feature table as h_av_* / a_av_* (games with no player box stay NaN)."""
    h = av.rename(columns={c: f"h_{c}" for c in COLS}).rename(columns={"team": "home"})
    a = av.rename(columns={c: f"a_{c}" for c in COLS}).rename(columns={"team": "away"})
    f = feat.merge(h, on=["game_id", "home"], how="left").merge(a, on=["game_id", "away"], how="left")
    return f
