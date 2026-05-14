"""
runners/monitor_ops.py — Infrastructure health monitor.

Fires at 13:15 UTC daily (after all feature builds complete) via the
mlb-monitor-ops Cloud Scheduler job. Posts to Discord ONLY when something
is wrong — silent on a clean run.

Checks:
  1. All 11 Cloud Scheduler jobs: last run status.code (0 = ok, 2 = error)
  2. SGO snapshot age: Odds/sgo/latest.json must be < 26 hrs old
  3. Feature CSV age: each system's model_features.csv must be < 26 hrs old
  4. Model artifact exists: each system's xgb_*.json must exist in GCS

Called by main.py /monitor-ops.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SCHEDULER_JOBS = [
    "mlb-refresh-data",
    "mlb-settle",
    "mlb-monitor",
    "mlb-hr-features",
    "mlb-nrfi-features",
    "mlb-k-features",
    "mlb-f5-features",
    "mlb-snapshot-morning",
    "mlb-betting-morning",
    "mlb-snapshot-evening",
    "mlb-betting-evening",
    "mlb-monitor-ops",
]

FEATURE_KEYS = {
    "HR":   "HR_Pro/data/model_features.csv",
    "NRFI": "NRFI_Pro_System/data/model_features.csv",
    "F5":   "F5_Pro_System/data/model_features.csv",
    "K":    "K_Pro_System/data/model_features.csv",
}

MODEL_KEYS = {
    "HR":   "HR_Pro/models/xgb_hr_v6.json",
    "NRFI": "NRFI_Pro_System/models/xgb_halfinn_v17.json",
    "F5":   "F5_Pro_System/models/xgb_f5_v5.json",
    "K":    "K_Pro_System/models/xgb_k_v1.json",
}

SGO_SNAPSHOT_KEY   = "Odds/sgo/latest.json"
DATA_MASTER_KEYS = {
    "scoring_master":  "Scoring/scoring_master.csv",
    "statcast_master": "Statcast/statcast_master.csv",
    "weather_master":  "Weather/weather_master.csv",
    "umpires_master":  "Umpires/umpscorecards_master.csv",
}
DATA_STALE_HOURS = float(os.getenv("MONITOR_OPS_DATA_STALE_HOURS", "26"))
STALE_HOURS        = float(os.getenv("MONITOR_OPS_STALE_HOURS", "26"))
GCP_PROJECT        = os.getenv("GCP_PROJECT", "concrete-crow-445205-m4")
SCHEDULER_LOCATION = os.getenv("SCHEDULER_LOCATION", "us-central1")


# ── Scheduler checks ──────────────────────────────────────────────────────────

def _check_schedulers() -> list[str]:
    """Return failure messages for any scheduler job whose last run errored."""
    try:
        from google.cloud import scheduler_v1
    except ImportError:
        logger.warning("monitor_ops: google-cloud-scheduler not installed — skipping")
        return []

    client = scheduler_v1.CloudSchedulerClient()
    parent = f"projects/{GCP_PROJECT}/locations/{SCHEDULER_LOCATION}"
    try:
        jobs = list(client.list_jobs(parent=parent))
    except Exception as e:
        return [f"Scheduler API error: {e}"]

    failures = []
    for job in jobs:
        name = job.name.split("/")[-1]
        if name not in SCHEDULER_JOBS:
            continue
        code = job.status.code if job.status else 0
        if code not in (0, -1):  # -1 = never run, 0 = ok
            last_run = (
                job.last_attempt_time.isoformat()
                if job.last_attempt_time else "unknown"
            )
            failures.append(
                f"`{name}` last run failed (code={code}, at={last_run})"
            )
    return failures


# ── GCS freshness / existence checks ─────────────────────────────────────────

def _gcs_age_hours(gcs_key: str) -> float | None:
    """Return age of a GCS object in hours. None if missing or GCS unavailable."""
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import exists

    if not GCS_BUCKET:
        return None
    if not exists(gcs_key):
        return None
    try:
        from google.cloud import storage as gcs
        client = gcs.Client()
        blob = client.bucket(GCS_BUCKET).blob(gcs_key)
        blob.reload()
        age = (datetime.now(timezone.utc) - blob.updated).total_seconds() / 3600
        return age
    except Exception as e:
        logger.warning(f"monitor_ops: age check failed for {gcs_key}: {e}")
        return None


def _check_data_masters() -> list[str]:
    """Check scoring/statcast/weather/umpires masters are fresh."""
    failures = []
    for name, key in DATA_MASTER_KEYS.items():
        age = _gcs_age_hours(key)
        if age is None:
            failures.append(f"`{name}` missing: `{key}`")
        elif age > DATA_STALE_HOURS:
            failures.append(f"`{name}` stale: {age:.1f}h old")
    return failures


def _check_snapshot_freshness() -> list[str]:
    age = _gcs_age_hours(SGO_SNAPSHOT_KEY)
    if age is None:
        return [f"SGO snapshot missing: `{SGO_SNAPSHOT_KEY}`"]
    if age > STALE_HOURS:
        return [f"SGO snapshot stale: {age:.1f}h old (threshold={STALE_HOURS:.0f}h)"]
    return []


def _check_feature_freshness() -> list[str]:
    failures = []
    for system, key in FEATURE_KEYS.items():
        age = _gcs_age_hours(key)
        if age is None:
            failures.append(f"{system} `model_features.csv` missing")
        elif age > STALE_HOURS:
            failures.append(f"{system} `model_features.csv` stale: {age:.1f}h old")
    return failures


def _check_stuck_bets() -> list[str]:
    """Alert if any bets have been pending for > 3 days."""
    try:
        from datetime import date, timedelta
        from mlb_core.tracking.bet_tracker import _make_engine
        from sqlalchemy import text
        cutoff = (date.today() - timedelta(days=3)).isoformat()
        engine = _make_engine(db_path="unused")
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT system, COUNT(*) as n FROM bets "
                     "WHERE result IS NULL AND game_date <= :cutoff "
                     "GROUP BY system"),
                {"cutoff": cutoff},
            ).fetchall()
        failures = []
        for row in rows:
            failures.append(f"{row[0]}: {row[1]} bets pending > 3 days")
        return failures
    except Exception as e:
        logger.warning(f"monitor_ops: stuck bets check failed: {e}")
        return []


def _check_model_artifacts() -> list[str]:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import exists

    if not GCS_BUCKET:
        return []
    failures = []
    for system, key in MODEL_KEYS.items():
        if not exists(key):
            failures.append(f"{system} model artifact missing: `{key}`")
    return failures


# ── Discord ───────────────────────────────────────────────────────────────────

def _post_ops_alert(failures: list[str], run_date: str) -> None:
    from mlb_core.notify.discord import _get_webhook, _post

    webhook_url = _get_webhook("SUMMARY") or _get_webhook("HR")
    if not webhook_url:
        logger.warning("monitor_ops: no webhook configured")
        return

    bullet_list = "\n".join(f"• {f}" for f in failures)
    embed = {
        "title":       f"🚨 Ops Alert | {run_date}",
        "description": f"**{len(failures)} issue(s) detected:**\n{bullet_list}",
        "color":       0xED4245,
        "footer":      {"text": "mlb-betting ops monitor"},
    }
    _post(webhook_url, {"embeds": [embed]})
    logger.warning(f"monitor_ops: alert posted — {len(failures)} failure(s)")


# ── Entry point ───────────────────────────────────────────────────────────────

def run(run_date: str = None) -> dict:
    """Run all infrastructure health checks. Post to Discord only on failure."""
    from datetime import date
    run_date = run_date or date.today().isoformat()
    logger.info(f"monitor_ops: starting for {run_date}")

    failures: list[str] = []
    failures += _check_schedulers()
    failures += _check_data_masters()
    failures += _check_snapshot_freshness()
    failures += _check_stuck_bets()
    failures += _check_feature_freshness()
    failures += _check_model_artifacts()

    if failures:
        _post_ops_alert(failures, run_date)
    else:
        logger.info("monitor_ops: all checks passed")

    return {
        "status":   "ok",
        "run_date": run_date,
        "failures": failures,
        "healthy":  len(failures) == 0,
    }
