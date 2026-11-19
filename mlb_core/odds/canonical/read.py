"""mlb_core.odds.canonical.read -- Derived Read Layer (BNK-12).

Spec Section 4:
  - load_day(league, date, root="") -> DataFrame
  - load_snapshot(source, date, hhmm=None, root="") -> DataFrame
  - load_quarantine(source, date, root="") -> DataFrame
  - load_report(source, date, root="") -> dict
  - filter_canonical(df, market=None, book_id=None, player_id=None) -> DataFrame
All reads go through mlb_core.storage.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
import pandas as pd

from mlb_core import storage
from mlb_core.odds.canonical.ingest import _prefix_key
from mlb_core.odds.canonical.schema import empty_frame


def load_day(league: str, date: str, root: str = "") -> pd.DataFrame:
    """Load canonical odds for a single league-day."""
    lg = league.strip().lower()
    dt = date.strip()[:10]
    key = _prefix_key(f"CanonicalOdds/{lg}/{dt}.parquet", root)
    if storage.exists(key):
        return storage.read_parquet(key)
    return empty_frame()


def load_snapshot(source: str, date: str, hhmm: Optional[str] = None, root: str = "") -> pd.DataFrame:
    """Load intraday snapshot(s) for a source and date.

    If hhmm is specified, loads that capture.
    If hhmm is None, loads and concatenates all captures for that date.
    """
    src = source.strip().lower()
    dt = date.strip()[:10]
    if hhmm:
        key = _prefix_key(f"CanonicalOdds/_snapshots/{src}/{dt}/{hhmm}.parquet", root)
        if storage.exists(key):
            return storage.read_parquet(key)
        return empty_frame()

    prefix = _prefix_key(f"CanonicalOdds/_snapshots/{src}/{dt}/", root)
    keys = storage.list_keys(prefix)
    frames: List[pd.DataFrame] = []
    for k in sorted(keys):
        if k.endswith(".parquet"):
            try:
                frames.append(storage.read_parquet(k))
            except Exception:
                continue
    if frames:
        return pd.concat(frames, ignore_index=True)
    return empty_frame()


def load_quarantine(source: str, date: str, root: str = "") -> pd.DataFrame:
    """Load quarantined rows for a source and date."""
    src = source.strip().lower()
    dt = date.strip()[:10]
    key = _prefix_key(f"CanonicalOdds/_quarantine/{src}/{dt}.parquet", root)
    if storage.exists(key):
        return storage.read_parquet(key)
    return pd.DataFrame()


def load_report(source: str, date: str, root: str = "") -> Dict[str, Any]:
    """Load ingest report JSON for a source and date."""
    src = source.strip().lower()
    dt = date.strip()[:10]
    key = _prefix_key(f"CanonicalOdds/_ingest_reports/{src}/{dt}.json", root)
    if storage.exists(key):
        raw = storage.read_bytes(key)
        return json.loads(raw.decode("utf-8"))
    return {}


# ---------------------------------------------------------------------------
# Named views (RUL-20261119-closeout-plan): the five surfaces consumers read.
# Views are functions over the stored layer, not separate stored copies.
# ---------------------------------------------------------------------------

def canonical_quotes(league: str, date: str, root: str = "") -> pd.DataFrame:
    """View: all canonical quote rows for a league-day (the day file)."""
    return load_day(league, date, root=root)


def canonical_futures(date: str, leagues: Optional[List[str]] = None, root: str = "") -> pd.DataFrame:
    """View: futures outcomes for a date — binary_contract rows across league
    day files (Kalshi futures partitions publish under their league)."""
    frames = []
    for lg in (leagues or ["nba", "nfl", "mlb", "nhl", "ncaaf", "ncaab", "wnba"]):
        df = load_day(lg, date, root=root)
        if len(df):
            frames.append(df[df["market_class"] == "binary_contract"])
    if not frames:
        return empty_frame()
    return pd.concat(frames, ignore_index=True)


def canonical_outcomes(league: str, date: str, root: str = "") -> pd.DataFrame:
    """View: distinct outcome identities for a league-day, keyed by the
    deterministic natural-key hash (outcomes.outcome_id)."""
    from mlb_core.odds.canonical.outcomes import build_outcomes
    return build_outcomes(load_day(league, date, root=root))


def canonical_prop_eval(league: str, date: str, root: str = "") -> pd.DataFrame:
    """View: the prop-evaluation surface — resolved prop quotes only (player
    identity present, prop market classes), the rows a screen may grade."""
    from mlb_core.odds.canonical.schema import PROP_MARKET_CLASSES
    df = load_day(league, date, root=root)
    if not len(df):
        return df
    return df[df["market_class"].isin(list(PROP_MARKET_CLASSES)) & df["player_id"].notna()].reset_index(drop=True)


def unmapped_quarantine(source: str, date: str, root: str = "") -> pd.DataFrame:
    """View: quarantine rows whose reject reason is the fail-closed venue
    outcome map miss (unmapped_outcome)."""
    q = load_quarantine(source, date, root=root)
    if not len(q) or "reject_reason" not in q.columns:
        return q.iloc[0:0]
    return q[q["reject_reason"] == "unmapped_outcome"].reset_index(drop=True)


def filter_canonical(
    df: pd.DataFrame,
    market: Optional[str] = None,
    book_id: Optional[str] = None,
    player_id: Optional[str] = None,
) -> pd.DataFrame:
    """Convenience filter over a canonical DataFrame."""
    if df is None or df.empty:
        return df

    out = df
    if market is not None:
        out = out[out["market"] == market]
    if book_id is not None:
        out = out[out["book_id"] == book_id]
    if player_id is not None:
        out = out[out["player_id"] == str(player_id)]
    return out.reset_index(drop=True)
