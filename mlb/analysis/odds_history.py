"""
mlb.analysis.odds_history -- the odds_history analytics store (roadmap P0.1).

Normalized historical-odds store for backtesting: one row per
(selection, book, snapshot). Parquet in GCS, partitioned `market` then `date`:

    Odds/history/market=<market>/date=<YYYY-MM-DD>/part-0.parquet

This is the ANALYTICS store (distinct from the live `bets` Postgres table).
Both the SGO snapshot ETL (P0.2) and the BettingPros loader (P0.3,
bettingpros_to_parquet.py) write into this same schema.

Reads/writes go through mlb_core.storage so it works against GCS (when
MLB_GCS_BUCKET/GCS_BUCKET is set) or local files. Requires pyarrow (pandas
to_parquet/read_parquet backend) -- see requirements.txt.

See roadmap_2026-06-29_cross_system_odds_and_roi.md section 3 for the schema
rationale and the coverage-gating discipline ("never let a backtest silently
run on thin data").
"""

from __future__ import annotations

import io
import json

# Schema -- one row per (selection, book, snapshot). Roadmap section 3.
SCHEMA_COLUMNS = [
    "sport",          # "mlb" (NBA later)
    "market",         # canonical: hr_yn, nrfi_ou, k_ou, game_ml, ...
    "system",         # registry key (HR, 1IOU, ...) or "" if not a system market
    "game_pk",        # int, nullable -- resolved via team/schedule bridge
    "game_date",      # YYYY-MM-DD (ET game day); PARTITION KEY
    "event_id",       # source event id (SGO eventID / BettingPros id)
    "away_team",      # canonical 3-letter
    "home_team",      # canonical 3-letter
    "player_id",      # MLBAM id for props, nullable
    "selection",      # OVER/UNDER/YES/NO/HOME/AWAY/NRFI/YRFI
    "line",           # O/U or spread line; NULL for ML/yn
    "book",           # canonical onshore (draftkings, fanduel, consensus, ...)
    "american",       # American odds (int)
    "decimal",        # derived
    "implied_prob",   # vig-inclusive
    "fair_prob",      # de-vigged where the pair exists; NULL otherwise
    "snapshot_ts",    # "YYYY-MM-DD HH:MM:SS"
    "is_open",        # bool -- first/opening snapshot for this market/selection
    "is_closing",     # bool -- the closing (pregame) snapshot
    "source",         # "sgo" | "bettingpros"
    "ingested_at",    # passed in via args (Date.now unavailable in some envs)
]

# De-dup identity for a single quote. Source precedence on overlap is the
# caller's job (SGO for 2026+ closing; BettingPros for history).
DEDUP_KEYS = ["market", "game_pk", "selection", "line", "book", "snapshot_ts", "source"]

HISTORY_PREFIX = "Odds/history"
COVERAGE_PREFIX = "Odds/history/_coverage"


def partition_path(market: str, game_date: str) -> str:
    return f"{HISTORY_PREFIX}/market={market}/date={game_date}/part-0.parquet"


def _to_parquet_bytes(df) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)  # requires pyarrow
    return buf.getvalue()


def write_partition(df, market: str, game_date: str, append: bool = False) -> int:
    """Write one (market, date) partition. Reorders to SCHEMA_COLUMNS, fills
    missing columns, de-dups on DEDUP_KEYS. Returns rows written. No-op for
    an empty frame.

    append=False (default): OVERWRITE the partition -- used by the historical
      re-ingest to REPLACE corrupt rows.
    append=True: MERGE with the existing partition, then de-dup on DEDUP_KEYS
      (which includes snapshot_ts) -- used by the intraday tracker so multiple
      snapshots/day ACCUMULATE instead of clobbering each other. Idempotent:
      re-writing the same snapshot dedups to one row."""
    import pandas as pd
    from mlb_core import storage

    if df is None or len(df) == 0:
        return 0
    df = df.copy()
    for col in SCHEMA_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[SCHEMA_COLUMNS]
    if append:
        try:
            raw = storage.read_bytes(partition_path(market, game_date))
            existing = pd.read_parquet(io.BytesIO(raw))
            df = pd.concat([existing, df], ignore_index=True)
        except Exception:  # noqa: BLE001 -- no existing partition yet
            pass
    df = df.drop_duplicates(subset=DEDUP_KEYS, keep="last")
    storage.write_bytes(_to_parquet_bytes(df), partition_path(market, game_date))
    return len(df)


def _list_partition_dates(market: str) -> list:
    """Dates present for a market, parsed from partition key paths (cheap)."""
    from mlb_core import storage
    dates = set()
    for key in storage.list_keys(f"{HISTORY_PREFIX}/market={market}/"):
        # .../market=<m>/date=<YYYY-MM-DD>/part-0.parquet
        for part in key.split("/"):
            if part.startswith("date="):
                dates.add(part[len("date="):])
    return sorted(dates)


def read_history(market: str, since: str | None = None, until: str | None = None):
    """Read a market's partitions into one DataFrame, optional date filter."""
    import pandas as pd
    from mlb_core import storage

    dates = _list_partition_dates(market)
    if since:
        dates = [d for d in dates if d >= since]
    if until:
        dates = [d for d in dates if d <= until]
    frames = []
    for d in dates:
        try:
            raw = storage.read_bytes(partition_path(market, d))
            frames.append(pd.read_parquet(io.BytesIO(raw)))
        except Exception:  # noqa: BLE001 -- skip a missing/corrupt partition
            continue
    if not frames:
        return pd.DataFrame(columns=SCHEMA_COLUMNS)
    return pd.concat(frames, ignore_index=True)


# Source precedence when the same quote exists from >1 provider (e.g. SGO + ParlayAPI
# for a 2026 game). Backtests should read ONE line per quote-key; pick the preferred
# source. Default: live providers (parlayapi > sgo) over the historical scrape.
SOURCE_PRECEDENCE = ("parlayapi", "sgo", "bettingpros")
_QUOTE_KEY = ["market", "game_pk", "selection", "line", "book", "snapshot_ts"]


def dedupe_by_source(df, precedence: tuple = SOURCE_PRECEDENCE):
    """Collapse cross-source duplicates of the same quote, keeping the highest-
    precedence source. No-op if `source` is absent or df is empty."""
    if df is None or len(df) == 0 or "source" not in df.columns:
        return df
    rank = {s: i for i, s in enumerate(precedence)}
    keys = [c for c in _QUOTE_KEY if c in df.columns]
    df = df.copy()
    df["_rank"] = df["source"].map(lambda s: rank.get(s, len(precedence)))
    df = (df.sort_values("_rank")
            .drop_duplicates(subset=keys, keep="first")
            .drop(columns="_rank"))
    return df


def coverage_report(market: str, write: bool = True) -> dict:
    """Per-season partition (date) counts for a market, plus first/last.

    Cheap: derived from partition listing, not by reading parquet. Writes
    Odds/history/_coverage/{market}.json when write=True so backtests can gate
    on coverage without scanning data.
    """
    from mlb_core import storage

    dates = _list_partition_dates(market)
    per_season: dict = {}
    for d in dates:
        yr = d[:4]
        per_season[yr] = per_season.get(yr, 0) + 1
    report = {
        "market": market,
        "n_dates": len(dates),
        "first": dates[0] if dates else None,
        "last": dates[-1] if dates else None,
        "per_season": per_season,
    }
    if write and dates:
        storage.write_bytes(json.dumps(report, indent=2).encode(),
                            f"{COVERAGE_PREFIX}/{market}.json")
    return report
