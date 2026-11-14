"""The official NBA injury report, captured AS PUBLISHED (MIN-08): the decision-time feed.

WHY THIS EXISTS: `cases.select_designation` answers "what was the designation at tip - 60m"
from banked report versions. That answer is only honest if the bank holds the versions that
had actually been published by then -- reconstructed after the fact from a partial set, the
"decision-time" designation is whatever the archive happens to contain (the DEC-02 audit's
one stale field is the small version of this failure). The historical bank
(nba.data.nba_injury_report.backfill) covers complete past days; THIS module is the forward
path: run it on a cadence during the day and it banks every version of today's report that
has been published so far, each with its raw PDF and provenance, so a designation selected
later from the bank is reproducible byte-for-byte, not reconstructed.

THE GRID (probed live 2026-11-13 on 2026-11-12): the league publishes on quarter-hour file
stamps 11:00-23:45 ET; in the current era the filename stamp EQUALS the title time, and the
old hourly filename no longer exists (403). capture_day therefore tries every grid stamp
whose title time has passed, and:

  * banks what exists (parsed CSV + raw PDF + provenance, via nba_injury_report.bank_report);
  * skips what is already banked (idempotent; a capture run is safe to repeat at any cadence);
  * treats a 403 as "not yet published", NEVER as missing: capture does not touch
    _missing.json (the backfill's permanent record). A stamp unpublished at 18:00 may exist
    at 18:15; only the backfill, run over complete days, may declare a stamp missing;
  * records each run in a per-day capture ledger (NBA/injuries/official/_capture/<date>.json):
    which stamps are banked, when each was captured, how many runs have happened. The ledger
    is the feed's provenance: it proves a version was in the bank before the tip it feeds.

REPRODUCIBILITY: load_banked_reports(days) assembles the designation frame from the bank
through mlb_core.storage (GCS or local mirror -- the local-dir reader in
nba.props.official_availability covers local stores), and verify_day(day) re-parses the
banked RAW PDFs and compares them row-for-row against the banked parses: a day that verifies
is a day whose decision-time designations are reproducible from banked data alone.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timezone
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from nba.config import TIMEZONE
from nba.data import nba_injury_report as R

log = logging.getLogger(__name__)

_ET = ZoneInfo(TIMEZONE)
CAPTURE_PREFIX = f"{R.PREFIX}/_capture"
# Quarter-hour stamps across the publication window: 52 candidate versions per game day.
STAMP_MINUTES = R.GRID_STAMP_MINUTES
STAMP_HOURS = R.GRID_STAMP_HOURS


# ----------------------------------------------------------------------------------------------- grid
def stamp_grid(day: date) -> list:
    """Every candidate report stamp (aware ET) for one ET day: GRID hours x quarter hours."""
    return [datetime.combine(day, time(h, m), tzinfo=_ET)
            for h in STAMP_HOURS for m in STAMP_MINUTES]


def due_stamps(day: date, now: datetime) -> list:
    """Grid stamps whose publication time has passed at `now` (title time == filename stamp
    in the current era, so a stamp is due as soon as its own time is reached). Stamps for a
    future day are never due; every stamp of a past day is."""
    now_et = now.astimezone(_ET) if now.tzinfo else now.replace(tzinfo=_ET)
    return [ts for ts in stamp_grid(day) if ts <= now_et]


# ----------------------------------------------------------------------------------------------- ledger
def ledger_key(day: date) -> str:
    return f"{CAPTURE_PREFIX}/{day.isoformat()}.json"


def _read_ledger(store, day: date) -> dict:
    key = ledger_key(day)
    if store.exists(key):
        return json.loads(store.read_bytes(key).decode())
    return {"day": day.isoformat(), "runs": 0, "captured": {}}


def _write_ledger(store, day: date, ledger: dict) -> None:
    store.write_bytes(json.dumps(ledger, indent=1, sort_keys=True).encode(), ledger_key(day))


# ----------------------------------------------------------------------------------------------- capture
def capture_day(day: date, now: Optional[datetime] = None, fetch=R.fetch_report,
                parse=R.parse_pdf, store=None, delay: float = 0.0) -> dict:
    """Bank every due, not-yet-banked version of `day`'s report. Idempotent.

    -> {"day", "due", "captured": [HHMM...], "already_banked", "not_yet_published",
        "failed": [{stamp, error}]}. A parse or fetch failure on one stamp is counted and
    does not stop the run (a later run retries it: it is neither banked nor declared
    missing). `now` is injectable for tests; the storage module is resolved lazily so tests
    can substitute a fake store."""
    if store is None:
        from mlb_core import storage as store
    now = now or datetime.now(_ET)
    ledger = _read_ledger(store, day)
    out = {"day": day.isoformat(), "due": 0, "captured": [], "already_banked": 0,
           "not_yet_published": 0, "failed": []}
    for ts in due_stamps(day, now):
        out["due"] += 1
        key = R.bank_key(ts)
        stamp = ts.strftime("%H%M")
        if store.exists(key):
            out["already_banked"] += 1
            ledger["captured"].setdefault(stamp, {"banked_pre_capture": True})
            continue
        try:
            got = fetch(ts)
            if delay:
                import time as _time
                _time.sleep(delay)
            if got is None:
                out["not_yet_published"] += 1
                continue
            url, pdf = got
            df = parse(pdf)
            R.bank_report(df, ts, pdf, source_url=url)
            ledger["captured"][stamp] = {
                "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_url": url, "source_pdf_sha256": R.pdf_sha256(pdf), "rows": len(df)}
            out["captured"].append(stamp)
        except Exception as exc:                                                     # noqa: BLE001
            log.warning("capture %s %s failed: %r", day, stamp, exc)
            out["failed"].append({"stamp": stamp, "error": f"{type(exc).__name__}: {exc}"})
    ledger["runs"] += 1
    ledger["last_run_at_utc"] = datetime.now(timezone.utc).isoformat()
    _write_ledger(store, day, ledger)
    return out


# ----------------------------------------------------------------------------------------------- read / verify
def load_banked_reports(days: Sequence, store=None) -> pd.DataFrame:
    """The designation frame (nba_injury_report.COLUMNS; provenance columns ride along when
    present) assembled from the bank for the given ET days, via mlb_core.storage -- the
    input select_designation consumes, reproducible from banked data alone."""
    if store is None:
        from mlb_core import storage as store
    frames = []
    for day in days:
        d = day if isinstance(day, date) else date.fromisoformat(str(day))
        for ts in stamp_grid(d):
            key = R.bank_key(ts)
            if store.exists(key):
                frames.append(pd.read_csv(_bytes_io(store.read_bytes(key))))
    if not frames:
        return pd.DataFrame(columns=R.COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["report_ts_et", "game_date", "player"]).reset_index(drop=True)


def compare_frames(banked: pd.DataFrame, reparsed: pd.DataFrame) -> dict:
    """Row-for-row comparison on the parse columns (COLUMNS), order-insensitive.
    -> {"equal", "n_banked", "n_reparsed", "only_in_banked": [...], "only_in_reparsed": [...]}
    with row keys (report_ts_et, player, team_code, status) for the differing rows."""
    def keyset(df: pd.DataFrame) -> set:
        cols = [c for c in R.COLUMNS if c in df.columns]
        # fillna BEFORE astype(str): a banked CSV reads an empty player (team-flag rows) back
        # as NaN while a fresh parse carries None -- both must key as "".
        return set(map(tuple, df[cols].fillna("").astype(str).itertuples(index=False, name=None)))

    a, b = keyset(banked), keyset(reparsed)
    def brief(rows):
        # COLUMNS order: report_ts_et 0, team_code 5, player 7, status 8
        return [{"report_ts_et": r[0], "player": r[7], "team_code": r[5], "status": r[8]}
                for r in sorted(rows)[:10]]
    return {"equal": a == b, "n_banked": len(a), "n_reparsed": len(b),
            "only_in_banked": brief(a - b), "only_in_reparsed": brief(b - a)}


def verify_day(day: date, store=None, parse=R.parse_pdf) -> dict:
    """Re-parse every banked RAW PDF for `day` and compare against the banked parses.
    A day verifies when the banked rows are exactly what the banked PDFs parse to -- the
    decision-time designations for that day are then reproducible from banked data alone.
    Versions banked without a raw PDF (none should exist) are reported, not silently passed."""
    if store is None:
        from mlb_core import storage as store
    d = day if isinstance(day, date) else date.fromisoformat(str(day))
    banked_frames, reparsed_frames, no_raw = [], [], []
    for ts in stamp_grid(d):
        key = R.bank_key(ts)
        if not store.exists(key):
            continue
        banked_frames.append(pd.read_csv(_bytes_io(store.read_bytes(key))))
        raw_key = key.replace("/report_", "/raw_report_").replace(".csv", ".pdf")
        if not store.exists(raw_key):
            no_raw.append(ts.strftime("%H%M"))
            continue
        reparsed_frames.append(parse(store.read_bytes(raw_key)))
    banked = pd.concat(banked_frames, ignore_index=True) if banked_frames else pd.DataFrame(columns=R.COLUMNS)
    reparsed = pd.concat(reparsed_frames, ignore_index=True) if reparsed_frames else pd.DataFrame(columns=R.COLUMNS)
    out = compare_frames(banked, reparsed)
    out.update({"day": d.isoformat(), "versions": len(banked_frames), "versions_without_raw": no_raw})
    out["equal"] = bool(out["equal"] and not no_raw)
    return out


def _bytes_io(data: bytes):
    import io
    return io.BytesIO(data)
