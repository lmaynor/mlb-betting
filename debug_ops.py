#!/usr/bin/env python3
"""
debug_ops.py — Run all monitor-ops checks locally and print a detailed report.

Usage:
    python debug_ops.py
    python debug_ops.py 2026-05-24   # specific date

Requires the same env vars as Cloud Run (DATABASE_URL / DB_*, GCS_BUCKET_NAME,
GOOGLE_APPLICATION_CREDENTIALS). Copy from .env or your local secrets.
"""
import sys
import json
from datetime import date, datetime, timezone

run_date = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()

print(f"\n{'='*60}")
print(f"  MLB Monitor-Ops Diagnostic — {run_date}")
print(f"{'='*60}\n")

# ── 1. Scheduler jobs ─────────────────────────────────────────────────────────
print("── 1. Scheduler jobs ──")
try:
    from google.cloud import scheduler_v1
    PROJECT  = "concrete-crow-445205-m4"
    LOCATION = "us-central1"
    client = scheduler_v1.CloudSchedulerClient()
    parent = f"projects/{PROJECT}/locations/{LOCATION}"
    jobs = list(client.list_jobs(parent=parent))
    for job in jobs:
        name = job.name.split("/")[-1]
        code = job.status.code if job.status else 0
        last = job.last_attempt_time.isoformat() if job.last_attempt_time else "never"
        status = "✅ OK" if code in (0, -1) else f"❌ FAILED (code={code})"
        msg = f"  {status}  {name:<35} last={last}"
        if code not in (0, -1) and job.status.message:
            msg += f"\n         msg: {job.status.message}"
        print(msg)
except ImportError:
    print("  ⚠️  google-cloud-scheduler not installed — run: pip install google-cloud-scheduler")
except Exception as e:
    print(f"  ❌ Error: {e}")
print()

# ── 2. GCS data masters ───────────────────────────────────────────────────────
print("── 2. GCS data masters ──")
DATA_MASTER_KEYS = {
    "scoring_master":  "Scoring/scoring_master.csv",
    "statcast_master": "Statcast/statcast_master.csv",
    "weather_master":  "Weather/weather_master.csv",
    "umpires_master":  "Umpires/umpscorecards_master.csv",
}
STALE_HOURS = 26.0
try:
    from mlb_core.storage import stat
    for name, key in DATA_MASTER_KEYS.items():
        s = stat(key)
        if s is None:
            print(f"  ❌ MISSING  {name}  ({key})")
        else:
            age = (datetime.now(timezone.utc) - s["mtime_utc"]).total_seconds() / 3600
            status = "✅ OK" if age <= STALE_HOURS else "❌ STALE"
            print(f"  {status}  {name:<22} age={age:.1f}h  ({key})")
except Exception as e:
    print(f"  ❌ Error: {e}")
print()

# ── 3. SGO snapshot ───────────────────────────────────────────────────────────
print("── 3. SGO snapshot ──")
try:
    from mlb_core.storage import stat
    s = stat("Odds/sgo/latest.json")
    if s is None:
        print("  ❌ MISSING  Odds/sgo/latest.json")
    else:
        age = (datetime.now(timezone.utc) - s["mtime_utc"]).total_seconds() / 3600
        status = "✅ OK" if age <= STALE_HOURS else "❌ STALE"
        print(f"  {status}  latest.json  age={age:.1f}h")
except Exception as e:
    print(f"  ❌ Error: {e}")
print()

# ── 4. Build sentinels ────────────────────────────────────────────────────────
print("── 4. Build sentinels ──")
try:
    from mlb_core.registry import SYSTEMS
    from mlb_core.storage import stat, read_bytes, exists
    seen: set = set()
    for s_name, cfg in SYSTEMS.items():
        if not cfg.active or cfg.build_sentinel in seen:
            continue
        seen.add(cfg.build_sentinel)
        key = cfg.build_sentinel
        if not exists(key):
            print(f"  ❌ MISSING   {s_name:<8} ({key})")
            continue
        si = stat(key)
        if si is None:
            print(f"  ❌ UNREADABLE {s_name:<8} ({key})")
            continue
        age = (datetime.now(timezone.utc) - si["mtime_utc"]).total_seconds() / 3600
        try:
            data = json.loads(read_bytes(key).decode())
            build_ok = data.get("status") == "ok"
            err = data.get("error", "")
        except Exception:
            build_ok, err = False, "parse error"
        status = "✅ OK" if (build_ok and age <= STALE_HOURS) else "❌ FAIL"
        detail = f"age={age:.1f}h" + (f"  error={err}" if not build_ok else "")
        print(f"  {status}  {s_name:<8} {detail}")
except Exception as e:
    print(f"  ❌ Error: {e}")
print()

# ── 5. Stuck bets ─────────────────────────────────────────────────────────────
print("── 5. Stuck bets (pending > 3 days) ──")
try:
    from datetime import timedelta
    from mlb_core.tracking.bet_tracker import _make_engine
    from sqlalchemy import text
    cutoff = (date.today() - timedelta(days=3)).isoformat()
    engine = _make_engine(db_path="unused")
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT system, COUNT(*) as n, MIN(game_date) as oldest
                FROM bets
                WHERE result IS NULL AND game_date <= :cutoff
                GROUP BY system
                ORDER BY oldest
            """),
            {"cutoff": cutoff},
        ).fetchall()
    if rows:
        for row in rows:
            print(f"  ❌ {row[0]:<8}  {row[1]} bets pending  oldest={row[2]}")
    else:
        print("  ✅ No stuck bets")
    # Also show count of ALL pending bets for context
    with engine.connect() as conn:
        total = conn.execute(
            text("SELECT system, COUNT(*) FROM bets WHERE result IS NULL GROUP BY system")
        ).fetchall()
    if total:
        print(f"\n  Pending (all): " + ", ".join(f"{r[0]}={r[1]}" for r in total))
except Exception as e:
    print(f"  ❌ Error: {e}")
print()

# ── 6. Feature CSV freshness ──────────────────────────────────────────────────
print("── 6. Feature CSVs ──")
try:
    from mlb_core.registry import SYSTEMS
    from mlb_core.storage import stat
    seen_csv: set = set()
    for s_name, cfg in SYSTEMS.items():
        if not cfg.active or cfg.feature_csv in seen_csv:
            continue
        seen_csv.add(cfg.feature_csv)
        s = stat(cfg.feature_csv)
        if s is None:
            print(f"  ❌ MISSING  {s_name:<8} ({cfg.feature_csv})")
        else:
            age = (datetime.now(timezone.utc) - s["mtime_utc"]).total_seconds() / 3600
            status = "✅ OK" if age <= STALE_HOURS else "❌ STALE"
            print(f"  {status}  {s_name:<8} age={age:.1f}h")
except Exception as e:
    print(f"  ❌ Error: {e}")
print()

# ── 7. Model artifacts ────────────────────────────────────────────────────────
print("── 7. Model artifacts ──")
try:
    from mlb_core.registry import SYSTEMS
    from mlb_core.storage import exists
    seen_art: set = set()
    for s_name, cfg in SYSTEMS.items():
        if not cfg.active or cfg.model_artifact in seen_art:
            continue
        seen_art.add(cfg.model_artifact)
        ok = exists(cfg.model_artifact)
        status = "✅ OK" if ok else "❌ MISSING"
        print(f"  {status}  {s_name:<8} ({cfg.model_artifact})")
except Exception as e:
    print(f"  ❌ Error: {e}")
print()

print(f"{'='*60}")
print("  Paste any ❌ lines and we'll fix them.")
print(f"{'='*60}\n")
