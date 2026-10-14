"""Synthetic SportsBlaze-shaped masters for the nba.props tests (schema = nba/data/flatten.py output).

Four teams, a 45-day window with off days, two games a night (one early, one late-night tip so the UTC date differs from the
ET game day), 9 players a side. Deterministic. NOT real data -- it exists to exercise feature leakage, identity and scoring.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TEAMS_SB = ("BOS", "LAL", "NY", "MIA")            # SportsBlaze abbreviations (NY -> NYK in our tricodes)
NAMED = {"BOS-0": "Jayson Tatum", "BOS-1": "Jaylen Brown", "LAL-0": "LeBron James", "LAL-1": "Austin Reaves",
         "NY-0": "Jalen Brunson", "NY-1": "Karl-Anthony Towns", "MIA-0": "Bam Adebayo", "MIA-1": "Tyler Herro"}


def _team_id(t):
    return f"team-{t}"


def make_masters(n_days: int = 45, start: str = "2025-11-01", seed: int = 3, dnp_rate: float = 0.06,
                 season_year: int = 2025, skip: dict | None = None):
    """-> (player_box, games, tips). `skip` = {player_id: set(game_index)} to force DNPs (a player missing games)."""
    rng = np.random.default_rng(seed)
    skip = skip or {}
    base = pd.Timestamp(start)
    games, boxes, tips = [], [], {}
    gi = 0
    for d in range(n_days):
        if d % 3 == 2:
            continue
        day = base + pd.Timedelta(days=d)
        pairs = [(TEAMS_SB[0], TEAMS_SB[1], 19.5), (TEAMS_SB[2], TEAMS_SB[3], 22.5)] if d % 2 == 0 else \
                [(TEAMS_SB[1], TEAMS_SB[2], 19.5), (TEAMS_SB[3], TEAMS_SB[0], 22.5)]
        for away, home, hour in pairs:
            gid = f"g{gi:04d}"
            tip_et = (day + pd.Timedelta(hours=hour)).tz_localize("America/New_York")
            tip_utc = tip_et.tz_convert("UTC")
            tips[gid] = tip_utc
            games.append({"game_id": gid, "season_year": season_year, "season_type": "Regular Season",
                          "date": tip_utc.date().isoformat(), "status": "Final", "live": False,
                          "away_id": _team_id(away), "away_abbr": away, "away_name": away,
                          "home_id": _team_id(home), "home_abbr": home, "home_name": home,
                          "away_points": 100, "home_points": 100})
            for team, is_home in ((away, False), (home, True)):
                for k in range(9):
                    pid = f"{team}-{k}"
                    starter = k < 5
                    played = (rng.random() > dnp_rate) and gi not in skip.get(pid, set())
                    mins = float(np.clip(rng.normal(31 if starter else 14, 3.5 if starter else 5), 2, 44)) if played else 0.0
                    ppm = 0.55 - 0.04 * k
                    pts = int(rng.poisson(mins * ppm)) if played else 0
                    reb = int(rng.poisson(mins * 0.16)) if played else 0
                    ast = int(rng.poisson(mins * 0.10)) if played else 0
                    fg3 = int(rng.poisson(mins * 0.04)) if played else 0
                    fga = int(rng.poisson(mins * 0.4)) if played else 0
                    boxes.append({
                        "game_id": gid, "season_year": season_year, "season_type": "Regular Season",
                        "date": tip_utc.date().isoformat(), "team_id": _team_id(team), "team_abbr": team,
                        "is_home": is_home, "player_id": pid, "name": NAMED.get(pid, f"{team} Player{k}"),
                        "position": ["PG", "SG", "SF", "PF", "C"][k % 5], "starter": starter, "played": played,
                        "points": pts, "minutes": f"{int(mins)}:{int((mins % 1) * 60):02d}", "plus_minus": 0.0,
                        "field_goals_made": min(fga, pts // 2), "field_goals_attempted": fga,
                        "two_points_made": 0, "two_points_attempted": 0, "three_points_made": fg3,
                        "three_points_attempted": fg3 + 2 if played else 0, "free_throws_made": 0,
                        "free_throws_attempted": int(rng.poisson(mins * 0.08)) if played else 0,
                        "rebounds": reb, "rebounds_offensive": reb // 4, "rebounds_defensive": reb - reb // 4,
                        "assists": ast, "steals": int(rng.poisson(mins * 0.03)) if played else 0,
                        "blocks": int(rng.poisson(mins * 0.02)) if played else 0,
                        "turnovers": int(rng.poisson(mins * 0.07)) if played else 0, "fouls": 0})
            gi += 1
    return pd.DataFrame(boxes), pd.DataFrame(games), pd.Series(tips).astype("datetime64[ns, UTC]")


def prepared(pb, gm, tips):
    from nba.props import data as D
    return D.prepare(pb, gm, tips)


def et_day_of(game_ids, tips) -> pd.Series:
    return pd.Series({g: pd.Timestamp(tips[g]).tz_convert("America/New_York").tz_localize(None).normalize()
                      for g in game_ids})


def perturb_from(pb, gm, tips, cutoff: pd.Timestamp, factor: int = 11):
    """Copy of the masters where EVERY game on/after the ET day `cutoff` has wildly different box stats and minutes."""
    pb2 = pb.copy()
    day = et_day_of(gm["game_id"], tips)
    late = pb2["game_id"].map(day) >= cutoff
    for c in ("points", "rebounds", "assists", "three_points_made", "three_points_attempted", "field_goals_attempted",
              "free_throws_attempted", "turnovers", "steals", "blocks", "rebounds_offensive"):
        pb2.loc[late, c] = pb2.loc[late, c] * factor + 3
    pb2.loc[late, "minutes"] = "47:30"
    return pb2


def truncate_before(pb, gm, tips, cutoff: pd.Timestamp):
    """Masters containing only games whose ET day is strictly before `cutoff` (what the live path would hold)."""
    day = et_day_of(gm["game_id"], tips)
    keep = set(day[day < cutoff].index)
    return pb[pb["game_id"].isin(keep)].copy(), gm[gm["game_id"].isin(keep)].copy(), tips[tips.index.isin(keep)]
