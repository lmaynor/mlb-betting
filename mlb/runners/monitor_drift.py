"""
runners/monitor_drift.py — Population Stability Index feature drift monitor. (T14)

Fires weekly (Monday mornings) via the mlb-monitor-drift Cloud Scheduler job.

For each system compares the distribution of the last 7 days of live
prediction feature values against the training-set distribution stored in
model_meta (feature_means + feature_stds). Uses PSI as the drift metric.

PSI interpretation:
  < 0.10 — no significant change
  0.10 - 0.25 — moderate change, monitor
  > 0.25 — significant shift, investigate before next retrain

Only checks the top-N features by model importance if available in meta;
otherwise checks all features with feature_stds populated.

Posts a Discord alert when any feature exceeds PSI_WARN_THRESHOLD (0.25).
Silent when clean.

Add to Cloud Scheduler: 0 9 * * 1 (09:00 UTC every Monday)
  POST https://<service>/monitor-drift

Entrypoint: python -m runners.monitor_drift
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

PSI_WARN_THRESHOLD = float(os.getenv("MONITOR_PSI_WARN", "0.25"))
PSI_MONITOR_THRESHOLD = float(os.getenv("MONITOR_PSI_MONITOR", "0.10"))
TOP_N_FEATURES = int(os.getenv("MONITOR_PSI_TOP_N", "15"))
LOOKBACK_DAYS = int(os.getenv("MONITOR_PSI_LOOKBACK", "7"))
N_BINS = 10  # PSI binning


SYSTEM_CONFIG = {
    "1IOU": {
        # Live runner loads the v18 ensemble (run_nrfi._load_v18_ensemble); v17 is
        # fallback only. Must point at v18 meta or PSI checks the wrong (stale)
        # training distribution. v18 nests feature_means/feature_stds under
        # sub_models -- _flatten_meta_stats unions them. (Fixed 2026-06-28.)
        "gcs_meta":     "NRFI_Pro_System/models/model_meta_v18.json",
        "gcs_features": "NRFI_Pro_System/data/model_features.csv",
    },
    "F5": {
        "gcs_meta":     "F5_Pro_System/models/model_meta_f5_v5.json",
        "gcs_features": "F5_Pro_System/data/model_features.csv",
    },
    "HR": {
        "gcs_meta":     "HR_Pro/models/model_meta_hr_v6.json",
        "gcs_features": "HR_Pro/data/model_features.csv",
    },
    "K": {
        "gcs_meta":     "K_Pro_System/models/model_meta_v1.json",
        "gcs_features": "K_Pro_System/data/model_features.csv",
    },
    # Added 2026-08-17 (finding C6.4): BATTER_HITS/BATTER_TB/GAME had zero
    # PSI/drift monitoring -- this dict hardcoded exactly the 4 systems that
    # existed when it was written, and CONTEXT.md's own "adding a new
    # system" checklist never mentioned this file, so every system added
    # since silently repeated the gap. See CONTEXT.md s6 for the checklist
    # line added alongside this fix.
    "BATTER_HITS": {
        "gcs_meta":     "BATTER_HITS_System/models/model_meta_batter_hits_v1.json",
        "gcs_features": "BATTER_HITS_System/data/model_features.csv",
    },
    "SB": {
        "gcs_meta":     "SB_Pro_System/models/model_meta_sb_v1.json",
        "gcs_features": "SB_Pro_System/data/model_features.csv",
    },
    "BATTER_TB": {
        "gcs_meta":     "BATTER_TB_System/models/model_meta_batter_tb_v1.json",
        "gcs_features": "BATTER_TB_System/data/model_features.csv",
    },
    "GAME": {
        "gcs_meta":     "GAME_Pro_System/models/model_meta_game_v1.json",
        "gcs_features": "GAME_Pro_System/data/model_features.csv",
    },
}


def _psi(expected: np.ndarray, actual: np.ndarray, n_bins: int = N_BINS) -> float:
    """
    Population Stability Index between two continuous distributions.

    Uses equal-frequency binning on the expected (training) distribution,
    then maps actual values into the same bins. Adds a small epsilon to
    avoid log(0).
    """
    if len(expected) < 20 or len(actual) < 5:
        return float("nan")

    # Drop NaN
    exp = expected[~np.isnan(expected)]
    act = actual[~np.isnan(actual)]
    if len(exp) < 10 or len(act) < 2:
        return float("nan")

    # Bin edges from expected distribution (equal-frequency)
    quantiles = np.linspace(0, 100, n_bins + 1)
    bin_edges = np.percentile(exp, quantiles)
    # Ensure monotonic and unique edges
    bin_edges = np.unique(bin_edges)
    if len(bin_edges) < 3:
        return float("nan")

    # Count proportions per bin
    exp_counts, _ = np.histogram(exp, bins=bin_edges)
    act_counts, _ = np.histogram(act, bins=bin_edges)

    exp_pct = exp_counts / len(exp)
    act_pct = act_counts / len(act)

    # Clip zeros
    eps = 1e-6
    exp_pct = np.clip(exp_pct, eps, None)
    act_pct = np.clip(act_pct, eps, None)

    psi = float(np.sum((act_pct - exp_pct) * np.log(act_pct / exp_pct)))
    return round(psi, 4)


def _psi_binary(train_p: float, actual_vals: np.ndarray) -> float:
    """Exact 2-category PSI for a 0/1 feature.

    The continuous _psi approximates the training distribution by sampling from a
    Gaussian(mean, std). That is invalid for binary features and produced absurd
    PSI in the 10-12 range (false positives: high_wind, is_cold, opener_flag, ...).
    For a binary feature the training proportion P(==1) IS the stored mean, so PSI
    over the two categories {1, 0} is exact.
    """
    act = actual_vals[~np.isnan(actual_vals)]
    if len(act) < 2:
        return float("nan")
    eps = 1e-6
    p = min(max(float(train_p), eps), 1.0 - eps)   # train P(==1)
    q = min(max(float(np.mean(act)), eps), 1.0 - eps)  # recent P(==1)
    psi = (q - p) * np.log(q / p) + ((1.0 - q) - (1.0 - p)) * np.log((1.0 - q) / (1.0 - p))
    return round(float(psi), 4)


def _flatten_meta_stats(meta: dict) -> tuple[dict, dict, dict, bool]:
    """Return (feature_means, feature_stds, feature_dists, is_ensemble).

    Single-model metas (v17, HR, K, F5) store these flat at top level. Ensemble
    metas (NRFI v18) store per-feature stats nested under meta["sub_models"][name]
    (pitcher/lineup/context). Their feature groups are disjoint, so we union them.

    is_ensemble flags whether to skip the top-N truncation in _check_system: for an
    ensemble we want full coverage across every sub-model, otherwise the first
    group's features (e.g. the 27 pitcher features) would crowd out lineup/context
    -- and lineup is the only sub-model carrying live signal.
    """
    means = dict(meta.get("feature_means", {}) or {})
    stds  = dict(meta.get("feature_stds",  {}) or {})
    dists = dict(meta.get("feature_dists", {}) or {})
    is_ensemble = False
    sub_models = meta.get("sub_models")
    if isinstance(sub_models, dict) and not stds:
        is_ensemble = True
        for sub in sub_models.values():
            if not isinstance(sub, dict):
                continue
            means.update(sub.get("feature_means", {}) or {})
            stds.update(sub.get("feature_stds",  {}) or {})
            dists.update(sub.get("feature_dists", {}) or {})
    return means, stds, dists, is_ensemble


def _load_meta(gcs_key: str) -> dict | None:
    from mlb_core.storage import read_bytes, exists
    if not exists(gcs_key):
        return None
    try:
        return json.loads(read_bytes(gcs_key))
    except Exception as e:
        logger.warning(f"drift: meta load failed {gcs_key}: {e}")
        return None


def _load_recent_features(gcs_key: str, lookback_days: int,
                          run_date: str) -> pd.DataFrame:
    from mlb_core.storage import read_csv, exists
    if not exists(gcs_key):
        return pd.DataFrame()
    try:
        df = read_csv(gcs_key, low_memory=False)
    except Exception as e:
        logger.warning(f"drift: features load failed {gcs_key}: {e}")
        return pd.DataFrame()
    if df.empty or "game_date" not in df.columns:
        return pd.DataFrame()
    df["game_date"] = pd.to_datetime(df["game_date"])
    cutoff = pd.Timestamp(run_date) - pd.Timedelta(days=lookback_days)
    recent = df[df["game_date"] >= cutoff].copy()
    logger.debug(f"drift: {len(recent):,} rows in last {lookback_days} days "
                 f"(from {cutoff.date()})")
    return recent


def _check_system(system: str, cfg: dict, run_date: str) -> dict:
    """Run PSI check for one system. Returns per-feature PSI dict."""
    meta = _load_meta(cfg["gcs_meta"])
    if meta is None:
        return {"status": "no_meta"}

    feature_means, feature_stds, feature_dists, is_ensemble = _flatten_meta_stats(meta)

    if not feature_stds:
        return {"status": "no_feature_stds",
                "note": "Run retrain to populate feature_stds in meta (T10/T14)"}

    # Single-model metas cap at top-N; ensemble metas (NRFI v18) check every
    # sub-model's features so lineup/context are not dropped by the truncation.
    keys = list(feature_stds.keys())
    features_to_check = keys if is_ensemble else keys[:TOP_N_FEATURES]

    recent = _load_recent_features(cfg["gcs_features"], LOOKBACK_DAYS, run_date)
    if recent.empty:
        return {"status": "no_recent_data"}

    psi_results: dict[str, float] = {}
    warnings: list[str] = []

    for feat in features_to_check:
        if feat not in recent.columns:
            continue
        mean = feature_means.get(feat)
        std  = feature_stds.get(feat)
        if mean is None or std is None or std < 1e-9:
            continue

        recent_vals = pd.to_numeric(recent[feat], errors="coerce").values
        nonnan = recent_vals[~np.isnan(recent_vals)]
        is_binary = nonnan.size > 0 and bool(np.all(np.isin(nonnan, (0.0, 1.0))))

        if is_binary:
            # Exact 2-category PSI; Gaussian sampling is invalid for 0/1 features.
            psi_val = _psi_binary(mean, recent_vals)
        else:
            # C04: use empirical percentiles from model_meta if available.
            # Falls back to Gaussian for backward compatibility with old meta.
            # (feature_dists is flattened across sub-models above; v18 has none yet
            # so NRFI uses the Gaussian fallback until the retrain writes them.)
            fdist = feature_dists.get(feat)
            if fdist is not None:
                ptiles = [fdist.get(k) for k in ("p5", "p10", "p25", "p50", "p75", "p90", "p95")]
                if all(v is not None for v in ptiles):
                    quantiles = np.array([0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95])
                    rng = np.random.default_rng(seed=42)
                    u = rng.uniform(0, 1, size=10_000)
                    train_approx = np.interp(u, quantiles, ptiles)
                else:
                    rng = np.random.default_rng(seed=42)
                    train_approx = rng.normal(loc=mean, scale=std, size=10_000)
            else:
                rng = np.random.default_rng(seed=42)
                train_approx = rng.normal(loc=mean, scale=std, size=10_000)
            psi_val = _psi(train_approx, recent_vals)
        if np.isnan(psi_val):
            continue

        psi_results[feat] = psi_val

        if psi_val >= PSI_WARN_THRESHOLD:
            warnings.append(
                f"**{feat}**: PSI={psi_val:.3f} (>= {PSI_WARN_THRESHOLD} — significant shift)"
            )
        elif psi_val >= PSI_MONITOR_THRESHOLD:
            logger.info(f"  drift {system}/{feat}: PSI={psi_val:.3f} (moderate, monitor)")

    n_warn = len(warnings)
    n_ok   = len(psi_results) - n_warn
    logger.info(
        f"drift {system}: {len(features_to_check)} features checked | "
        f"{n_ok} OK | {n_warn} warnings"
    )
    return {
        "status":        "ok",
        "features_checked": len(psi_results),
        "warnings":      warnings,
        "psi":           psi_results,
    }


def _post_drift_alert(system: str, warnings: list[str], run_date: str) -> None:
    from mlb_core.notify.discord import _get_webhook, _post
    webhook_url = _get_webhook("SUMMARY") or _get_webhook("HR")
    if not webhook_url:
        return
    color_dot = {"HR": "🔴", "1IOU": "🔵", "F5": "🟢", "K": "🟡"}.get(system, "⚪")
    alert_text = "\n".join(f"• {w}" for w in warnings)
    embed = {
        "title":       f"⚠️ {color_dot} {system} Feature Drift Alert | {run_date}",
        "description": (
            f"PSI >= {PSI_WARN_THRESHOLD} detected in {len(warnings)} feature(s). "
            f"Consider retraining before promotion to live.\n\n{alert_text}"
        ),
        "color":  0xFFA500,
        "footer": {"text": f"mlb-betting drift monitor | last {LOOKBACK_DAYS} days vs training dist"},
    }
    _post(webhook_url, {"embeds": [embed]})


def run(run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"drift monitor: starting for {run_date} | "
                f"lookback={LOOKBACK_DAYS}d | psi_warn={PSI_WARN_THRESHOLD}")

    from mlb_core.config import GCS_BUCKET
    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set — requires GCS mode"}

    results = {}
    total_warnings = 0

    for system, cfg in SYSTEM_CONFIG.items():
        try:
            result = _check_system(system, cfg, run_date)
        except Exception as e:
            logger.error(f"drift: {system} check failed: {e}")
            result = {"status": "error", "error": str(e)}

        results[system] = result
        warnings = result.get("warnings", [])
        if warnings:
            logger.warning(f"drift: {system} has {len(warnings)} PSI warning(s)")
            _post_drift_alert(system, warnings, run_date)
            total_warnings += len(warnings)
        else:
            logger.info(f"drift: {system} clean")

    return {
        "status":         "ok",
        "run_date":       run_date,
        "total_warnings": total_warnings,
        "systems":        results,
    }


def main():
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    result = run()
    print(json.dumps({
        k: v for k, v in result.items() if k != "systems"
    }, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
