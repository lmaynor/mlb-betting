"""
nba.games.data -- one-row-per-game NBA table for the moneyline / spread / totals models.

Two sources, joined by franchise tricode (nba.teams.team_code):
  * hoopR/ESPN team box (nba/data/hoopr.py mirror, 2018-19 onward): real ET tip time, regular season + play-in +
    playoffs (no preseason). This is the live-reproducible source.
  * the nba_api team game log in the `NBA-Time-Series-Forecasts` zip (`nba_game_data.csv`, 1983 -> 2025-01-20): used for
    Elo / rating WARM-UP before 2018-19 only (`before=` cut). Its `processed_nba_game_data.csv` is NOT used -- its
    `Points_Per_Game` is a season-wide mean (uses the future) and its TEAM_ID is zeroed.

Output columns: game_id, date (ET, str), tip (ET timestamp or NaT), season (START year, 2025 = 2025-26),
season_type (2 regular, 3 playoffs, 5 play-in), home, away, home_pts, away_pts, and `{h,a}_{fga,fgm,fg3m,fg3a,fta,ftm,
oreb,dreb,tov,ast,stl,blk}` box stats.
"""
from __future__ import annotations

import pandas as pd

from nba.teams import team_code

BOX = ["fga", "fgm", "fg3m", "fg3a", "fta", "ftm", "oreb", "dreb", "tov", "ast", "stl", "blk"]

_HOOPR = {"field_goals_attempted": "fga", "field_goals_made": "fgm", "three_point_field_goals_made": "fg3m",
          "three_point_field_goals_attempted": "fg3a", "free_throws_attempted": "fta", "free_throws_made": "ftm",
          "offensive_rebounds": "oreb", "defensive_rebounds": "dreb", "total_turnovers": "tov", "assists": "ast",
          "steals": "stl", "blocks": "blk"}
_TSF = {"FGA": "fga", "FGM": "fgm", "FG3M": "fg3m", "FG3A": "fg3a", "FTA": "fta", "FTM": "ftm", "OREB": "oreb",
        "DREB": "dreb", "TOV": "tov", "AST": "ast", "STL": "stl", "BLK": "blk"}


def _pair(long: pd.DataFrame) -> pd.DataFrame:
    """long = one row per team-game with is_home, team, pts, box cols -> one row per game (home/away prefixed)."""
    keep = ["game_id", "team", "pts"] + BOX
    h = long[long["is_home"]][keep].rename(columns={c: f"h_{c}" if c in BOX else c for c in keep})
    a = long[~long["is_home"]][keep].rename(columns={c: f"a_{c}" if c in BOX else c for c in keep})
    h = h.rename(columns={"team": "home", "pts": "home_pts"})
    a = a.rename(columns={"team": "away", "pts": "away_pts"})
    return h.merge(a, on="game_id", how="inner")


def from_hoopr(team_box: pd.DataFrame) -> pd.DataFrame:
    """hoopR team_box (already concatenated) -> game table. Drops games with an unmapped team (All-Star / exhibitions)."""
    tb = team_box.copy()
    tb["team"] = tb["team_abbreviation"].map(team_code)
    tb = tb[tb["team"].notna() & tb["opponent_team_abbreviation"].map(team_code).notna()]
    tb = tb.rename(columns={"team_score": "pts", **_HOOPR})
    tb["is_home"] = tb["team_home_away"].eq("home")
    for c in ["pts"] + BOX:
        tb[c] = pd.to_numeric(tb[c], errors="coerce")
    tb["game_id"] = tb["game_id"].astype(str)
    tip = pd.to_datetime(tb["game_date_time"], utc=True).dt.tz_convert("America/New_York")
    meta = pd.DataFrame({"game_id": tb["game_id"], "tip": tip, "season_hoopr": tb["season"],
                         "season_type": tb["season_type"]}).drop_duplicates("game_id")
    g = _pair(tb.drop_duplicates(["game_id", "is_home"])).merge(meta, on="game_id")
    g["date"] = g["tip"].dt.strftime("%Y-%m-%d")
    g["season"] = g["season_hoopr"] - 1          # repo convention: START year (hoopR season = year the season ENDS)
    g = g.drop(columns="season_hoopr")
    return _finish(g)


def from_tsf(csv_path: str, before: str | None = None) -> pd.DataFrame:
    """nba_api team-game-log CSV (Time-Series-Forecasts zip) -> game table (no tip time). `before` = first date to EXCLUDE
    (pass the first hoopR date so the two sources never overlap)."""
    d = pd.read_csv(csv_path)
    d = d[d["SEASON_ID"].astype(str).str[0].isin(["2", "4", "5"])].copy()   # 1 = preseason, 6 = in-season tournament etc.
    # TEAM_ID is stable per franchise; the abbreviation moved (NJN->BKN, SEA->OKC, NOH->NOP): take each id's latest one.
    latest = d.sort_values("GAME_DATE").groupby("TEAM_ID")["TEAM_ABBREVIATION"].last().map(team_code)
    d["team"] = d["TEAM_ID"].map(latest)
    d = d[d["team"].notna()]
    d["is_home"] = ~d["MATCHUP"].str.contains("@", regex=False)
    d = d.rename(columns={"PTS": "pts", **_TSF})
    d["game_id"] = d["GAME_ID"].astype(str).str.zfill(10)
    for c in ["pts"] + BOX:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    meta = d[["game_id", "GAME_DATE", "SEASON_ID"]].drop_duplicates("game_id")
    g = _pair(d.drop_duplicates(["game_id", "is_home"])).merge(meta, on="game_id")
    g["date"] = g["GAME_DATE"].astype(str)
    st = g["SEASON_ID"].astype(str)
    g["season_type"] = st.str[0].astype(int)
    g["season"] = st.str[1:].astype(int)                 # 22024 -> 2024 (START year already)
    g["tip"] = pd.NaT
    g = g.drop(columns=["GAME_DATE", "SEASON_ID"])
    if before:
        g = g[g["date"] < before]
    return _finish(g)


def _finish(g: pd.DataFrame) -> pd.DataFrame:
    g = g.dropna(subset=["home_pts", "away_pts"])
    g = g[(g["home_pts"] > 0) & (g["away_pts"] > 0)]
    # a stable chronological order; tip breaks same-day ties, then game_id
    g = g.sort_values(["date", "tip", "game_id"], na_position="last").reset_index(drop=True)
    cols = ["game_id", "date", "tip", "season", "season_type", "home", "away", "home_pts", "away_pts"]
    return g[cols + [f"{s}_{b}" for s in ("h", "a") for b in BOX]]


def load(local_hoopr_dir: str, tsf_csv: str | None, seasons=range(2019, 2028)) -> pd.DataFrame:
    """hoopR seasons (hoopR numbering) + optional pre-2018-19 warm-up from the TSF csv."""
    from nba.data import hoopr
    tb = hoopr.read_dataset("team_boxscores", seasons, local_hoopr_dir)
    g = from_hoopr(tb)
    if tsf_csv:
        w = from_tsf(tsf_csv, before=g["date"].min())
        g = pd.concat([w, g], ignore_index=True)
    return g.sort_values(["date", "tip", "game_id"], na_position="last").reset_index(drop=True)
