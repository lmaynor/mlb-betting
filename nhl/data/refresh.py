"""Nightly NHL incremental ingest. Runs year-round; no-ops when no games finished.

Re-fetches the trailing REFRESH_LOOKBACK_DAYS (default 2, ending yesterday in America/New_York),
upserts into the masters, and writes the NHL/last_refresh.json sentinel. Re-fetching (force)
rather than skipping known games is deliberate: scorekeepers correct stats a day or two later and
the upsert keeps the latest copy. Self-healing across the offseason: a window with no finished
games writes status="skipped" and touches nothing else.

Run:
    GCS_BUCKET=concrete-crow-445205-m4-mlb-data python3 -m nhl.data.refresh
    python3 -m nhl.data.refresh --date 2026-10-13        # one explicit end date
"""
import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from mlb_core import storage
from nhl.data import ingest
from nhl.data.nhl_api import NhlClient
from nhl.data.settings import LAST_REFRESH, REFRESH_LOOKBACK_DAYS, TIMEZONE

logger = logging.getLogger(__name__)


def _yesterday() -> date:
    return (datetime.now(ZoneInfo(TIMEZONE)) - timedelta(days=1)).date()


def _write_sentinel(payload: dict) -> None:
    payload = dict(payload)
    payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    try:
        storage.write_bytes(json.dumps(payload).encode(), LAST_REFRESH)
    except Exception as exc:                                                # noqa: BLE001
        logger.warning("could not write NHL refresh sentinel: %s", exc)


def _refresh_reference(ref_client=None) -> dict:
    """Roster crosswalk + team vocabulary (nhl.data.players / nhl.data.teams).

    Runs AFTER game ingest so a throttled roster pull can never block box scores. A non-empty
    `failed_teams` is a PARTIAL crosswalk, not a success: with one roster missing, a namesake on
    another team could resolve 'exactly' to the wrong player (a hazard guarded against in
    players.py; the current crosswalks contain no same-name pairs, so this is precautionary).
    Never raises -- failures come back as `reference_error` / `failed_teams`.
    """
    out = {}
    try:
        from nhl.data import players, teams
        from nhl.data.client import NhlClient as ReferenceClient
        rc = ref_client or ReferenceClient()
        out["players"] = players.refresh_players(rc)
        out["teams"] = teams.refresh_teams(rc)
        failed = (out["players"] or {}).get("failed_teams") or []
        if failed:
            logger.error("NHL roster crosswalk PARTIAL: failed teams %s", failed)
            out["failed_teams"] = list(failed)
    except Exception as exc:                                                # noqa: BLE001
        logger.exception("NHL reference refresh failed")
        out["reference_error"] = repr(exc)
    return out


def refresh_nightly_gcs(end: date = None, lookback_days: int = None, client=None,
                        reference: bool = True, ref_client=None) -> dict:
    end = end or _yesterday()
    lookback = lookback_days or REFRESH_LOOKBACK_DAYS
    start = end - timedelta(days=lookback - 1)
    client = client or NhlClient()

    window = {str(start + timedelta(days=i)) for i in range(lookback)}
    games = [g for g in ingest.games_in_weeks(client, start, end)
             if g.get("_date") in window and ingest.is_final(g)]
    if not games:
        logger.info("no finished NHL games %s..%s (offseason/off-days); no game ingest", start, end)
        result = {"status": "skipped", "start": str(start), "end": str(end), "games": 0}
    else:
        acc = ingest.ingest_games(client, games, force=True)
        added = ingest.write_masters(acc) if acc["games"] else {}
        logger.info("NHL refresh %s..%s: %d games ingested, %d failed, %s",
                    start, end, len(acc["games"]), len(acc["failed"]), added)
        result = {"status": "ok" if not acc["failed"] else "partial",
                  "start": str(start), "end": str(end),
                  "games": len(acc["games"]), "failed_game_ids": acc["failed"], **added}

    if reference:                       # independent of whether games were played
        ref = _refresh_reference(ref_client)
        result.update(ref)
        if ref.get("failed_teams") or ref.get("reference_error"):
            result["status"] = "partial"
    _write_sentinel(result)
    return result


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="end date YYYY-MM-DD (default: yesterday, ET)")
    ap.add_argument("--lookback", type=int, default=0, help="trailing days to re-fetch")
    ap.add_argument("--no-reference", action="store_true", help="skip the roster/team crosswalk refresh")
    args = ap.parse_args()
    result = refresh_nightly_gcs(date.fromisoformat(args.date) if args.date else None,
                                 args.lookback or None, reference=not args.no_reference)
    if result["status"] == "partial":
        sys.exit(1)        # non-zero so the Cloud Run Job shows failed and retries


if __name__ == "__main__":
    main()
