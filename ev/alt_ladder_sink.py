"""
ev.alt_ladder_sink -- rung-level sink for alternate-ladder player-prop quotes, extracted from the
EVSweep raw ParlayAPI payloads.

WHY: the NFL sweep's consensus path (ev/props.py) counts one-sided quotes (n_onesided) and discards
them, and nothing persists the multi-point ladders some books post inside their main markets (Novig's
two-sided receiving ladders, Bovada's one-sided milestones, the DFS apps' milestone ladders). The
2026-11-12 ladder spike (~/workspace/beezy/ladder-spike-2026-11-12.md) found the 2024/2025 pricing
test impossible for exactly this reason: the quotes flowed through the system and were thrown away.
The RAW payloads themselves are banked (EVSweep/nfl_props/raw/{ET day}/{event}_{HHMMSS}.json, the
HHMMSS is UTC), so the sink is an EXTRACTION over them -- zero new ParlayAPI credits, and it backfills
to the first banked payload (2026-10-04).

A quote lands in the sink when the consensus path cannot use it as a plain main-line price:
  * its market key is an *_alternate market (Quote.alt), or
  * it is one-sided (Over only or Under only -- the n_onesided stream), or
  * its book posts >= 2 distinct points for the same (player, stat) in the payload, i.e. it is part
    of a ladder (the main rung of a ladder is kept too, so each ladder is complete).

Sink (GCS, via mlb_core.storage):  EVSweep/nfl_props/alt_ladder/{ET day}.jsonl.gz
One JSON object per quote with the game's commence timestamp on EVERY row, so the pre-kickoff
filter (quote_ts < commence_ts) is computable from the sink alone -- the failure that made the
2025 BettingPros archive unusable cannot recur here.

RETENTION: the raw prefix carries no bucket lifecycle rule; this job additionally sets an
event-based hold on every raw payload it processes, so the source payloads cannot be deleted
(or overwritten) until a hold is deliberately released. Holds are best-effort: a hold failure is
logged and counted, never fatal.

Run (Cloud Run job ev-alt-ladder-extract, every 15 min):
  python -m ev.alt_ladder_sink                 # re-extract the trailing 3 ET days (idempotent)
  python -m ev.alt_ladder_sink --backfill      # every banked raw day, 2026-10-04 onward
Re-extraction OVERWRITES the day file, so a re-run is a repair, not a duplicate.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ev.kalshi_map import game_key
from ev.props import DFS_BOOKS, parse_event_props
from ev.sports.nfl import TEAM_NAMES

log = logging.getLogger("ev.alt_ladder_sink")
_ET = ZoneInfo("America/New_York")
RAW_PREFIX = "EVSweep/nfl_props/raw"
SINK_PREFIX = "EVSweep/nfl_props/alt_ladder"
SOURCE = "parlayapi"


# ----------------------------------------------------------------------------- payload -> rows
def _event_game(payload: dict):
    """Payload -> (game_key, commence datetime UTC) or None if a team name is unknown. Same mapping
    the sweep applies to slate events (ev.sweep_nfl_props.event_game); payloads carry the same fields."""
    away, home = TEAM_NAMES.get(payload.get("away_team")), TEAM_NAMES.get(payload.get("home_team"))
    if not away or not home or not payload.get("commence_time"):
        return None
    start = datetime.fromisoformat(payload["commence_time"].replace("Z", "+00:00"))
    return game_key("nfl", start.astimezone(_ET).date().isoformat(), away, home), start


def snapshot_ts(day: str, filename: str) -> datetime:
    """Reconstruct a raw payload's fetch instant: the sweep files raw payloads under the cycle's ET
    day with a UTC HHMMSS suffix ({event}_{HHMMSS}.json), so the UTC date is the ET day or a
    neighbour -- the unique candidate whose ET date matches the directory wins."""
    hhmmss = filename.rsplit("_", 1)[1].split(".")[0]
    hh, mm, ss = int(hhmmss[:2]), int(hhmmss[2:4]), int(hhmmss[4:6])
    et_day = datetime.fromisoformat(day).date()
    for delta in (0, -1, 1):
        cand = datetime.combine(et_day + timedelta(days=delta),
                                datetime.min.time(), tzinfo=timezone.utc).replace(hour=hh, minute=mm, second=ss)
        if cand.astimezone(_ET).date() == et_day:
            return cand
    raise ValueError(f"cannot place {day}/{filename} in ET")


def extract_rows(payload: dict, quote_ts: datetime, raw_object: str = "") -> list[dict]:
    """One ParlayAPI single-event payload -> sink rows (see module docstring for the selection rule)."""
    eg = _event_game(payload)
    if eg is None:
        return []
    gk, start = eg
    quotes, _ = parse_event_props(payload)
    points: dict = defaultdict(set)
    for q in quotes:
        points[(q.book, q.player_norm, q.stat)].add(q.point)
    ladder_groups = {k for k, pts in points.items() if len(pts) >= 2}
    rows = []
    for q in quotes:
        if not (q.alt or not q.two_sided or (q.book, q.player_norm, q.stat) in ladder_groups):
            continue
        rows.append({
            "source": SOURCE,
            "sport": "nfl",
            "stat": q.stat,
            "alt": bool(q.alt),
            "book": q.book,
            "is_dfs": q.book in DFS_BOOKS,
            "player": q.player,
            "player_norm": q.player_norm,
            "player_id": q.player_id,
            "team_hint": q.team_hint,
            "game_key": gk,
            "event_id": payload.get("id"),
            "rung": q.point,
            "over_odds": q.over,
            "under_odds": q.under,
            "two_sided": bool(q.two_sided),
            "quote_ts": quote_ts.isoformat(),
            "commence_ts": start.isoformat(),
            "mins_to_kick": round((start - quote_ts).total_seconds() / 60.0, 1),
            "raw_object": raw_object,
        })
    rows.sort(key=lambda r: (r["player_norm"], r["stat"], r["book"], r["rung"]))
    return rows


# ----------------------------------------------------------------------------- storage (faked in tests)
def _client():
    from google.cloud import storage as gcs
    return gcs.Client()


def _bucket_name() -> str:
    from mlb_core.storage import _get_bucket
    return _get_bucket()


def list_raw_objects(day: str) -> list[str]:
    bucket = _client().bucket(_bucket_name())
    return sorted(b.name for b in bucket.list_blobs(prefix=f"{RAW_PREFIX}/{day}/"))


def list_raw_days() -> list[str]:
    bucket = _client().bucket(_bucket_name())
    pages = bucket.list_blobs(prefix=f"{RAW_PREFIX}/", delimiter="/")
    _ = list(pages)                                            # prefixes populate only after iteration
    return sorted(p.rstrip("/").rsplit("/", 1)[1] for p in (pages.prefixes or ()))


def read_object(key: str) -> bytes:
    from mlb_core.storage import read_bytes
    return read_bytes(key)


def write_sink(day: str, rows: list[dict]) -> str:
    from mlb_core.storage import write_bytes
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        for r in rows:
            gz.write((json.dumps(r, sort_keys=True) + "\n").encode())
    key = f"{SINK_PREFIX}/{day}.jsonl.gz"
    write_bytes(buf.getvalue(), key)
    return key


def hold_object(key: str) -> bool:
    """Set an event-based hold (permanent retention until deliberately released). Best-effort."""
    try:
        blob = _client().bucket(_bucket_name()).blob(key)
        blob.event_based_hold = True
        blob.patch()
        return True
    except Exception as e:                                     # noqa: BLE001
        log.warning("hold failed for %s: %r", key, e)
        return False


# ----------------------------------------------------------------------------- runs
def extract_day(day: str, holds: bool = True) -> dict:
    """(Re-)extract one ET day of raw payloads into its sink file. Idempotent: the day file is
    overwritten wholesale from the payloads present, so re-runs repair rather than duplicate."""
    rows, files, held = [], 0, 0
    for key in list_raw_objects(day):
        fname = key.rsplit("/", 1)[1]
        try:
            payload = json.loads(read_object(key))
            rows.extend(extract_rows(payload, snapshot_ts(day, fname), key))
        except Exception:                                      # noqa: BLE001
            log.exception("extract failed for %s", key)
            continue
        files += 1
        if holds:
            held += 1 if hold_object(key) else 0
    rows.sort(key=lambda r: (r["quote_ts"], r["game_key"], r["player_norm"], r["stat"], r["book"], r["rung"]))
    key = write_sink(day, rows)
    stats = {"day": day, "raw_files": files, "rows": len(rows), "sink": key, "holds_set": held,
             "books": sorted({r["book"] for r in rows}), "stats": sorted({r["stat"] for r in rows})}
    log.info("ALT_LADDER %s", json.dumps(stats))
    return stats


def trailing_days(n: int, now: datetime | None = None) -> list[str]:
    today = (now or datetime.now(timezone.utc)).astimezone(_ET).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="trailing ET days to re-extract (job default)")
    ap.add_argument("--backfill", action="store_true", help="extract every banked raw day instead")
    ap.add_argument("--no-holds", action="store_true", help="skip event-based holds on raw payloads")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    days = list_raw_days() if a.backfill else trailing_days(a.days)
    log.info("alt-ladder extraction: %d day(s) %s..%s", len(days), days[0] if days else "-", days[-1] if days else "-")
    total = {"days": 0, "raw_files": 0, "rows": 0, "holds_set": 0}
    for day in days:
        s = extract_day(day, holds=not a.no_holds)
        total["days"] += 1
        for k in ("raw_files", "rows", "holds_set"):
            total[k] += s[k]
    log.info("ALT_LADDER_DONE %s", json.dumps(total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
