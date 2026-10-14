"""Read/merge/write the NHL master CSVs via mlb_core.storage.

upsert() merges new rows into an existing master, deduping on the grain's key columns and
keeping the latest copy on conflict (so scorekeeper corrections overwrite). Used by the
backfill, the nightly refresh, and the players/teams reference refreshes
(`upsert(key, rows)` -- key columns come from nhl.config.MASTER_KEYS, which includes
PLAYERS_MASTER and TEAMS_MASTER).
"""
import logging

import pandas as pd

from mlb_core import storage
from nhl.data.settings import MASTER_KEYS, SHOTS_KEY_COLS

logger = logging.getLogger(__name__)


def _load(key: str) -> pd.DataFrame:
    if storage.exists(key):
        try:
            return storage.read_csv(key)
        except Exception as exc:
            # Treating an unreadable master as empty would OVERWRITE it with only the new rows;
            # fail loudly instead -- unlike a cache, this is the accumulated source of truth.
            raise RuntimeError(f"master unreadable at {key}: {exc}") from exc
    return pd.DataFrame()


def upsert(key: str, new_rows, subset=None) -> int:
    """Merge new_rows (list of dict or DataFrame) into the master at key.

    Returns the net number of rows added (new uniques). A correction to an existing row does not
    change the row count but updates its values.
    """
    new_df = new_rows if isinstance(new_rows, pd.DataFrame) else pd.DataFrame(new_rows)
    if new_df.empty:
        return 0
    subset = subset or MASTER_KEYS[key]
    existing = _load(key)
    before = len(existing)
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=subset, keep="last").reset_index(drop=True)
    storage.write_csv(combined, key)
    return len(combined) - before


def upsert_shots(shots_key_fn, shots_by_season: dict) -> int:
    """Upsert per-season shots masters. shots_by_season: {season_year: [rows]}."""
    added = 0
    for syear, rows in shots_by_season.items():
        added += upsert(shots_key_fn(syear), rows, subset=SHOTS_KEY_COLS)
    return added
