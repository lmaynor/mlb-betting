"""
runners/monitor_ops.py — Infrastructure health monitor.

Fires at 15:20 UTC daily (after all feature builds complete) via the
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

from mlb_core.registry import SYSTEMS

logger = logging.getLogger(__name__)

# Deleted 2026-08-17 (finding C6.8): a hardcoded SCHEDULER_JOBS allowlist
# used to live here (4 of its 16 entries were already-retired legacy
# snapshot job names). It was dead code -- _check_schedulers() below was
# already rewritten to enumerate every live job from the Scheduler API
# directly (see its own comment) -- but CONTEXT.md s9 still told engineers
# to keep it in sync "because it drives the health check," which it no
# longer did. A future "cleanup" restoring allowlist filtering, believing
# that to be the intended behavior, would have silently reintroduced
# exactly the blind-spot class that caused the original SGO incident this
# file's health check exists to catch. CONTEXT.md s9 corrected alongside
# this deletion.

# Derived from registry — OUTS shares K's feature CSV so deduplicate by value.
# Use a dict comprehension; OUTS will map to the same path as K (that's correct —
# both share one CSV, so the freshness check fires once per unique path).
FEATURE_KEYS = {
    s: cfg.feature_csv
    for s, cfg in SYSTEMS.items()
    if cfg.active and s != "OUTS"   # OUTS shares K's CSV; K check covers it
}

MODEL_KEYS = {
    s: cfg.model_artifact
    for s, cfg in SYSTEMS.items()
    if cfg.active
    # OUTS now has its own dedicated model_artifact (fixed 2026-08-17, see
    # docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md
    # finding A8) -- no longer excluded, so its real artifact gets checked
    # like every other system's.
}

SGO_SNAPSHOT_KEY   = "Odds/sgo/latest.json"
DATA_MASTER_KEYS = {
    "scoring_master":  "Scoring/scoring_master.csv",
    "statcast_master": "Statcast/statcast_master.csv",
    "weather_master":  "Weather/weather_master.csv",
    "umpires_master":  "Umpires/umpscorecards_master.csv",
    # Added 2026-08-19: these four had NO freshness check anywhere, on top of
    # having no scheduled refresh at all -- the combination is exactly how
    # fangraphs_pitching/swing_take/manager_hooks sat frozen for 11+ weeks
    # (since ~2026-06-02) with zero alerts. Now refreshed by
    # auxiliary_features_nightly_gcs() via /refresh-data (see main.py) and
    # checked here the same way as the four sources above. See docs/audits/
    # 2026-08-19_feature_data_pipeline_review.md finding 2.3.
    "fangraphs_pitching_master": "AuxData/fangraphs_pitching_master.csv",
    "swing_take_master":         "AuxData/swing_take_master.csv",
    "team_schedule_master":      "AuxData/team_schedule_master.csv",
    "manager_hooks_master":      "AuxData/manager_hooks_master.csv",
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
        # Check EVERY enabled scheduler, not an allowlist -- the hardcoded
        # SCHEDULER_JOBS filter silently ignored all newer jobs, which is how
        # PERMISSION_DENIED (code 7) and OOM 503s (code 14) ran for days
        # unalerted. Paused jobs are deliberate; skip only those.
        try:
            from google.cloud import scheduler_v1 as _sv1
            if job.state == _sv1.Job.State.PAUSED:
                continue
        except Exception:  # noqa: BLE001
            pass
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


def _check_odds_history_freshness() -> list[str]:
    """The intraday odds tracker must have banked YESTERDAY's k_ou partition.
    (Its failure mode is silent: schedulers denied or job broken -> the
    survival/alert/alt-line chain quietly starves.)

    Checks for ANY file under the partition directory, not a hardcoded
    part-0.parquet -- write_partition's append=True path now writes each
    intraday snapshot as its own part-{uuid}.parquet (see
    docs/solutions/logic-errors/odds-history-append-write-amplification.md),
    so a healthy, actively-banking partition may never contain a literal
    part-0.parquet at all."""
    from datetime import date, timedelta
    from mlb.analysis.odds_history import partition_dir
    from mlb_core.storage import list_keys
    yday = (date.today() - timedelta(days=1)).isoformat()
    if date.today().month in (12, 1, 2):  # off-season
        return []
    if not list_keys(partition_dir("k_ou", yday)):
        return [f"odds_history: no k_ou partition for {yday} -- BettingPros tracker not banking"]
    return []


# ── GCS freshness / existence checks ─────────────────────────────────────────

def _gcs_age_hours(gcs_key: str) -> float | None:
    """Return age of a GCS object in hours. None if missing or GCS unavailable."""
    from mlb_core.config import GCS_BUCKET
    if not GCS_BUCKET:
        return None
    try:
        from mlb_core.storage import stat
        s = stat(gcs_key)
        if s is None:
            return None
        return (datetime.now(timezone.utc) - s["mtime_utc"]).total_seconds() / 3600
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


def _check_build_sentinels() -> list[str]:
    """Check each system's last_build.json sentinel for freshness and status."""
    from mlb_core.storage import stat, read_bytes, exists
    import json

    # Build from registry; deduplicate by path (OUTS shares K's sentinel).
    _seen: set[str] = set()
    SENTINELS: dict[str, str] = {}
    for s, cfg in SYSTEMS.items():
        if cfg.active and cfg.build_sentinel not in _seen:
            SENTINELS[s] = cfg.build_sentinel
            _seen.add(cfg.build_sentinel)
    failures = []
    for system, key in SENTINELS.items():
        if not exists(key):
            failures.append(f"`{system}` build sentinel missing: `{key}`")
            continue
        s = stat(key)
        if s is None:
            failures.append(f"`{system}` build sentinel unreadable: `{key}`")
            continue
        age_hrs = (datetime.now(timezone.utc) - s["mtime_utc"]).total_seconds() / 3600
        if age_hrs > DATA_STALE_HOURS:
            failures.append(f"`{system}` build sentinel stale: {age_hrs:.1f}h old")
            continue
        try:
            data = json.loads(read_bytes(key).decode())
            if data.get("status") != "ok":
                failures.append(f"`{system}` last build failed: {data.get('error', '?')}")
        except Exception as e:
            failures.append(f"`{system}` build sentinel parse error: {e}")
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
    from mlb_core.notify.discord import _get_ops_webhook, _post

    webhook_url = _get_ops_webhook()
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
    failures += _check_odds_history_freshness()
    failures += _check_data_masters()
    failures += _check_snapshot_freshness()
    failures += _check_build_sentinels()
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
