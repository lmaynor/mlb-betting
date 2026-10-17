"""nba.props.espn_data -- ESPN-keyed history for the NBA props pillar, from the hoopR parquet mirror (nba/data/hoopr.py).

It feeds the SAME downstream code as the SportsBlaze path: `to_masters` reshapes the ESPN player box into the
SportsBlaze-shaped (player_box, games, tips) triple and `prepare_espn` hands it to nba.props.data.prepare, so rows /
team_games have exactly the schema features.py, train.py and score.py already consume (a test pins the schema parity).

What changes versus SportsBlaze:
  * player_id IS the ESPN athlete id -- the id space of NBA/players_xwalk.csv and ev.grade_props.nba_actuals. The
    audited name bridge (identity.build_bridge, 505 of 629 players) becomes an identity map (`identity_bridge`);
    the 124 players it had to drop (2026 draftees) are simply players with no history yet.
  * the tip time is a real timestamp (game_date_time, America/New_York) on every row, so no schedule dumps, no
    UTC-date correction, tip_known is always True.
  * every inactive / DNP player is a row WITH a reason string -- `inactives()` / `classify_reason()` turn that into
    an availability feed. Caveat (same one the "history" availability mode already carries): this is who did NOT play in
    the game, i.e. the training-side truth. The live side only has the pre-game injury list (nba/data/espn_injuries.py).
  * seasons: hoopR season = the year the season ENDS; `season_year` here keeps the repo convention (START year,
    2025 = 2025-26), i.e. hoopR season - 1.
  * it has the 2026-27 season as it is played, which SportsBlaze does not.

Not covered (unchanged): exhibition/All-Star rows (WORLD/STARS/STRIPES abbreviations are not NBA teams -> dropped), games
with an unmapped team (dropped whole), preseason (excluded exactly as in the SportsBlaze path).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from nba.data import hoopr
from nba.props import config as C
from nba.props import data as D
from nba.teams import team_code

# hoopR/ESPN season_type code -> the SportsBlaze strings the rest of nba.props compares against
SEASON_TYPE_NAME = {1: "Preseason", 2: "Regular Season", 3: "Playoffs", 5: "Play-In"}

STAT_RENAME = {
    "field_goals_made": "field_goals_made", "field_goals_attempted": "field_goals_attempted",
    "three_point_field_goals_made": "three_points_made", "three_point_field_goals_attempted": "three_points_attempted",
    "free_throws_made": "free_throws_made", "free_throws_attempted": "free_throws_attempted",
    "offensive_rebounds": "rebounds_offensive", "defensive_rebounds": "rebounds_defensive",
    "rebounds": "rebounds", "assists": "assists", "steals": "steals", "blocks": "blocks",
    "turnovers": "turnovers", "fouls": "fouls", "points": "points",
}


def load_espn(seasons, local_dir: str | None = None) -> pd.DataFrame:
    """hoopR season numbers -> the concatenated mirrored ESPN player box (empty frame if nothing is mirrored)."""
    return hoopr.read_dataset("player_boxscores", seasons, local_dir)


def _game_frame(pb: pd.DataFrame) -> pd.DataFrame:
    """One row per game with the SportsBlaze `games_master` columns prepare() needs, derived from the player box."""
    home = pb[pb["home_away"] == "home"].drop_duplicates("game_id")
    away = pb[pb["home_away"] == "away"].drop_duplicates("game_id")
    g = home[["game_id", "season", "season_type_name", "game_date", "team_abbreviation"]].rename(
        columns={"team_abbreviation": "home_abbr"})
    g = g.merge(away[["game_id", "team_abbreviation"]].rename(columns={"team_abbreviation": "away_abbr"}),
                on="game_id", how="inner")
    g["season_year"] = g["season"] - 1
    g = g.rename(columns={"season_type_name": "season_type", "game_date": "date"})
    g["date"] = g["date"].astype(str)
    return g[["game_id", "season_year", "season_type", "date", "away_abbr", "home_abbr"]].reset_index(drop=True)


def to_masters(espn_box: pd.DataFrame):
    """ESPN player box -> (player_box, games, tips) in the SportsBlaze masters shape (see nba/data/flatten.py).

    DNP / inactive rows are dropped here (they have no minutes); `inactives()` reads them from the raw box instead."""
    pb = espn_box.copy()
    if pb.empty:
        return pb, pd.DataFrame(), pd.Series(dtype="datetime64[ns, UTC]")
    pb["season_type_name"] = pb["season_type"].map(SEASON_TYPE_NAME)
    pb = pb[pb["season_type_name"].notna() & pb["athlete_id"].notna() & pb["game_date_time"].notna()]
    # a game is usable only when both teams are real NBA teams (drops the All-Star exhibition)
    ok_team = pb["team_abbreviation"].map(lambda a: team_code(a) is not None)
    bad_games = set(pb.loc[~ok_team, "game_id"])
    pb = pb[~pb["game_id"].isin(bad_games)]
    games = _game_frame(pb)
    tips = (pb.drop_duplicates("game_id").set_index("game_id")["game_date_time"]
            .map(lambda t: pd.Timestamp(t).tz_convert("UTC")))
    tips = pd.Series(tips).astype("datetime64[ns, UTC]")

    out = pd.DataFrame({
        "game_id": pb["game_id"].astype("int64").values,
        "season_year": (pb["season"] - 1).astype("int64").values,
        "season_type": pb["season_type_name"].values,
        "date": pb["game_date"].astype(str).values,
        "team_abbr": pb["team_abbreviation"].values,
        "is_home": (pb["home_away"] == "home").values,
        "player_id": pb["athlete_id"].astype("int64").values,
        "name": pb["athlete_display_name"].values,
        "position": pb["athlete_position_abbreviation"].values,
        "starter": pb["starter"].fillna(False).astype(bool).values,
        "played": (~pb["did_not_play"].fillna(False).astype(bool)).values,
        "minutes": pb["minutes"].values,
    })
    for src, dst in STAT_RENAME.items():
        out[dst] = pd.to_numeric(pb[src], errors="coerce").fillna(0.0).values
    out = out[out["played"] & out["minutes"].notna()]
    return out.reset_index(drop=True), games, tips


def prepare_espn(espn_box: pd.DataFrame, include_preseason: bool = False):
    """ESPN player box -> (rows, team_games): the exact schema nba.props.data.prepare returns for SportsBlaze.
    Nothing usable (empty mirror, or only preseason rows) -> two empty frames, never a crash."""
    pb, games, tips = to_masters(espn_box)
    if not include_preseason and len(pb):
        pb = pb[~pb["season_type"].isin(C.EXCLUDE_SEASON_TYPES)]
    if pb.empty:
        return pd.DataFrame(columns=_EMPTY_ROWS), pd.DataFrame(columns=_EMPTY_TG)
    return D.prepare(pb, games, tips, include_preseason)


_EMPTY_ROWS = ["game_id", "season_year", "season_type", "team_abbr", "is_home", "player_id", "name", "position",
               "starter", "played", "minutes", *STAT_RENAME.values(), "min", "team", "date", "tip_sort", "tip_known",
               "opp", "pra", "usg_poss", "pos_prev", "pos_grp"]
_EMPTY_TG = ["game_id", "date", "season_year", "season_type", "team", "opp", "tip_sort", "tip_known", "is_home",
             "t_pts", "t_fga", "t_fta", "t_oreb", "t_reb", "t_tov", "t_ast", "t_fg3m", "t_fg3a", "t_min", "t_usg",
             "poss", "opp_poss", "pace", "ortg"]


# ----------------------------------------------------------------------------------------------- availability
# Reason strings are free text (228 distinct values in 2026). Everything not matched below is a body part / injury.
_CATEGORY_RULES = (
    ("COACH", re.compile(r"COACH'?S? DECISION")),
    ("SUSPENSION", re.compile(r"SUSPEND")),
    ("NOT_WITH_TEAM", re.compile(r"NOT WITH TEAM|G LEAGUE|TWO[- ]WAY|ASSIGN")),
    ("PERSONAL", re.compile(r"PERSONAL|BEREAVEMENT|FAMILY|PATERNITY|BIRTH")),
    ("REST", re.compile(r"^REST$|LOAD MANAGEMENT|^RESTING")),
    ("ILLNESS", re.compile(r"ILLNESS|VIRUS|SICK|FLU\b|COVID")),
    ("UNKNOWN", re.compile(r"DID NOT DRESS|^NAN$|^NONE$|^$")),
)
# reasons that are public before tip (what an injury list can carry); COACH and UNKNOWN are not
OUT_CATEGORIES = ("INJURY", "ILLNESS", "REST", "SUSPENSION", "PERSONAL", "NOT_WITH_TEAM")


def classify_reason(reason) -> str:
    """Free-text ESPN DNP reason -> COACH | SUSPENSION | NOT_WITH_TEAM | PERSONAL | REST | ILLNESS | UNKNOWN | INJURY.

    'LEFT KNEE INJURY MANAGEMENT' is INJURY (not REST): the rule order lets REST match only the bare word / load
    management. A missing reason is UNKNOWN, never guessed."""
    s = "" if reason is None or (isinstance(reason, float) and np.isnan(reason)) else str(reason).strip().upper()
    for name, rx in _CATEGORY_RULES:
        if rx.search(s):
            return name
    return "INJURY"


def inactives(espn_box: pd.DataFrame) -> pd.DataFrame:
    """Every listed DNP row -> game_id, date (ET day), team, player_id, name, reason, category.

    ESPN lists inactive players on the game's box score (about 4.6 per game: ~80% COACH'S DECISION, the rest are the
    absences that matter). Players ESPN does not list for a game are NOT thereby 'active' -- this is a one-way feed."""
    if espn_box is None or not len(espn_box):
        return pd.DataFrame(columns=["game_id", "date", "team", "player_id", "name", "reason", "category"])
    x = espn_box[espn_box["did_not_play"].fillna(False).astype(bool)].copy()
    x = x[x["athlete_id"].notna() & x["season_type"].isin([2, 3, 5])]
    x["team"] = x["team_abbreviation"].map(team_code)
    x = x[x["team"].notna()]
    out = pd.DataFrame({
        "game_id": x["game_id"].astype("int64").values,
        "date": pd.to_datetime(x["game_date"]).values,
        "team": x["team"].values,
        "player_id": x["athlete_id"].astype("int64").values,
        "name": x["athlete_display_name"].values,
        "reason": x["reason"].values,
    })
    out["category"] = out["reason"].map(classify_reason)
    return out.sort_values(["date", "game_id", "team", "player_id"]).reset_index(drop=True)


def scratch_frame(inactive: pd.DataFrame) -> pd.DataFrame:
    """inactives() -> the (game_id, team, player_id) rows listed as COACH'S DECISION: healthy scratches. They did not play,
    but no injury list can ever contain them, so a train-time absence definition that matches the live one drops them.

    COMPLETENESS (2024-2026, 7,296 team-games): ESPN's box lists only some non-playing players. Of 28,224 rotation-
    absent player-games, 39% are listed COACH'S DECISION, 6% injury, 2% other, and 53% are NOT LISTED at all (inactive
    players are generally not on the box). So the listing is a partial feed -- exclude scratches, never treat 'listed
    out' as the whole truth. Feed to FeatureContext(rows, tg, scratches=...), build with availability="history_excl_scratch"."""
    if inactive is None or not len(inactive):
        return pd.DataFrame(columns=["game_id", "team", "player_id"])
    k = inactive[inactive["category"] == "COACH"]
    return k[["game_id", "team", "player_id"]].drop_duplicates().reset_index(drop=True)


# ----------------------------------------------------------------------------------------------- identity
def identity_bridge(rows: pd.DataFrame, xwalk: pd.DataFrame):
    """Bridge-compatible object for ESPN-keyed history: espn_id maps to itself when the player has history.

    Same `.sb_id(espn_id) -> (id | None, reason)` contract and audit `table` as identity.Bridge, so score.py's
    resolution code is unchanged. A crosswalk player with no played game in `rows` is dropped with `no_history`
    (rookies / draftees) -- never guessed."""
    from nba.props.identity import Bridge
    have = set(rows["player_id"].astype("int64").unique()) if len(rows) else set()
    recs = []
    for e in xwalk.itertuples(index=False):
        eid = int(e.espn_id)
        ok = eid in have
        recs.append({"espn_id": eid, "espn_name": e.full_name, "espn_team": e.team,
                     "sb_player_id": eid if ok else None, "status": "ok" if ok else "dropped",
                     "reason": "espn_id" if ok else "no_history", "how": "espn_id", "team_changed": None})
    t = pd.DataFrame(recs)
    return Bridge(t, {int(r.espn_id): int(r.sb_player_id) for r in t[t["status"] == "ok"].itertuples(index=False)})
