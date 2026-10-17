"""Read mirrored nflverse datasets from the shared bucket (or the local data dir) as pandas.

    from nfl.data.read import load
    stats = load("player_stats", seasons=[2024, 2025], columns=["player_id", "season", "week", "passing_yards"])
    games = load("schedules")

Explicitly requested seasons that are not mirrored RAISE (strict=True, the default): silently returning a
partial history is exactly how a backtest ends up trained on a hole. Pass strict=False to skip them.
"""
import logging
import re
from typing import Iterable, Optional

import pandas as pd

from mlb_core import storage
from nfl.config import DATASETS, NFLVERSE_PREFIX

logger = logging.getLogger(__name__)

_SEASON_RE = re.compile(r"_(\d{4})\.parquet$")


def mirrored_seasons(dataset: str) -> list:
    """Seasons currently mirrored for a per-season dataset, ascending."""
    ds = _dataset(dataset)
    if not ds.per_season:
        return []
    keys = storage.list_keys(f"{NFLVERSE_PREFIX}/{ds.name}/")
    return sorted(int(m.group(1)) for k in keys if (m := _SEASON_RE.search(k)))


def _dataset(name: str):
    try:
        return DATASETS[name]
    except KeyError:
        raise ValueError(f"unknown NFL dataset {name!r}; known: {sorted(DATASETS)}") from None


def load(dataset: str, seasons: Optional[Iterable[int]] = None, columns: Optional[list] = None,
         strict: bool = True) -> pd.DataFrame:
    """Load a mirrored dataset. seasons=None means every mirrored season (single-file datasets ignore it)."""
    ds = _dataset(dataset)
    if not ds.per_season:
        return storage.read_parquet(ds.key(), columns=columns)

    have = mirrored_seasons(dataset)
    want = sorted(set(seasons)) if seasons is not None else have
    missing = [s for s in want if s not in have]
    if missing and strict:
        raise FileNotFoundError(
            f"NFL dataset {dataset!r} has no mirrored file for season(s) {missing} (have {have}). "
            f"Backfill with: python3 -m nfl.data.ingest --mode backfill --datasets {dataset} "
            f"--seasons {missing[0]}-{missing[-1]}")
    if missing:
        logger.warning("%s: skipping unmirrored season(s) %s", dataset, missing)
    frames = [storage.read_parquet(ds.key(s), columns=columns) for s in want if s in have]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns or [])


# -- derived (gold) tables built by nfl.data.derived ------------------------------------------------------------
def _derived_prefix(table: str) -> str:
    from nfl.data.derived import DERIVED_PREFIX, PLAYER_TABLE, TEAM_TABLE
    if table not in (TEAM_TABLE, PLAYER_TABLE):
        raise ValueError(f"unknown derived table {table!r}; known: {[TEAM_TABLE, PLAYER_TABLE]}")
    return f"{DERIVED_PREFIX}/{table}"


def derived_seasons(table: str) -> list:
    """Seasons for which a derived table exists, ascending."""
    keys = storage.list_keys(f"{_derived_prefix(table)}/")
    return sorted(int(m.group(1)) for k in keys if (m := _SEASON_RE.search(k)))


def load_derived(table: str, seasons: Optional[Iterable[int]] = None, columns: Optional[list] = None,
                 strict: bool = True) -> pd.DataFrame:
    """Load derived table(s) (team_game_pbp / player_game_pbp). Same strictness contract as `load`."""
    prefix = _derived_prefix(table)
    have = derived_seasons(table)
    want = sorted(set(seasons)) if seasons is not None else have
    missing = [s for s in want if s not in have]
    if missing and strict:
        raise FileNotFoundError(
            f"derived table {table!r} has no file for season(s) {missing} (have {have}). "
            f"Build with: python3 -m nfl.data.derived --seasons {missing[0]}-{missing[-1]}")
    frames = [storage.read_parquet(f"{prefix}/{table}_{s}.parquet", columns=columns) for s in want if s in have]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns or [])
