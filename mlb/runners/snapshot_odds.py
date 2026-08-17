"""
runners/snapshot_odds.py — Fetch the MLB slate and save it SGO-shaped to GCS.

Runs on Cloud Scheduler via main.py's /snapshot-odds endpoint.

Provider (env ODDS_PRIMARY, default "parlay"):
  - "parlay": ParlayAPI primary (HR/K/OUTS/HITS/TB/ER props + game ML) adapted to
              SGO shape (mlb_core.odds.parlay_adapter), MERGED with SGO inning
              markets (NRFI/1I-3way/F5/F5-ML/F1H, which ParlayAPI can't express).
  - "sgo":    legacy — SGO /v2/events for all markets. Do NOT run this at the
              current 8x/day snapshot cadence -- combined with SGO's 2500
              entities/month amateur-tier quota, this caused a 24+ hour SGO
              outage on 2026-08-09/10 (see
              docs/solutions/integration-issues/odds-primary-cadence-mismatch.md).
              Only safe at ~4x/day or below.

The default here and in deploy/deploy_service.sh's --update-env-vars must
both stay "parlay" -- this is deliberately the safe-by-default direction so
an unset env var (a fresh environment, a dropped var, a careless manual
call) can never silently reproduce the incident above.

Cadence: ParlayAPI runs ~8x/day, SGO only 4x/day (free tier ~2500 entities/mo).
On the 4 SGO runs pass include_sgo=true (fresh inning markets); the other ~4 runs
pass include_sgo=false and CARRY FORWARD inning markets from the prior snapshot,
so inning runners never see an empty book and SGO stays at 4 pulls/day.

day_offset: 0 = today (default), 1 = tomorrow. Next-day lines post ~9pm ET, so
late-night jobs pass day_offset=1 to bank tomorrow's slate. ParlayAPI events are
dated per-event from commence_time, so a multi-day slate resolves correctly
regardless; day_offset only steers which ET day the SGO inning fetch targets.

Credit guard (implicit, no header): each ParlayAPI run estimates spend as
events*markets (+ game-line + discovery) and tracks a monthly tally in GCS. When
the month would exceed PARLAY_CREDIT_CEILING (default 19500), it skips the
expensive per-event props for that run (still writes game lines) -- so we push
toward 20k without ever going over, no matter the slate size.

Output (SGO shape, same paths -> runners untouched):
     {out_prefix}/{YYYY-MM-DD}/snapshot_{HHMM_UTC}.json   (archive)
     {out_prefix}/latest.json                             (runner read target)
out_prefix defaults to "Odds/sgo"; pass a scratch prefix for a shadow run.
"""
import calendar
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DEFAULT_PREFIX = "Odds/sgo"
SPORT = "baseball_mlb"
_ET = ZoneInfo("America/New_York")
CREDIT_CEILING = int(os.environ.get("PARLAY_CREDIT_CEILING", "19500"))


def _keys(out_prefix: str, run_date: str, hhmm: str):
    latest = f"{out_prefix}/latest.json"
    return f"{out_prefix}/{run_date}/snapshot_{hhmm}.json", latest, f"{latest}.tmp"


def _target_date(run_date: str | None, day_offset: int) -> str:
    base = (datetime.strptime(run_date, "%Y-%m-%d").date() if run_date
            else datetime.now(_ET).date())
    return (base + timedelta(days=day_offset)).isoformat()


def run(run_date: str = None, provider: str = None, out_prefix: str = DEFAULT_PREFIX,
        day_offset: int = 0, include_sgo: bool = None) -> dict:
    provider = (provider or os.environ.get("ODDS_PRIMARY", "parlay")).lower()
    target_date = _target_date(run_date, day_offset)
    started = datetime.now(timezone.utc)
    logger.info(f"snapshot | date={target_date} | provider={provider} | "
                f"prefix={out_prefix} | offset={day_offset} | include_sgo={include_sgo}")

    from mlb_core.config import GCS_BUCKET
    if not GCS_BUCKET:
        return {"status": "error", "error": "GCS_BUCKET not set"}

    latest_key = f"{out_prefix}/latest.json"
    try:
        if provider == "parlay":
            events, meta = _gather_parlay(target_date, latest_key, include_sgo)
        else:
            events, meta = _gather_sgo(target_date)
    except Exception as e:  # noqa: BLE001
        logger.error(f"snapshot: gather failed ({provider}): {e}")
        return {"status": "error", "error": f"gather({provider}): {e}"}

    if not events:
        logger.warning("snapshot: 0 events — leaving latest.json unchanged")
        return {"status": "error", "error": "no events returned", "events": 0, **meta}

    hhmm = started.strftime("%H%M")
    archive_key, latest_key, tmp_key = _keys(out_prefix, target_date, hhmm)
    payload = json.dumps(events, separators=(",", ":")).encode("utf-8")

    from mlb_core.storage import write_bytes, exists, delete
    try:
        write_bytes(payload, archive_key)
        write_bytes(payload, tmp_key)
        write_bytes(payload, latest_key)
        if exists(tmp_key):
            delete(tmp_key)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": f"write: {e}", "events": len(events), **meta}

    logger.info(f"snapshot ok | {len(events)} events | {len(payload)/1024:.1f}KB | {provider}")
    return {"status": "ok", "events": len(events), "archive_key": archive_key,
            "latest_key": latest_key, "provider": provider, "date": target_date, **meta}


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def _gather_sgo(run_date: str):
    from mlb_core.odds.sgo import SgoClient
    client = SgoClient()
    before = _safe_usage(client)
    events = client.fetch_mlb_slate(run_date=run_date)
    return events, {"objects_consumed": _delta(before, _safe_usage(client))}


def _gather_parlay(target_date: str, latest_key: str, include_sgo: bool | None):
    """ParlayAPI covered markets (adapted) merged with SGO inning markets.
    include_sgo: True -> fetch SGO fresh; False -> carry inning markets forward
    from the prior snapshot; None -> default False (the safe/cheap direction --
    an unset flag must never silently turn into an extra SGO call; only the 4
    explicitly-flagged daily windows should ever touch SGO)."""
    import pandas as pd
    from nba.config import (PARLAY_PROP_MARKETS, oddsaccum_csv_key,
                            oddsaccum_latest_key, oddsaccum_raw_key)
    from nba.odds import extract, parlay_extract
    from nba.odds.parlayapi import ParlayApiClient
    from mlb_core.odds import parlay_adapter as A
    from mlb_core.odds.sgo import SgoClient, load_snapshot
    from mlb_core.storage import write_bytes, write_csv

    if include_sgo is None:
        include_sgo = False
    markets = PARLAY_PROP_MARKETS.get(SPORT, [])
    client = ParlayApiClient(delay=0.2)
    game_lines = client.get_slate(SPORT, markets="game")   # ~3 credits (whole slate)
    n_events = len(game_lines or [])

    # --- credit guard: pace spend EVENLY across the month. The allowance grows
    # linearly (CEILING * day/days_in_month), so cumulative spend tracks a line to
    # ~CEILING by month end instead of front-loading. Skip the expensive per-event
    # props when this run would push the month ahead of pace.
    month = target_date[:7]
    spent = _read_credits(month)
    est_props = n_events * max(1, len(markets))
    pace_cap = _pace_ceiling(target_date)
    do_props = (spent + 3 + est_props) <= pace_cap

    props_by_id, raw_props, prop_rows = {}, [], []
    if do_props:
        for ev in game_lines or []:
            eid = ev.get("id")
            if not eid:
                continue
            obj = client.get_event_props(SPORT, eid, markets)
            if obj:
                props_by_id[eid] = obj
                raw_props.append(obj)
                prop_rows.extend(parlay_extract.flatten_parlay_props(obj, SPORT))
        _add_credits(month, 3 + n_events * max(1, len(markets)))
    else:
        logger.warning("snapshot(parlay): over month pace (%.0f, spent=%d) — "
                       "game lines only this run", pace_cap, spent)
        _add_credits(month, 3)

    parlay_events = A.parlay_slate_to_sgo_events(game_lines, props_by_id, target_date)

    if include_sgo:
        try:
            sgo_events = SgoClient().fetch_mlb_slate(run_date=target_date)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"snapshot(parlay): SGO inning fetch failed: {e}")
            sgo_events = []
        merged = A.merge_events(parlay_events, sgo_events)
    else:
        carry = A.inning_odds_only(load_snapshot(latest_key))   # carry inning fwd
        for pev in parlay_events:
            pev.setdefault("odds", {}).update(carry.get(pev["eventID"], {}))
        merged = parlay_events

    # Credit unification: bank the ParlayAPI pull to OddsAccum (raw + best-book).
    hhmm = datetime.now(timezone.utc).strftime("%H%M")
    try:
        if raw_props:
            write_bytes(json.dumps(raw_props).encode(),
                        oddsaccum_raw_key(SPORT, target_date, "props", hhmm))
            best = extract.best_book_props(prop_rows)
            if best:
                write_csv(pd.DataFrame(best), oddsaccum_csv_key(SPORT, target_date, "props", hhmm))
        write_bytes(json.dumps(game_lines).encode(),
                    oddsaccum_raw_key(SPORT, target_date, "game_lines", hhmm))
        write_bytes(json.dumps({
            "sport": SPORT, "date": target_date, "hhmm": hhmm, "events": n_events,
            "events_priced": len(raw_props), "props_pulled": do_props,
            "merged_events": len(merged), "credits_month": _read_credits(month),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, default=str).encode(), oddsaccum_latest_key(SPORT))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"snapshot(parlay): OddsAccum write failed (non-fatal): {e}")

    meta = {"parlay_events": len(parlay_events), "merged_events": len(merged),
            "props_pulled": do_props, "credits_month": _read_credits(month),
            "include_sgo": include_sgo}
    if not merged:
        if include_sgo:
            # This window was already budgeted to touch SGO -- falling back
            # to pure SGO here doesn't add any *new* SGO load.
            logger.warning("snapshot(parlay): empty merge — falling back to pure SGO")
            return _gather_sgo(target_date)[0], {**meta, "fallback": "sgo"}
        # include_sgo=False windows must never sneak in an SGO call -- a
        # sustained ParlayAPI outage would otherwise silently turn every one
        # of the 8 daily windows into an SGO caller, reproducing the
        # 2026-08-09/10 incident through a different trigger. Return empty;
        # run() already handles this by leaving latest.json untouched.
        logger.warning(
            "snapshot(parlay): empty merge on an include_sgo=False run -- "
            "NOT falling back to SGO (would defeat the 4x/day SGO budget); "
            "leaving latest.json unchanged this run"
        )
        return [], meta
    return merged, meta


# ---------------------------------------------------------------------------
# Implicit monthly credit tally (GCS) -- no header needed
# ---------------------------------------------------------------------------

def _credit_key(month: str) -> str:
    return f"OddsAccum/{SPORT}/_credits/{month}.json"


def _pace_ceiling(target_date: str) -> float:
    """Linear daily allowance: CEILING * day_of_month / days_in_month. Keeps
    cumulative monthly spend on a straight line to ~CEILING (even spread)."""
    y, m, d = (int(x) for x in target_date.split("-"))
    days_in_month = calendar.monthrange(y, m)[1]
    return CREDIT_CEILING * d / days_in_month


def _read_credits(month: str) -> int:
    from mlb_core.storage import exists, read_bytes
    key = _credit_key(month)
    if not exists(key):
        return 0
    try:
        d = json.loads(read_bytes(key))
        return int(d.get("credits", 0)) if d.get("month") == month else 0
    except Exception:  # noqa: BLE001
        return 0


def _add_credits(month: str, n: int) -> None:
    from mlb_core.storage import write_bytes
    try:
        total = _read_credits(month) + int(n)
        write_bytes(json.dumps({"month": month, "credits": total}).encode(),
                    _credit_key(month))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"credit tally write failed: {e}")


def _safe_usage(client):
    try:
        return client.get_usage()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"SGO usage check failed: {e}")
        return None


def _delta(before, after):
    if not before or not after:
        return None
    try:
        b = int(before["rateLimits"]["per-month"]["current-entities"])
        a = int(after["rateLimits"]["per-month"]["current-entities"])
        return a - b
    except (KeyError, TypeError, ValueError):
        return None
