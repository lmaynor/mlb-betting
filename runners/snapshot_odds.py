"""
runners/snapshot_odds.py — Fetch today's MLB slate from SGO, save to GCS.

Runs on a Cloud Scheduler trigger via main.py's /snapshot-odds endpoint.

What it does:
  1. Calls SGO /v2/events with leagueID=MLB, oddsAvailable=true.
  2. Costs N objects where N = number of MLB events returned.
  3. Writes raw JSON to GCS at two paths:
       Odds/sgo/{YYYY-MM-DD}/snapshot_{HHMM_UTC}.json   (historical archive)
       Odds/sgo/latest.json                              (runner read target)
  4. Returns a result dict with status, event count, and the GCS paths.

Failure behavior:
  - SGO returns empty/error → write nothing, return error status. Old
    latest.json stays intact so dependent runners don't see empty data.
  - Partial write of latest.json → temp-then-overwrite pattern avoids races.
"""
import json
import logging
import time
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)


# GCS path conventions
GCS_SNAPSHOT_DIR_FMT = "Odds/sgo/{date}/snapshot_{hhmm}.json"
GCS_LATEST_KEY       = "Odds/sgo/latest.json"
GCS_LATEST_TMP_KEY   = "Odds/sgo/latest.json.tmp"


def run(run_date: str = None) -> dict:
    """Fetch today's slate from SGO and write to GCS.

    Args:
        run_date: ISO date string ("2026-05-12"). Defaults to today (UTC).

    Returns:
        Dict with keys:
          status:           "ok" or "error"
          events:           int, number of events returned
          archive_key:      str, GCS path to the timestamped snapshot
          latest_key:       str, GCS path to latest.json
          objects_consumed: int, monthly entity delta (best-effort)
          error:            str, present on failure
    """
    run_date = run_date or date.today().isoformat()
    started_utc = datetime.now(timezone.utc)
    logger.info(f"SGO snapshot | date={run_date} | start={started_utc.isoformat()}")

    from mlb_core.config import GCS_BUCKET
    if not GCS_BUCKET:
        return {"status": "error",
                "error": "GCS_BUCKET not set — snapshot runner requires GCS mode"}

    from mlb_core.odds.sgo import SgoClient
    from mlb_core.storage import write_bytes, exists, read_bytes, delete

    client = SgoClient()

    # Read usage before the call so we can report the delta
    usage_before = _safe_usage(client)

    # Fetch the slate
    try:
        events = client.fetch_mlb_slate()
    except Exception as e:
        logger.error(f"SGO snapshot: fetch failed: {e}")
        return {"status": "error", "error": f"fetch_mlb_slate: {e}"}

    if not events:
        # Genuinely empty slate (off-season, schedule gap). Don't overwrite
        # latest.json — runners can keep using yesterday's snapshot if needed.
        logger.warning("SGO snapshot: 0 events returned — leaving latest.json unchanged")
        return {"status": "error",
                "error": "no events returned",
                "events": 0}

    # Build paths
    hhmm = started_utc.strftime("%H%M")
    archive_key = GCS_SNAPSHOT_DIR_FMT.format(date=run_date, hhmm=hhmm)

    # Serialize once, write to both paths
    payload = json.dumps(events, separators=(",", ":")).encode("utf-8")
    size_kb = len(payload) / 1024
    logger.info(f"SGO snapshot: {len(events)} events | {size_kb:.1f} KB payload")

    # 1. Archive — straight write, no race risk
    try:
        write_bytes(payload, archive_key)
        logger.info(f"  archive → gs://{GCS_BUCKET}/{archive_key}")
    except Exception as e:
        logger.error(f"  archive write failed: {e}")
        return {"status": "error", "error": f"archive write: {e}"}

    # 2. latest.json — atomic-ish swap: write to .tmp then overwrite latest
    try:
        write_bytes(payload, GCS_LATEST_TMP_KEY)
        # GCS doesn't have rename, but write_bytes on the destination is
        # a single atomic operation from a reader's perspective. Overwrite
        # latest.json directly, then clean up the tmp marker.
        write_bytes(payload, GCS_LATEST_KEY)
        if exists(GCS_LATEST_TMP_KEY):
            delete(GCS_LATEST_TMP_KEY)
        logger.info(f"  latest  → gs://{GCS_BUCKET}/{GCS_LATEST_KEY}")
    except Exception as e:
        logger.error(f"  latest write failed: {e}")
        return {"status": "error", "error": f"latest write: {e}",
                "events": len(events), "archive_key": archive_key}

    # Re-read usage to measure cost
    usage_after = _safe_usage(client)
    consumed = _delta(usage_before, usage_after)

    logger.info(f"SGO snapshot: done | events={len(events)} | "
                f"objects consumed={consumed}")

    return {
        "status":           "ok",
        "events":           len(events),
        "archive_key":      archive_key,
        "latest_key":       GCS_LATEST_KEY,
        "objects_consumed": consumed,
        "size_kb":          round(size_kb, 1),
    }


def _safe_usage(client) -> dict | None:
    """Best-effort /account/usage read. Returns None on any failure."""
    try:
        return client.get_usage()
    except Exception as e:
        logger.warning(f"SGO usage check failed: {e}")
        return None


def _delta(before: dict | None, after: dict | None) -> int | None:
    """Compute monthly entity delta between two usage payloads."""
    if not before or not after:
        return None
    try:
        b = int(before["rateLimits"]["per-month"]["current-entities"])
        a = int(after["rateLimits"]["per-month"]["current-entities"])
        return a - b
    except (KeyError, TypeError, ValueError):
        return None
