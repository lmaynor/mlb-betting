"""
runners/snapshot_odds.py — Fetch today's MLB slate and save to GCS (SGO-shaped).

Runs on a Cloud Scheduler trigger via main.py's /snapshot-odds endpoint.

Provider (env ODDS_PRIMARY, default "sgo"):
  - "sgo":    legacy path — SGO /v2/events for all markets.
  - "parlay": ParlayAPI primary for covered markets (HR/K/OUTS/HITS/TB/ER props
              + game ML), converted to SGO shape via mlb_core.odds.parlay_adapter,
              MERGED with SGO-sourced inning markets (NRFI/1I-3way/F5/F5-ML/F1H,
              which ParlayAPI can't express). The same ParlayAPI pull also banks
              OddsAccum artifacts (credit unification — no separate accumulator).

Either way the output is the SAME SGO-shaped snapshot written to the SAME paths,
so the 9 runners + extractors are untouched:
     {out_prefix}/{YYYY-MM-DD}/snapshot_{HHMM_UTC}.json   (archive)
     {out_prefix}/latest.json                             (runner read target)
`out_prefix` defaults to "Odds/sgo"; pass a scratch prefix for a shadow run that
never touches the live latest.json (cutover safety).

Failure behavior: empty/failed fetch -> write nothing, old latest.json stays.
ParlayAPI failure falls back to the pure-SGO snapshot.
"""
import json
import logging
import os
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

# GCS path conventions (parameterized by out_prefix for shadow runs).
DEFAULT_PREFIX = "Odds/sgo"
SPORT = "baseball_mlb"


def _keys(out_prefix: str, run_date: str, hhmm: str) -> tuple[str, str, str]:
    archive = f"{out_prefix}/{run_date}/snapshot_{hhmm}.json"
    latest = f"{out_prefix}/latest.json"
    return archive, latest, f"{latest}.tmp"


def run(run_date: str = None, provider: str = None, out_prefix: str = DEFAULT_PREFIX) -> dict:
    """Fetch today's slate and write an SGO-shaped snapshot to GCS.

    Args:
        run_date:   ISO date ("2026-05-12"). Defaults to today (UTC). Slate is
                    filtered to this day in ET.
        provider:   "sgo" | "parlay". Defaults to env ODDS_PRIMARY or "sgo".
        out_prefix: GCS prefix for the snapshot (default "Odds/sgo"). Use a
                    scratch prefix for a shadow run.
    """
    run_date = run_date or date.today().isoformat()
    provider = (provider or os.environ.get("ODDS_PRIMARY", "sgo")).lower()
    started_utc = datetime.now(timezone.utc)
    logger.info(f"snapshot | date={run_date} | provider={provider} | prefix={out_prefix}")

    from mlb_core.config import GCS_BUCKET
    if not GCS_BUCKET:
        return {"status": "error",
                "error": "GCS_BUCKET not set — snapshot runner requires GCS mode"}

    # Gather provider-specific SGO-shaped events.
    try:
        if provider == "parlay":
            events, meta = _gather_parlay(run_date)
        else:
            events, meta = _gather_sgo(run_date)
    except Exception as e:  # noqa: BLE001
        logger.error(f"snapshot: gather failed ({provider}): {e}")
        return {"status": "error", "error": f"gather({provider}): {e}"}

    if not events:
        logger.warning("snapshot: 0 events — leaving latest.json unchanged")
        return {"status": "error", "error": "no events returned", "events": 0, **meta}

    hhmm = started_utc.strftime("%H%M")
    archive_key, latest_key, tmp_key = _keys(out_prefix, run_date, hhmm)
    payload = json.dumps(events, separators=(",", ":")).encode("utf-8")
    size_kb = len(payload) / 1024
    logger.info(f"snapshot: {len(events)} events | {size_kb:.1f} KB | {provider}")

    from mlb_core.storage import write_bytes, exists, delete
    try:
        write_bytes(payload, archive_key)
        write_bytes(payload, tmp_key)
        write_bytes(payload, latest_key)
        if exists(tmp_key):
            delete(tmp_key)
        logger.info(f"  wrote gs://{GCS_BUCKET}/{latest_key} (+archive)")
    except Exception as e:  # noqa: BLE001
        logger.error(f"  write failed: {e}")
        return {"status": "error", "error": f"write: {e}", "events": len(events), **meta}

    return {"status": "ok", "events": len(events), "archive_key": archive_key,
            "latest_key": latest_key, "size_kb": round(size_kb, 1),
            "provider": provider, **meta}


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def _gather_sgo(run_date: str) -> tuple[list, dict]:
    """Legacy: SGO /v2/events for all markets (already SGO-shaped)."""
    from mlb_core.odds.sgo import SgoClient
    client = SgoClient()
    before = _safe_usage(client)
    events = client.fetch_mlb_slate(run_date=run_date)
    consumed = _delta(before, _safe_usage(client))
    return events, {"objects_consumed": consumed}


def _gather_parlay(run_date: str) -> tuple[list, dict]:
    """ParlayAPI covered markets (adapted to SGO shape) MERGED with SGO inning
    markets. Also banks OddsAccum artifacts so the standalone accumulator can
    be retired (credit unification). Falls back to pure SGO if ParlayAPI yields
    nothing."""
    import pandas as pd
    from nba.config import (PARLAY_PROP_MARKETS, oddsaccum_csv_key,
                            oddsaccum_latest_key, oddsaccum_raw_key)
    from nba.odds import extract, parlay_extract
    from nba.odds.parlayapi import ParlayApiClient
    from mlb_core.odds.parlay_adapter import merge_events, parlay_slate_to_sgo_events
    from mlb_core.odds.sgo import SgoClient
    from mlb_core.storage import write_bytes, write_csv

    markets = PARLAY_PROP_MARKETS.get(SPORT, [])
    client = ParlayApiClient(delay=0.2)
    game_lines = client.get_slate(SPORT, markets="game")

    props_by_id, raw_props, prop_rows = {}, [], []
    for ev in game_lines or []:
        eid = ev.get("id")
        if not eid:
            continue
        obj = client.get_event_props(SPORT, eid, markets)
        if obj:
            props_by_id[eid] = obj
            raw_props.append(obj)
            prop_rows.extend(parlay_extract.flatten_parlay_props(obj, SPORT))

    # SGO inning markets (one cheap call returns all markets; we use innings only).
    try:
        sgo_events = SgoClient().fetch_mlb_slate(run_date=run_date)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"snapshot(parlay): SGO inning fetch failed: {e}")
        sgo_events = []

    parlay_events = parlay_slate_to_sgo_events(game_lines, props_by_id, run_date)
    merged = merge_events(parlay_events, sgo_events, run_date)

    # Credit unification: bank the ParlayAPI pull to OddsAccum (raw + best-book).
    hhmm = datetime.now(timezone.utc).strftime("%H%M")
    try:
        write_bytes(json.dumps(raw_props).encode(),
                    oddsaccum_raw_key(SPORT, run_date, "props", hhmm))
        best = extract.best_book_props(prop_rows)
        if best:
            write_csv(pd.DataFrame(best), oddsaccum_csv_key(SPORT, run_date, "props", hhmm))
        write_bytes(json.dumps(game_lines).encode(),
                    oddsaccum_raw_key(SPORT, run_date, "game_lines", hhmm))
        gl_rows = extract.flatten_game_lines(game_lines)
        if gl_rows:
            write_csv(pd.DataFrame(gl_rows), oddsaccum_csv_key(SPORT, run_date, "game_lines", hhmm))
        write_bytes(json.dumps({
            "sport": SPORT, "date": run_date, "hhmm": hhmm,
            "events": len(game_lines or []), "events_priced": len(raw_props),
            "prop_rows": len(prop_rows), "merged_events": len(merged),
            "credits_remaining": client.credits_remaining,
            "timestamp": started_iso(),
        }, default=str).encode(), oddsaccum_latest_key(SPORT))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"snapshot(parlay): OddsAccum write failed (non-fatal): {e}")

    meta = {"parlay_events": len(parlay_events), "sgo_events": len(sgo_events),
            "merged_events": len(merged), "credits_remaining": client.credits_remaining}

    if not merged:
        logger.warning("snapshot(parlay): empty merge — falling back to pure SGO")
        return sgo_events, {**meta, "fallback": "sgo"}
    return merged, meta


def started_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_usage(client) -> dict | None:
    try:
        return client.get_usage()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"SGO usage check failed: {e}")
        return None


def _delta(before: dict | None, after: dict | None) -> int | None:
    if not before or not after:
        return None
    try:
        b = int(before["rateLimits"]["per-month"]["current-entities"])
        a = int(after["rateLimits"]["per-month"]["current-entities"])
        return a - b
    except (KeyError, TypeError, ValueError):
        return None
