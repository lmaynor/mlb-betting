"""One-shot (or periodic) Kaggle -> GCS ingest of the eoinamoore historical
NBA dataset (stats.nba.com lineage: games, team/player box scores, schedule,
play-by-play; 1947-today; no odds).

Downloads the full dataset via kagglehub and mirrors every file verbatim into
GCS under NBA/stats_nba/raw/<relpath>, then writes a manifest sentinel. Intended
as a slow, gentle overnight batch -- latency does not matter.

Auth: kagglehub reads KAGGLE_USERNAME + KAGGLE_KEY from the environment (injected
from Secret Manager in the Cloud Run Job). Create a token at
kaggle.com/settings -> "Create New Token".

Run:
    GCS_BUCKET=concrete-crow-445205-m4-mlb-data \
    KAGGLE_USERNAME=... KAGGLE_KEY=... \
        python3 -m nba.data.kaggle_ingest

Memory note: kagglehub caches to a memory-backed filesystem on Cloud Run, so the
job's memory must exceed the dataset size. Provision generously (see
deploy/setup_nba_kaggle_ingest.sh). If the dataset outgrows memory, switch to the
per-file path documented there.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from mlb_core import storage
from nba.config import (KAGGLE_DATASET, STATS_NBA_INGEST_SENTINEL,
                        stats_nba_raw_key)

logger = logging.getLogger(__name__)


def _default_download(handle: str) -> str:
    """Download the full dataset and return the local cache directory."""
    import kagglehub  # lazy import so the module loads without kagglehub present
    return kagglehub.dataset_download(handle)


def run(handle: str = KAGGLE_DATASET, download_fn=None) -> dict:
    download_fn = download_fn or _default_download
    logger.info("downloading Kaggle dataset %s ...", handle)
    local_dir = Path(download_fn(handle))
    logger.info("downloaded to %s", local_dir)

    files = sorted(p for p in local_dir.rglob("*") if p.is_file())
    if not files:
        raise RuntimeError(f"no files found under {local_dir}")

    manifest = []
    total_bytes = 0
    for p in files:
        rel = p.relative_to(local_dir).as_posix()
        key = stats_nba_raw_key(rel)
        size = p.stat().st_size
        storage.upload_file(p, key)
        manifest.append({"file": rel, "key": key, "bytes": size})
        total_bytes += size
        logger.info("uploaded %s (%.1f MB) -> %s", rel, size / 1e6, key)

    sentinel = {
        "status": "ok",
        "dataset": handle,
        "files": len(manifest),
        "total_bytes": total_bytes,
        "manifest": manifest,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    storage.write_bytes(json.dumps(sentinel, indent=1).encode(), STATS_NBA_INGEST_SENTINEL)
    logger.info("ingest complete: %d files, %.1f MB total",
                len(manifest), total_bytes / 1e6)
    return {k: sentinel[k] for k in ("status", "dataset", "files", "total_bytes")}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not (os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY")):
        logger.warning("KAGGLE_USERNAME / KAGGLE_KEY not set -- kagglehub auth will "
                       "likely fail. Inject them from Secret Manager.")
    result = run()
    logger.info("done: %s", result)


if __name__ == "__main__":
    main()
