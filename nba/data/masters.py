"""Read/merge/write the three NBA master CSVs via mlb_core.storage.

upsert() merges new rows into an existing master, deduping on the grain's key
columns and keeping the latest copy on conflict (so corrections overwrite).
Used by both the one-time backfill and the nightly refresh.
"""
import logging

import pandas as pd

from mlb_core import storage
from nba.config import GAMES_MASTER, PLAYER_BOX_MASTER, TEAM_BOX_MASTER

logger = logging.getLogger(__name__)

_KEYS = {
    GAMES_MASTER: ["game_id"],
    TEAM_BOX_MASTER: ["game_id", "team_id"],
    PLAYER_BOX_MASTER: ["game_id", "player_id"],
}


def _load(key: str) -> pd.DataFrame:
    if storage.exists(key):
        try:
            return storage.read_csv(key)
        except Exception as exc:
            logger.warning("master unreadable at %s (%s); treating as empty", key, exc)
    return pd.DataFrame()


def upsert(key: str, new_rows) -> int:
    """Merge new_rows (list of dict or DataFrame) into the master at key.

    Returns the net number of rows added (new uniques). A correction to an
    existing row does not change the row count but updates its values.
    """
    new_df = new_rows if isinstance(new_rows, pd.DataFrame) else pd.DataFrame(new_rows)
    if new_df.empty:
        return 0
    existing = _load(key)
    before = len(existing)
    subset = _KEYS[key]
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=subset, keep="last").reset_index(drop=True)
    storage.write_csv(combined, key)
    return len(combined) - before
