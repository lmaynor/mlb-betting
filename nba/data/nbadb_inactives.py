"""nbadb (Kaggle wyattowalsh/basketball) official per-game INACTIVE lists -> ESPN-keyed frame for the props availability features.

STATUS: BUILT AGAINST THE DOCUMENTED SCHEMA, NEVER RUN ON REAL DATA. The dataset needs Kaggle credentials and none were available
when this was written (no ~/.kaggle, no KAGGLE_* env). The column names below come from nbadb's own docs
(docs/content/docs/diagrams/er-auto.mdx in the supplied zip):

    fact_box_score_summary_v3_inactive_players: game_id, team_id, person_id, first_name, family_name, jersey_num
    dim_game: game_id, game_date, season_year, season_type, home_team_id, visitor_team_id, matchup, ...

Every loader fails loudly when a column is missing rather than guessing. The dataset ships DuckDB / SQLite / Parquet / CSV; this
reads the Parquet or CSV per-table files (nba.data.kaggle_ingest mirrors a different dataset, eoinamoore's, whose inactives
coverage is unknown). Run it only after checking the real file names/columns:

    kaggle datasets download wyattowalsh/basketball   # or kagglehub; then point --dir at the extracted tables

IDS: nbadb ids are NBA personIds / team ids -- neither ESPN. Teams map through nba.teams.NBA_ID; players resolve by NAME + team
(mlb_core PlayerResolver over ESPN-keyed history, fail closed: ambiguous or unknown names are dropped and COUNTED). Games map
by (ET game date, team) to the ESPN game id in the prepared history.
Use: FeatureContext(rows, tg, inactives=to_espn(...)); build with availability="history_inactive" (the league's own pre-game
inactive list -- published ~30 min before tip -- restricted to rotation players who did not dress).
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from mlb_core.data.player_names import PlayerResolver
from nba.teams import NBA_ID

log = logging.getLogger(__name__)

INACTIVE_TABLE = "fact_box_score_summary_v3_inactive_players"
GAME_TABLE = "dim_game"
INACTIVE_COLS = ("game_id", "team_id", "person_id", "first_name", "family_name")
GAME_COLS = ("game_id", "game_date")
_TEAM_OF_ID = {i: c for c, i in NBA_ID.items()}


def _read_table(root: Path, name: str) -> pd.DataFrame:
    for ext, reader in ((".parquet", pd.read_parquet), (".csv", pd.read_csv)):
        hits = sorted(root.rglob(f"{name}{ext}"))
        if hits:
            return reader(hits[0])
    raise FileNotFoundError(f"table {name} (.parquet/.csv) not found under {root}")


def _need(df: pd.DataFrame, cols, table: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{table}: missing columns {missing} (have {list(df.columns)}); the schema this loader was written "
                         "against has changed -- check nbadb's docs before using it")


def load_inactives(root: str) -> pd.DataFrame:
    """-> game_id (NBA), game_date, team (tricode), nba_person_id, name. Unknown team ids are dropped and counted in attrs."""
    r = Path(root)
    ina, gm = _read_table(r, INACTIVE_TABLE), _read_table(r, GAME_TABLE)
    _need(ina, INACTIVE_COLS, INACTIVE_TABLE)
    _need(gm, GAME_COLS, GAME_TABLE)
    df = ina.merge(gm[list(GAME_COLS)].drop_duplicates("game_id"), on="game_id", how="inner")
    df["team"] = df["team_id"].astype("int64").map(_TEAM_OF_ID)
    n_unknown = int(df["team"].isna().sum())
    df = df[df["team"].notna()].copy()
    df["name"] = (df["first_name"].fillna("").astype(str).str.strip() + " " +
                  df["family_name"].fillna("").astype(str).str.strip()).str.strip()
    df["game_date"] = pd.to_datetime(df["game_date"]).dt.normalize()
    out = df.rename(columns={"person_id": "nba_person_id"})[["game_id", "game_date", "team", "nba_person_id", "name"]]
    out = out.reset_index(drop=True)
    out.attrs["n_unknown_team"] = n_unknown
    return out


def to_espn(inactives: pd.DataFrame, rows: pd.DataFrame, tg: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """-> ((game_id ESPN, team, player_id ESPN) frame, stats). Fails closed: unresolved names / unmatched games are dropped."""
    last = rows.sort_values(["tip_sort", "game_id"]).groupby("player_id", as_index=False).tail(1)
    res = PlayerResolver({"player_id": r.player_id, "full_name": r.name, "team": r.team}
                         for r in last.itertuples(index=False))          # ids kept native (ESPN ints in production)
    games = {(pd.Timestamp(d).normalize(), t): g for g, d, t in zip(tg["game_id"], tg["date"], tg["team"])}
    recs, bad_name, bad_game = [], 0, 0
    for r in inactives.itertuples(index=False):
        gid = games.get((pd.Timestamp(r.game_date).normalize(), r.team))
        if gid is None:
            bad_game += 1
            continue
        pid, _how = res.resolve(r.name, r.team)
        if pid is None:
            bad_name += 1
            continue
        recs.append((gid, r.team, pid))
    out = pd.DataFrame(recs, columns=["game_id", "team", "player_id"]).drop_duplicates().reset_index(drop=True)
    return out, {"in": int(len(inactives)), "out": int(len(out)), "unmatched_game": bad_game, "unresolved_name": bad_name}
