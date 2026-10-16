"""Loaders for the NHL masters + shots. `data_dir` set -> read local copies (`{data_dir}/NHL/*.csv`, `.../NHL/shots/`);
unset -> mlb_core.storage (GCS when GCS_BUCKET is set). Read-only: nothing here writes anywhere."""
from __future__ import annotations

import os

import pandas as pd

from nhl.config import GAMES_MASTER, GOALIE_BOX_MASTER, SKATER_BOX_MASTER, TEAM_BOX_MASTER

SEASONS = range(2019, 2027)


def _read(key: str, data_dir: str | None, **kw) -> pd.DataFrame | None:
    if data_dir:
        path = os.path.join(data_dir, key)
        return pd.read_csv(path, **kw) if os.path.exists(path) else None
    from mlb_core import storage
    return storage.read_csv(key, **kw) if storage.exists(key) else None


def load_shots(data_dir: str | None = None, seasons=SEASONS) -> pd.DataFrame:
    frames = [f for y in seasons if (f := _read(f"NHL/shots/shots_{y}.csv", data_dir, low_memory=False)) is not None]
    if not frames:
        raise FileNotFoundError("no NHL/shots/shots_*.csv found")
    return pd.concat(frames, ignore_index=True)


def load_masters(data_dir: str | None = None) -> dict:
    out = {}
    for name, key in (("games", GAMES_MASTER), ("skaters", SKATER_BOX_MASTER), ("goalies", GOALIE_BOX_MASTER),
                      ("teams", TEAM_BOX_MASTER)):
        df = _read(key, data_dir, low_memory=False)
        if df is None:
            raise FileNotFoundError(key)
        out[name] = df
    return out
