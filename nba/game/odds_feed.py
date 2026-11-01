"""
nba.game.odds_feed -- load banked GAME-LINE events (h2h / spreads / totals) for the NBA and NHL game engines.

The odds banker (nba/odds/accumulator.py, kind="game_lines") writes TWO things per run:
  OddsAccum/{sport}/raw/{date}/game_lines_{HHMM}.json   the raw event list (list of ParlayAPI event dicts)
  OddsAccum/{sport}/latest_game_lines.json              a POINTER: {"sport","kind","date","hhmm","events": <int COUNT>,
                                                        "timestamp", ...}  (NOT the events themselves)
So the events live in the raw object the pointer references. Reading `pointer["events"]` as a list (what the runners
used to do) either raised on len(int) inside a swallowed try or matched nothing: no game odds were ever consumed.

The BettingPros prop feed (Odds/bettingpros_live/{sport}/latest.json) carries PLAYER PROPS only. It is deliberately not a
fallback here: treating prop rows as game odds would fabricate fair lines.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from mlb_core import storage
from nba.config import oddsaccum_latest_kind_key, oddsaccum_raw_key

log = logging.getLogger("nba.game.odds_feed")
_ET = ZoneInfo("America/New_York")

# a banked game-lines snapshot older than this is refused (quotes drift; stale fair lines fake edges)
MAX_AGE_MIN: float = 360.0
GAME_MARKET_KEYS = ("h2h", "spreads", "totals")


def has_game_markets(events: Any) -> bool:
    """True if at least one event carries a bookmaker market keyed h2h / spreads / totals (i.e. real game odds)."""
    if not isinstance(events, list):
        return False
    for ev in events:
        if not isinstance(ev, dict):
            continue
        for b in ev.get("bookmakers") or []:
            if not isinstance(b, dict):
                continue
            for mk in b.get("markets") or []:
                if isinstance(mk, dict) and mk.get("key") in GAME_MARKET_KEYS:
                    return True
    return False


def _valid_events(obj: Any) -> Optional[List[Dict[str, Any]]]:
    """The raw payload must be a list of dicts (or a dict wrapping such a list under 'events'); else None."""
    if isinstance(obj, dict):
        obj = obj.get("events")
    if not isinstance(obj, list):
        return None
    events = [e for e in obj if isinstance(e, dict)]
    return events or None


def read_banked_game_lines(
    parlay_key: str,
    now: Optional[datetime] = None,
    max_age_min: float = MAX_AGE_MIN,
) -> Tuple[Optional[List[Dict[str, Any]]], Dict[str, Any]]:
    """
    Newest banked game-line events for a ParlayAPI sport key ('basketball_nba' | 'icehockey_nhl') -> (events | None, meta).
    meta always carries `ok` (bool), plus `reason` when not ok and `age_min` / `snapshot_ts` / `raw_key` when known.
    """
    now = now or datetime.now(timezone.utc)
    ptr_key = oddsaccum_latest_kind_key(parlay_key, "game_lines")
    meta: Dict[str, Any] = {"ok": False, "pointer_key": ptr_key}
    try:
        if not storage.exists(ptr_key):
            return None, {**meta, "reason": "no latest_game_lines.json pointer yet (game_lines banker has not run)"}
        ptr = json.loads(storage.read_bytes(ptr_key).decode("utf-8"))
        if not isinstance(ptr, dict):
            return None, {**meta, "reason": f"pointer is {type(ptr).__name__}, expected a dict"}
        if ptr.get("skipped") or not ptr.get("date") or not ptr.get("hhmm"):
            return None, {**meta, "reason": f"pointer has no snapshot (skipped={ptr.get('skipped')}, reason={ptr.get('reason')})"}
        ts = datetime.fromisoformat(str(ptr["timestamp"]).replace("Z", "+00:00"))
        age = (now - ts).total_seconds() / 60.0
        meta.update(snapshot_ts=ts.isoformat(), age_min=round(age, 1), banked_events=ptr.get("events"))
        if age > max_age_min:
            return None, {**meta, "reason": f"game_lines snapshot is {age:.0f} min old (> {max_age_min:.0f}); refusing stale quotes"}
        raw_key = oddsaccum_raw_key(parlay_key, ptr["date"], "game_lines", ptr["hhmm"])
        meta["raw_key"] = raw_key
        if not storage.exists(raw_key):
            return None, {**meta, "reason": f"raw game_lines snapshot missing: {raw_key}"}
        events = _valid_events(json.loads(storage.read_bytes(raw_key).decode("utf-8")))
        if not events:
            return None, {**meta, "reason": f"raw snapshot {raw_key} is not a non-empty list of event dicts"}
        if not has_game_markets(events):
            return None, {**meta, "reason": f"raw snapshot {raw_key} carries no h2h/spreads/totals markets"}
        log.info("Loaded %d game-line events from %s (age %.0f min)", len(events), raw_key, age)
        return events, {**meta, "ok": True, "events": len(events)}
    except Exception as exc:                                                            # noqa: BLE001
        # logged WITH traceback: the old swallowed warning is how "no odds were ever consumed" went unnoticed
        log.exception("banked game-lines load failed for %s", parlay_key)
        return None, {**meta, "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}


def events_for_day(
    events: List[Dict[str, Any]],
    date_str: str,
) -> Tuple[List[Dict[str, Any]], int]:
    """Events whose commence_time falls on `date_str` in ET (the repo's game-day convention).
    Events with no parseable start are kept (a feed quirk must not drop a game) and counted -> (kept, n_undated)."""
    kept: List[Dict[str, Any]] = []
    undated = 0
    for ev in events:
        comm = ev.get("commence_time") or ev.get("start_time") or ""
        try:
            day = datetime.fromisoformat(str(comm).replace("Z", "+00:00")).astimezone(_ET).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            undated += 1
            kept.append(ev)
            continue
        if day == date_str:
            kept.append(ev)
    if undated:
        log.warning("%d event(s) had no parseable commence_time; kept for %s without a day check", undated, date_str)
    return kept, undated


def safe_price(p: Any) -> Optional[int]:
    """American price as int, or None for None / NaN / non-numeric / zero (a book with a null price must not abort the run)."""
    try:
        v = float(p)
    except (TypeError, ValueError):
        return None
    if v != v or v == 0.0:
        return None
    return int(v)


UNVALIDATED_LABEL = "UNVALIDATED model estimate, not a tested edge"


def persist_payload(
    payload: Dict[str, Any],
    out_key: str,
    archive_prefix: str,
    odds_ok: bool,
) -> Dict[str, Any]:
    """
    Write a game-prediction payload to out_key (+ the day's archive) and return what ended up in out_key.

    An EMPTY awaiting_lines payload produced because odds FAILED TO LOAD must not clobber a previous good payload for the
    same game day: that one is kept, re-written with stale=True and the reason, and the archive is left alone. (An empty
    payload from a successful load with no games that day is a real state and is written normally.)
    The day's archive is never overwritten by an empty payload when it already holds rows.
    """
    date_str = str(payload.get("date"))
    archive_key = f"{archive_prefix}/{date_str}.json"
    has_rows = bool(payload.get("rows"))

    if not has_rows and not odds_ok:
        prev = None
        try:
            if storage.exists(out_key):
                prev = json.loads(storage.read_bytes(out_key).decode("utf-8"))
        except Exception:                                                               # noqa: BLE001
            log.exception("could not read previous payload %s", out_key)
        if isinstance(prev, dict) and prev.get("rows") and prev.get("date") == date_str:
            reason = (payload.get("odds_meta") or {}).get("reason") or "odds failed to load"
            kept = {**prev, "stale": True, "stale_reason": f"odds failed to load: {reason}",
                    "stale_checked_ts": payload.get("ts")}
            storage.write_bytes(json.dumps(kept, indent=2).encode("utf-8"), out_key)
            log.warning("odds failed to load (%s); kept previous %s payload for %s and marked it stale",
                        reason, out_key, date_str)
            return kept

    blob = json.dumps(payload, indent=2).encode("utf-8")
    storage.write_bytes(blob, out_key)
    write_archive = has_rows
    if not write_archive:
        try:
            write_archive = not storage.exists(archive_key)
        except Exception:                                                               # noqa: BLE001
            write_archive = False
    if write_archive:
        storage.write_bytes(blob, archive_key)
    log.info("Saved game predictions to %s (rows=%d, archive_written=%s)", out_key, len(payload.get("rows") or []), write_archive)
    return payload
