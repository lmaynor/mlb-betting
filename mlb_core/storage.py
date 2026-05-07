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

from mlb_core.config import GCS_BUCKET, BASE_DATA


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _gcs_client():
    from google.cloud import storage as gcs
    return gcs.Client()


def _gcs_blob(key: str):
    client = _gcs_client()
    bucket = client.bucket(GCS_BUCKET)
    return bucket.blob(key)


def _local_path(key: str) -> Path:
    """Resolve a key like 'Statcast/foo.csv' to an absolute local path."""
    p = Path(key)
    if p.is_absolute():
        return p
    return BASE_DATA / key


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_bytes(key: str) -> bytes:
    """Read raw bytes from GCS or local disk."""
    if GCS_BUCKET:
        return _gcs_blob(key).download_as_bytes()
    path = _local_path(key)
    return path.read_bytes()


def write_bytes(data: bytes, key: str) -> None:
    """Write raw bytes to GCS or local disk."""
    if GCS_BUCKET:
        _gcs_blob(key).upload_from_string(data)
        return
    path = _local_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def read_csv(key: str, **kwargs) -> pd.DataFrame:
    """Read a CSV into a DataFrame from GCS or local disk."""
    if GCS_BUCKET:
        raw = _gcs_blob(key).download_as_bytes()
        return pd.read_csv(io.BytesIO(raw), **kwargs)
    path = _local_path(key)
    return pd.read_csv(path, **kwargs)


def write_csv(df: pd.DataFrame, key: str, index: bool = False) -> None:
    """Write a DataFrame as CSV to GCS or local disk."""
    buf = df.to_csv(index=index).encode()
    if GCS_BUCKET:
        _gcs_blob(key).upload_from_string(buf, content_type="text/csv")
        return
    path = _local_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf)


def exists(key: str) -> bool:
    """Check whether a key exists in GCS or on local disk."""
    if GCS_BUCKET:
        return _gcs_blob(key).exists()
    return _local_path(key).exists()


def list_keys(prefix: str) -> list[str]:
    """List all keys under a prefix in GCS, or file names in a local dir."""
    if GCS_BUCKET:
        client = _gcs_client()
        blobs = client.list_blobs(GCS_BUCKET, prefix=prefix)
        return [b.name for b in blobs]
    directory = _local_path(prefix)
    if not directory.is_dir():
        return []
    return [str(f.relative_to(BASE_DATA)) for f in sorted(directory.iterdir())]


def delete(key: str) -> None:
    """Delete a key from GCS or local disk (silent if missing)."""
    if GCS_BUCKET:
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
    if GCS_BUCKET:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        raw = _gcs_blob(gcs_key).download_as_bytes()
        local_path.write_bytes(raw)
    return local_path


def upload_model(local_path: Path, gcs_key: str) -> None:
    """Upload a local model file to GCS (no-op in local mode)."""
    if GCS_BUCKET:
        _gcs_blob(gcs_key).upload_from_filename(str(local_path))
