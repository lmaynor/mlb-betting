"""Leakage 'garble' test for walk-forward replay loops.

A replay is leak-free only if the prediction for a game does not depend on anything that happens at or after its kickoff. The
test: take a cut-off day T, SCRAMBLE every post-kickoff field (final scores, box / play-by-play statistics) of every game on or
after T -- including the games that are being predicted -- re-run the replay, and require the predictions for every game on or
before T to be bit-for-bit identical. A POWER check requires predictions AFTER T to change (otherwise the scramble did nothing and
the test proves nothing). Identifier / pre-game columns (ids, dates, teams, closing lines) are never touched.
"""
from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np
import pandas as pd


def garble_columns(df: pd.DataFrame, cols: Iterable[str], mask: Sequence[bool], seed: int = 0) -> pd.DataFrame:
    """Permute each listed column's values among the masked rows (independently per column, so row-level structure -- e.g. a
    score matching its own play -- is destroyed while marginal distributions survive). Unmasked rows are untouched."""
    out = df.copy()
    idx = np.flatnonzero(np.asarray(mask, dtype=bool))
    if len(idx) < 2:
        return out
    rng = np.random.default_rng(seed)
    for c in cols:
        if c not in out.columns:
            continue
        vals = out[c].to_numpy(copy=True)
        vals[idx] = vals[rng.permutation(idx)]
        out[c] = vals
    return out


def prediction_diff(a: pd.DataFrame, b: pd.DataFrame, key: str, cols: Sequence[str], date_col: str, upto: str,
                    after: bool = False) -> pd.DataFrame:
    """Rows (by `key`) whose any `cols` value differs between the two runs, restricted to games with date <= upto (or > upto when
    after=True, used by the power check). NaN == NaN. Games present in only one run count as differences."""
    m = a.merge(b, on=key, how="outer", suffixes=("_a", "_b"), indicator=True)
    dates = m[f"{date_col}_a"].fillna(m[f"{date_col}_b"]).astype(str)
    m = m[(dates > upto) if after else (dates <= upto)]
    bad = m["_merge"] != "both"
    for c in cols:
        x, y = m[f"{c}_a"].astype(float), m[f"{c}_b"].astype(float)
        bad = bad | ~((x == y) | (x.isna() & y.isna()))
    return m[bad]


def summarize(before: pd.DataFrame, after: pd.DataFrame, n_checked: int) -> dict:
    """One cut-off: leak = any pre-cutoff prediction changed; power_ok = some post-cutoff prediction changed."""
    return {"n_checked_before": int(n_checked), "leak": len(before) > 0, "n_changed_before": int(len(before)),
            "power_ok": len(after) > 0, "n_changed_after": int(len(after))}
