"""mlb_core.odds.canonical.ingest -- Canonical Ingest Publisher (BNK-12).

Spec Section 4 & 5.5:
  - Writes CanonicalOdds/{league}/{date}.parquet (day sources)
  - Or CanonicalOdds/_snapshots/{source}/{date}/{HHMM}.parquet (OddsAccum)
  - CanonicalOdds/_quarantine/{source}/{date}.parquet
  - CanonicalOdds/_ingest_reports/{source}/{date}.json
  - Idempotent: returns {"status": "already_published"} if input fingerprint matches.
  - write_registry(root) publishes registry artifacts as parquets under CanonicalOdds/_registry/.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
import pandas as pd

from mlb_core import storage
from mlb_core.odds.canonical.registry import (
    DEFAULT_REGISTRY_DIR,
    load_books,
    load_markets,
    load_player_aliases,
    load_teams,
)
from mlb_core.odds.canonical.schema import (
    CANONICAL_COLUMNS,
    empty_frame,
    validate_published_frame,
)

logger = logging.getLogger(__name__)


def _prefix_key(key: str, root: str = "") -> str:
    r = root.strip().rstrip("/")
    if r:
        return f"{r}/{key.lstrip('/')}"
    return key


def compute_frame_fingerprint(df: pd.DataFrame) -> str:
    """Compute deterministic hash of a DataFrame's contents."""
    if df is None or df.empty:
        return "empty_0"
    h = hashlib.sha256()
    h.update(str(len(df)).encode())
    for col in df.columns:
        h.update(col.encode())
    # Sample and length hash
    val_str = df.head(100).to_csv(index=False)
    h.update(val_str.encode("utf-8", "replace"))
    return f"{len(df)}_{h.hexdigest()[:16]}"


def compute_input_fingerprint(canonical_df: pd.DataFrame, quarantine_df: pd.DataFrame) -> str:
    c_fp = compute_frame_fingerprint(canonical_df)
    q_fp = compute_frame_fingerprint(quarantine_df)
    return f"c:{c_fp}__q:{q_fp}"


def publish(
    source: str,
    league_or_sport: str,
    date: str,
    canonical_df: pd.DataFrame,
    quarantine_df: pd.DataFrame,
    report: Dict[str, Any],
    root: str = "",
    force: bool = False,
    hhmm: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish canonical odds, quarantine rows, and ingest report.

    Idempotent: if the ingest report already exists with the same input fingerprint,
    returns {"status": "already_published"} without re-writing.
    """
    src = source.strip().lower()
    lg = league_or_sport.strip().lower()
    dt = date.strip()[:10]

    # Validate published frame invariants (spec 5.5 item 5)
    validate_published_frame(canonical_df)

    # Determine storage keys
    if src == "oddsaccum":
        snap_hhmm = hhmm or report.get("hhmm") or "0000"
        canonical_key = _prefix_key(f"CanonicalOdds/_snapshots/{src}/{dt}/{snap_hhmm}.parquet", root)
        report_key = _prefix_key(f"CanonicalOdds/_ingest_reports/{src}/{dt}_{snap_hhmm}.json", root)
        quarantine_key = _prefix_key(f"CanonicalOdds/_quarantine/{src}/{dt}_{snap_hhmm}.parquet", root)
    else:
        canonical_key = _prefix_key(f"CanonicalOdds/{lg}/{dt}.parquet", root)
        report_key = _prefix_key(f"CanonicalOdds/_ingest_reports/{src}/{dt}.json", root)
        quarantine_key = _prefix_key(f"CanonicalOdds/_quarantine/{src}/{dt}.parquet", root)

    fingerprint = compute_input_fingerprint(canonical_df, quarantine_df)

    # Check idempotency against existing ingest report
    if storage.exists(report_key) and not force:
        try:
            raw_bytes = storage.read_bytes(report_key)
            existing_report = json.loads(raw_bytes.decode("utf-8"))
            if existing_report.get("input_fingerprint") == fingerprint:
                return {
                    "status": "already_published",
                    "ingest_run_id": existing_report.get("ingest_run_id"),
                    "canonical_key": canonical_key,
                    "quarantine_key": quarantine_key,
                    "report_key": report_key,
                }
        except Exception as exc:
            logger.warning("Could not parse existing ingest report at %s: %s; proceeding to publish", report_key, exc)

    # Superseding or new publish run
    run_id = f"run_{src}_{dt}_{uuid.uuid4().hex[:8]}"
    rep_copy = dict(report)
    rep_copy["ingest_run_id"] = run_id
    rep_copy["input_fingerprint"] = fingerprint
    rep_copy["source"] = src
    rep_copy["league"] = lg
    rep_copy["date"] = dt
    rep_copy["canonical_rows"] = len(canonical_df) if canonical_df is not None else 0
    rep_copy["quarantined_rows"] = len(quarantine_df) if quarantine_df is not None else 0

    # Ensure canonical_df has exact columns and order
    if canonical_df is not None and not canonical_df.empty:
        c_to_write = canonical_df.reindex(columns=CANONICAL_COLUMNS)
    else:
        c_to_write = empty_frame()

    # Write canonical parquet
    storage.write_parquet(c_to_write, canonical_key)

    # Write quarantine parquet if quarantine rows exist
    if quarantine_df is not None and not quarantine_df.empty:
        storage.write_parquet(quarantine_df, quarantine_key)

    # Write ingest report JSON
    rep_bytes = json.dumps(rep_copy, indent=2, default=str).encode("utf-8")
    storage.write_bytes(rep_bytes, report_key)

    # Two-tier watermark (RUL-20261119 D3 amendment): every successful publish
    # is watermarked so Tier-2 historical translation walks only partitions
    # that have never been translated — zero re-translation.
    try:
        record_watermark(src, lg, dt, root=root, ingest_run_id=run_id)
    except Exception as exc:  # watermark must never fail a publish
        logger.warning("Watermark record failed for %s/%s/%s: %s", src, lg, dt, exc)

    return {
        "status": "published",
        "ingest_run_id": run_id,
        "canonical_key": canonical_key,
        "quarantine_key": quarantine_key,
        "report_key": report_key,
    }


WATERMARK_KEY = "CanonicalOdds/_ingest_reports/_watermark.json"
TIER1_FLOOR = "2026-09-01"


def load_watermark(root: str = "") -> Dict[str, Any]:
    """The translation watermark: {(source|scope|date): {ingest_run_id, recorded_at}}."""
    key = _prefix_key(WATERMARK_KEY, root)
    if storage.exists(key):
        try:
            return json.loads(storage.read_bytes(key).decode("utf-8"))
        except Exception:
            return {"entries": {}}
    return {"entries": {}}


def record_watermark(source: str, scope: str, date: str, root: str = "",
                     ingest_run_id: Optional[str] = None) -> None:
    wm = load_watermark(root)
    wm.setdefault("entries", {})[f"{source}|{scope}|{date}"] = {
        "ingest_run_id": ingest_run_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    storage.write_bytes(json.dumps(wm, indent=1).encode("utf-8"), _prefix_key(WATERMARK_KEY, root))


def watermarked(source: str, scope: str, date: str, root: str = "") -> bool:
    return f"{source}|{scope}|{date}" in load_watermark(root).get("entries", {})


def write_registry(root: str = "", registry_dir: Optional[Path | str] = None) -> Dict[str, str]:
    """Publish registry artifacts as Parquet under CanonicalOdds/_registry/."""
    rdir = Path(registry_dir) if registry_dir else DEFAULT_REGISTRY_DIR
    published_keys: Dict[str, str] = {}

    # Books
    books_df = load_books(rdir)
    k_books = _prefix_key("CanonicalOdds/_registry/books.parquet", root)
    storage.write_parquet(books_df, k_books)
    published_keys["books"] = k_books

    # Markets
    markets_df = load_markets(rdir)
    k_markets = _prefix_key("CanonicalOdds/_registry/markets.parquet", root)
    storage.write_parquet(markets_df, k_markets)
    published_keys["markets"] = k_markets

    # Teams
    teams_df = load_teams(rdir)
    k_teams = _prefix_key("CanonicalOdds/_registry/teams.parquet", root)
    storage.write_parquet(teams_df, k_teams)
    published_keys["teams"] = k_teams

    # Player aliases
    for lg in ["nba", "nhl", "nfl", "mlb"]:
        alias_df = load_player_aliases(lg, rdir)
        k_alias = _prefix_key(f"CanonicalOdds/_registry/player_aliases/{lg}.parquet", root)
        storage.write_parquet(alias_df, k_alias)
        published_keys[f"player_aliases_{lg}"] = k_alias

    return published_keys
