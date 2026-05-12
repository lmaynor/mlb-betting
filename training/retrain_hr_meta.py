"""
training/retrain_hr_meta.py — Patch HR model meta with feature_means.

The v6 model artifact arrived without a `feature_means` field in
model_meta_hr_v6.json. The HR runner uses feature_means to fill NaN at
predict time so missing features (live weather, missing pitcher rows,
unposted lineups) inherit the training-set mean rather than NaN.
Without it, predictions degrade silently on partial inputs.

This script does NOT retrain the model. It only patches the meta by
computing per-feature means from the production feature table.

Pattern (matches F5 retrain + Odds/sgo/latest.json):
  - HR_Pro/models/model_meta_hr_v6.json
        ← the "latest" pointer; what the runner reads. Overwritten each run.
  - HR_Pro/models/archive/model_meta_hr_v6.{timestamp}.json
        ← timestamped archive; never overwritten. For inspection / rollback.

HR-specific note: the runner (runners/run_hr.py::_load_model) falls back to
`booster.feature_names` if the meta is missing or has no 'features' key.
This script REQUIRES the meta to already exist with a 'features' key —
matching F5 behavior. If the meta is missing entirely, upload a minimal
one with `features` populated first (e.g. from booster.feature_names),
then run this job. We do not bootstrap from the booster here because that
would require downloading the model and feels out-of-scope for a meta patch.

Entrypoint: `python -m training.retrain_hr_meta` (the Cloud Run Job command).
"""
import json
import logging
import sys
from datetime import datetime, timezone

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


GCS_META_LATEST    = "HR_Pro/models/model_meta_hr_v6.json"
GCS_META_ARCHIVE   = "HR_Pro/models/archive/model_meta_hr_v6.{ts}.json"
GCS_MODEL_FEATURES = "HR_Pro/data/model_features.csv"


def compute_feature_means(features_df: pd.DataFrame,
                          feature_list: list[str]) -> dict[str, float]:
    """Compute mean per feature, skipping NaN. Features not in the DataFrame
    are omitted (the runner falls back to NaN for those, which XGBoost handles).
    """
    means = {}
    missing = []
    for f in feature_list:
        if f not in features_df.columns:
            missing.append(f)
            continue
        series = pd.to_numeric(features_df[f], errors="coerce")
        m = series.mean(skipna=True)
        if pd.isna(m):
            missing.append(f)
            continue
        means[f] = float(m)
    if missing:
        logger.warning(f"feature_means could not be computed for "
                       f"{len(missing)} features: {sorted(missing)[:5]}")
    logger.info(f"feature_means computed for {len(means)}/{len(feature_list)} features")
    return means


def run() -> dict:
    """Read meta + features from GCS, patch feature_means, write archive + latest."""
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_bytes, read_csv, write_bytes, exists

    if not GCS_BUCKET:
        return {"status": "error",
                "error": "MLB_GCS_BUCKET not set — this job requires GCS mode"}

    # 1. Load current meta
    if not exists(GCS_META_LATEST):
        return {"status": "error",
                "error": f"{GCS_META_LATEST} not found in GCS — "
                         f"upload a meta with 'features' key first"}
    try:
        meta = json.loads(read_bytes(GCS_META_LATEST))
    except Exception as e:
        return {"status": "error", "error": f"meta load: {e}"}

    features = meta.get("features")
    if not features:
        return {"status": "error",
                "error": "meta missing 'features' key — nothing to compute means for. "
                         "Bootstrap a meta with features=booster.feature_names first."}
    logger.info(f"Loaded meta: version={meta.get('version')} | "
                f"features={len(features)} | "
                f"feature_means_was={len(meta.get('feature_means', {}))}")

    # 2. Load feature table
    if not exists(GCS_MODEL_FEATURES):
        return {"status": "error",
                "error": f"{GCS_MODEL_FEATURES} not found — run /build-features for HR first"}
    try:
        feat_df = read_csv(GCS_MODEL_FEATURES, low_memory=False)
    except Exception as e:
        return {"status": "error", "error": f"features load: {e}"}
    logger.info(f"Loaded feature table: {len(feat_df):,} rows × {len(feat_df.columns)} cols")

    # 3. Compute means
    means = compute_feature_means(feat_df, features)
    if not means:
        return {"status": "error", "error": "0 features had a computable mean"}

    # 4. Patch meta — preserve everything else
    patched = dict(meta)
    patched["feature_means"] = means
    patched["feature_means_computed_at"] = datetime.now(timezone.utc).isoformat()
    patched["feature_means_source_rows"] = int(len(feat_df))

    patched_bytes = json.dumps(patched, indent=2).encode("utf-8")

    # 5. Write archive (timestamped, never overwritten)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_key = GCS_META_ARCHIVE.format(ts=ts)
    try:
        write_bytes(patched_bytes, archive_key)
        logger.info(f"Archived to {archive_key}")
    except Exception as e:
        return {"status": "error", "error": f"archive write: {e}"}

    # 6. Overwrite the latest pointer
    try:
        write_bytes(patched_bytes, GCS_META_LATEST)
        logger.info(f"Updated latest pointer: {GCS_META_LATEST}")
    except Exception as e:
        return {"status": "error", "error": f"latest write: {e}"}

    return {
        "status":            "ok",
        "features":          len(features),
        "feature_means":     len(means),
        "source_rows":       int(len(feat_df)),
        "archive_key":       archive_key,
        "latest_key":        GCS_META_LATEST,
    }


def main():
    """Cloud Run Job entrypoint. Logs result and exits non-zero on failure."""
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
