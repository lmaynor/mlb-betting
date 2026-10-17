"""Bank the ESPN NBA injury list forward (append-only, change-only) so live-vs-training availability can be measured later.

Why: nba/data/espn_injuries.py reads the list live and persists nothing, and there is NO historical copy of what the
public list said before a given tip. Without one, every availability feature is trained on who actually did not dress
(an upper bound) and served from a list nobody has ever compared to it. This keeps the evidence:

  NBA/injuries/espn/{ET day}/injuries_{HHMMSS}.csv   one file per CHANGED snapshot (UTC time in the name; every row carries
                                                     fetched_at_utc), the espn_injuries frame columns
  NBA/injuries/espn/latest.json                      {hash, key, fetched_at, checked_at, rows, status}

Change-only storage: the list as of time t is the newest stored snapshot with fetched_at <= t, valid because `latest.json`
`checked_at` proves the poller was still running after it. `state_asof` encodes that rule and REFUSES (returns None) when
the poller's heartbeat is older than the requested time -- "no heartbeat" is unknown, never "unchanged".

FAIL CLOSED: an unusable payload (frame.attrs["ok"] False: fetch error, non-JSON, skipped entries) is NOT stored and does
not advance the heartbeat. Reads of an unreadable pointer raise rather than being treated as empty.

    GCS_BUCKET=... python3 -m nba.data.injury_bank            # one snapshot (schedule it; ~every 30 min on game days)
    MLB_BASE_DATA=/tmp/x python3 -m nba.data.injury_bank      # local dry run
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from mlb_core import storage
from nba.config import NBA_PREFIX, TIMEZONE
from nba.data import espn_injuries

log = logging.getLogger(__name__)

PREFIX = f"{NBA_PREFIX}/injuries/espn"
LATEST = f"{PREFIX}/latest.json"
COLUMNS = list(espn_injuries.COLUMNS) + ["fetched_at_utc"]
_ET = ZoneInfo(TIMEZONE)


def content_hash(frame: pd.DataFrame) -> str:
    """Order-independent hash of what the list SAYS (not of when it was fetched)."""
    cols = ["team_abbrev", "espn_player_id", "status", "detail", "updated"]
    rows = sorted(tuple("" if pd.isna(v) else str(v) for v in r) for r in frame[cols].itertuples(index=False))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def snapshot_key(ts: datetime) -> str:
    ts = ts.astimezone(timezone.utc)
    day = ts.astimezone(_ET).date().isoformat()
    return f"{PREFIX}/{day}/injuries_{ts.strftime('%H%M%S')}.csv"


def read_pointer() -> dict:
    """{} when no snapshot has ever been banked; raises on an unreadable pointer (never silently restarts history)."""
    if not storage.exists(LATEST):
        return {}
    return json.loads(storage.read_bytes(LATEST).decode())


def snapshot(now: Optional[datetime] = None, fetch: Callable[[], pd.DataFrame] = espn_injuries.fetch_injuries) -> dict:
    """One poll. -> {"status": "stored"|"unchanged"|"skipped", ...}. Never raises on a bad payload."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    frame = fetch()
    if not frame.attrs.get("ok", False):
        log.warning("injury bank: unusable payload (%s); nothing stored", frame.attrs.get("error"))
        return {"status": "skipped", "reason": frame.attrs.get("error") or "not_ok"}
    ptr = read_pointer()
    h = content_hash(frame)
    iso = now.isoformat(timespec="seconds")
    if ptr.get("hash") == h:
        storage.write_bytes(json.dumps({**ptr, "checked_at": iso}).encode(), LATEST)
        return {"status": "unchanged", "hash": h, "key": ptr.get("key")}
    out = frame.copy()
    out["fetched_at_utc"] = iso
    key = snapshot_key(now)
    storage.write_bytes(out[COLUMNS].to_csv(index=False).encode(), key)
    storage.write_bytes(json.dumps({"hash": h, "key": key, "fetched_at": iso, "checked_at": iso,
                                    "rows": int(len(out)), "status": "ok"}).encode(), LATEST)
    log.info("injury bank: stored %d rows -> %s", len(out), key)
    return {"status": "stored", "hash": h, "key": key, "rows": int(len(out))}


def load_snapshots(keys) -> pd.DataFrame:
    """Concatenate stored snapshot files (list of storage keys) into one frame with fetched_at_utc as a UTC timestamp."""
    frames = [pd.read_csv(io.BytesIO(storage.read_bytes(k))) for k in keys]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df["fetched_at_utc"] = pd.to_datetime(df["fetched_at_utc"], utc=True)
    return df


def state_asof(snaps: pd.DataFrame, ts: datetime, heartbeat: Optional[str] = None,
               max_gap_min: float = 90.0) -> Optional[pd.DataFrame]:
    """The injury list as of `ts` from banked snapshots, or None when it cannot be known.

    The state is the newest snapshot with fetched_at <= ts. It is only trusted when the poller was alive afterwards:
    `heartbeat` (latest.json checked_at, ISO) must be >= ts, and the snapshot must be within `max_gap_min` of ts OR the
    heartbeat proves nothing changed since (heartbeat >= ts). A snapshot with no earlier-than-ts row -> None."""
    ts = pd.Timestamp(ts).tz_convert("UTC") if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts, tz="UTC")
    if snaps is None or not len(snaps):
        return None
    hb = pd.Timestamp(heartbeat) if heartbeat else None
    if hb is None or hb < ts:
        return None
    earlier = snaps[snaps["fetched_at_utc"] <= ts]
    if not len(earlier):
        return None
    newest = earlier["fetched_at_utc"].max()
    return earlier[earlier["fetched_at_utc"] == newest].drop(columns=["fetched_at_utc"]).reset_index(drop=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = snapshot()
    print(json.dumps(res, indent=1))
    return 0 if res["status"] != "skipped" else 1


if __name__ == "__main__":
    sys.exit(main())
