"""
mlb_core.storage v2 — transparent GCS / local file layer.

All data reads and writes in mlb_core go through these helpers so that
the same code works locally (plain files) and on GCP (GCS objects).

Usage:
    from mlb_core.storage import read_csv, write_csv, read_bytes, write_bytes

    df = read_csv("Statcast/statcast_master.csv")
    write_csv(df, "Statcast/statcast_master.csv")

When MLB_GCS_BUCKET is set the path is treated as a GCS object key inside
that bucket.  Otherwise it is joined onto MLB_BASE_DATA and treated as a
local file.
"""
import io
import os
from pathlib import Path
from typing import Optional

import pandas as pd

from mlb_core.config import BASE_DATA


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_bucket() -> str:
    """Re-read GCS bucket name from env at call time.

    Checks MLB_GCS_BUCKET first (Cloud Run), then GCS_BUCKET (Cloud Shell
    convenience alias).  Returns empty string in local-only mode.
    Re-reading at call time means `export GCS_BUCKET=...` takes effect
    without restarting Python.
    """
    return (os.environ.get("MLB_GCS_BUCKET", "")
            or os.environ.get("GCS_BUCKET", ""))


def _gcs_client():
    from google.cloud import storage as gcs
    return gcs.Client()


def _gcs_blob(key: str):
    client = _gcs_client()
    bucket = client.bucket(_get_bucket())
    return bucket.blob(key)


def _get_base_data() -> Path:
    """Re-read MLB_BASE_DATA from env at call time.

    Mirrors _get_bucket(): resolving at call time (instead of using the
    import-time BASE_DATA constant) means changing MLB_BASE_DATA after import
    takes effect, and avoids import-order-dependent path capture in tests.
    Falls back to the import-time BASE_DATA default when the env var is unset.
    """
    env = os.environ.get("MLB_BASE_DATA")
    return Path(env) if env else BASE_DATA


def _local_path(key: str) -> Path:
    """Resolve a key like 'Statcast/foo.csv' to an absolute local path."""
    p = Path(key)
    if p.is_absolute():
        return p
    return _get_base_data() / key


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_GCS_TIMEOUT = 600   # seconds -- statcast_master is ~300 MB; default 120s times out

def read_bytes(key: str) -> bytes:
    """Read raw bytes from GCS or local disk."""
    if _get_bucket():
        return _gcs_blob(key).download_as_bytes(timeout=_GCS_TIMEOUT)
    path = _local_path(key)
    return path.read_bytes()


def write_bytes(data: bytes, key: str) -> None:
    """Write raw bytes to GCS or local disk."""
    if _get_bucket():
        _gcs_blob(key).upload_from_string(data, timeout=_GCS_TIMEOUT)
        return
    path = _local_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# ---------------------------------------------------------------------------
# Parquet twins (cost/perf): the big masters are mirrored as .parquet next to
# the canonical .csv. read_csv() transparently prefers the twin (5-10x smaller
# download + faster parse); write_csv() keeps the twin fresh. The CSV stays
# authoritative for rollback -- delete the .parquet objects (or set
# MLB_PARQUET_TWIN=0) to revert instantly. One-time creation:
#     python3 scripts/migrate_masters_to_parquet.py
# ---------------------------------------------------------------------------

PARQUET_TWIN_KEYS = {
    "Statcast/statcast_master.csv",
    "Scoring/scoring_master.csv",
}


def _twin_key(key: str) -> str:
    return key[:-4] + ".parquet"


def _twin_enabled(key: str) -> bool:
    return (key in PARQUET_TWIN_KEYS
            and os.environ.get("MLB_PARQUET_TWIN", "1") != "0")


def read_parquet(key: str, columns=None) -> pd.DataFrame:
    """Read a Parquet object from GCS or local disk (requires pyarrow)."""
    if _get_bucket():
        raw = _gcs_blob(key).download_as_bytes(timeout=_GCS_TIMEOUT)
        return pd.read_parquet(io.BytesIO(raw), columns=columns)
    return pd.read_parquet(_local_path(key), columns=columns)


def write_parquet(df: pd.DataFrame, key: str) -> None:
    """Write a DataFrame as Parquet to GCS or local disk (requires pyarrow)."""
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    write_bytes(buf.getvalue(), key)


def read_csv(key: str, **kwargs) -> pd.DataFrame:
    """Read a CSV into a DataFrame from GCS or local disk.

    For keys in PARQUET_TWIN_KEYS, transparently reads the .parquet twin when
    it exists (much smaller/faster). `usecols` is honored (list or callable);
    other pandas CSV kwargs (low_memory, dtype, ...) are CSV-parse concerns
    and are ignored on the parquet path. Falls back to the CSV on any twin
    read failure.
    """
    if _twin_enabled(key):
        twin = _twin_key(key)
        try:
            if exists(twin):
                usecols = kwargs.get("usecols")
                columns = list(usecols) if isinstance(usecols, (list, tuple, set)) else None
                df = read_parquet(twin, columns=columns)
                if callable(usecols):
                    df = df[[c for c in df.columns if usecols(c)]]
                return df
        except Exception as e:  # noqa: BLE001 -- twin is an optimization only
            import logging
            logging.getLogger(__name__).warning(
                f"parquet twin read failed for {twin}: {e} -- falling back to CSV")
    if _get_bucket():
        raw = _gcs_blob(key).download_as_bytes(timeout=_GCS_TIMEOUT)
        return pd.read_csv(io.BytesIO(raw), **kwargs)
    path = _local_path(key)
    return pd.read_csv(path, **kwargs)


def write_csv(df: pd.DataFrame, key: str, index: bool = False) -> None:
    """Write a DataFrame as CSV to GCS or local disk.

    For keys in PARQUET_TWIN_KEYS, also refreshes the .parquet twin
    (best-effort -- a twin write failure never fails the CSV write)."""
    buf = df.to_csv(index=index).encode()
    if _get_bucket():
        _gcs_blob(key).upload_from_string(buf, content_type="text/csv", timeout=_GCS_TIMEOUT)
    else:
        path = _local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(buf)
    if _twin_enabled(key):
        try:
            write_parquet(df, _twin_key(key))
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                f"parquet twin write failed for {_twin_key(key)}: {e}")


def exists(key: str) -> bool:
    """Check whether a key exists in GCS or on local disk."""
    if _get_bucket():
        return _gcs_blob(key).exists()
    return _local_path(key).exists()


def list_keys(prefix: str) -> list[str]:
    """List all keys under a prefix in GCS, or file names in a local dir."""
    if _get_bucket():
        client = _gcs_client()
        blobs = client.list_blobs(_get_bucket(), prefix=prefix)
        return [b.name for b in blobs]
    directory = _local_path(prefix)
    if not directory.is_dir():
        return []
    # Resolve relative to the call-time base (matches _local_path / _get_base_data);
    # using the import-time BASE_DATA constant here was inconsistent and broke when
    # MLB_BASE_DATA changes after import.
    return [str(f.relative_to(_get_base_data())) for f in sorted(directory.iterdir())]


def delete(key: str) -> None:
    """Delete a key from GCS or local disk (silent if missing)."""
    if _get_bucket():
        blob = _gcs_blob(key)
        if blob.exists():
            blob.delete()
        return
    path = _local_path(key)
    if path.exists():
        path.unlink()


def download_model(gcs_key: str, local_path: Path) -> Path:
    """
    Download a model file from GCS to a local temp path.

    XGBoost requires a real file path, so model files always need to be
    materialised locally even in GCS mode.  Returns the local path.
    """
    local_path = Path(local_path)
    if _get_bucket():
        local_path.parent.mkdir(parents=True, exist_ok=True)
        raw = _gcs_blob(gcs_key).download_as_bytes(timeout=_GCS_TIMEOUT)
        local_path.write_bytes(raw)
    return local_path


def upload_model(local_path: Path, gcs_key: str) -> None:
    """Upload a local model file to GCS (no-op in local mode)."""
    if _get_bucket():
        _gcs_blob(gcs_key).upload_from_filename(str(local_path), timeout=_GCS_TIMEOUT)


def upload_file(local_path, key: str) -> None:
    """Upload an arbitrary local file to a GCS key, or copy it under BASE_DATA
    in local mode. Streams from disk (no full read into memory) so it is safe
    for large files (e.g. multi-hundred-MB CSVs)."""
    local_path = Path(local_path)
    if _get_bucket():
        _gcs_blob(key).upload_from_filename(str(local_path), timeout=_GCS_TIMEOUT)
        return
    import shutil
    dest = _local_path(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(local_path, dest)


def stat(key: str) -> dict | None:
    """Return {'mtime_utc': datetime, 'size': int} for a key, or None if missing."""
    if _get_bucket():
        blob = _gcs_blob(key)
        blob.reload()
        if not blob.exists():
            return None
        return {"mtime_utc": blob.updated, "size": blob.size}
    path = _local_path(key)
    if not path.exists():
        return None
    from datetime import datetime, timezone
    st = path.stat()
    return {"mtime_utc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
            "size": st.st_size}


def write_build_sentinel(system: str, result: dict) -> None:
    """Write a build sentinel JSON to GCS after a successful feature build.

    Key is read straight from mlb_core.registry.SYSTEMS[system].build_sentinel
    -- the single source of truth for where each system's sentinel lives
    (some systems share a sentinel with the builder they piggyback on, e.g.
    OUTS/PITCHER_ER -> K's, F1H -> F5's). Fixed 2026-09-04: this used to keep
    its own hand-maintained system-name -> GCS-prefix dict, which had already
    drifted out of sync with the registry (no entry at all for OUTS/
    PITCHER_ER/F1H/SB, falling back to a guessed f"{system}_Pro_System" that's
    wrong for the first three). No real caller was hitting the broken
    fallback -- every build_*_features.py passes a system name that was
    already in the old dict -- but reading the registry directly removes the
    second, divergence-prone copy rather than just patching the dict.
    """
    import json
    import logging
    from datetime import datetime, timezone
    from mlb_core.registry import SYSTEMS

    cfg = SYSTEMS.get(system.upper())
    if cfg is not None:
        key = cfg.build_sentinel
    else:
        logging.getLogger(__name__).warning(
            f"write_build_sentinel: {system!r} not in registry.SYSTEMS -- "
            f"guessing GCS key"
        )
        key = f"{system.upper()}_Pro_System/data/last_build.json"
    payload = {
        "system":    system.upper(),
        "status":    result.get("status", "ok"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rows":      result.get("rows", 0),
    }
    try:
        write_bytes(json.dumps(payload).encode(), key)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"write_build_sentinel failed for {system}: {e}")


def check_build_sentinel(gcs_bucket, system_prefix, max_age_hours=26):
    """
    Read {system_prefix}/data/last_build.json from GCS.
    Returns (ok: bool, reason: str).
    ok=False means the runner should abort and alert.
    Non-fatal on GCS read errors -- returns ok=True with a warning reason
    so a transient GCS blip does not block betting.
    """
    import json, datetime, logging
    logger = logging.getLogger(__name__)
    key = f"{system_prefix}/data/last_build.json"
    try:
        raw = read_bytes(key)
        sentinel = json.loads(raw)
    except Exception as exc:
        logger.warning("sentinel check: could not read %s -- %s", key, exc)
        return True, f"sentinel unreadable ({exc})"
    status = sentinel.get("status", "")
    if status not in ("ok", "success"):
        return False, f"last build status={status!r}"
    ts_str = sentinel.get("timestamp", "")
    try:
        ts = datetime.datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        age_h = (datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds() / 3600
    except Exception:
        return False, f"sentinel timestamp unparseable: {ts_str!r}"
    if age_h > max_age_hours:
        return False, f"sentinel age {age_h:.1f}h > {max_age_hours}h limit"
    return True, f"ok (age {age_h:.1f}h)"
