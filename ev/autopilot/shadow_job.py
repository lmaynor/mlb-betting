"""
ev.autopilot.shadow_job -- the AUT-01 Phase 0 LIVE shadow as a Cloud Run Job
(python -m ev.autopilot.shadow_job; deploy/setup_autopilot_shadow.sh).

WHAT IT IS.  One firing re-derives the Phase 0 shadow ledger for the live window
from the DEC-08 accumulator bank (OddsAccum/{feed}/raw/{ET day}/props_{HHMM}.json,
snapshot day folders >= --from-day), attaches closes from the same snapshot series
(ev.autopilot.closes), and banks the result to the data bucket:

    Autopilot/shadow_ledger/latest.jsonl.gz                   derived ledger, overwritten per firing
    Autopilot/shadow_ledger/latest.thin_anchor_annex.jsonl.gz derived annex, overwritten per firing
    Autopilot/shadow_ledger/runs/{run_id}.json                per-firing summary, create-only

The ledger is a DERIVED artifact -- any firing reproduces it in full from the bank,
first-sighting dedupe and close attachment included -- so the latest objects are
overwritten rather than chunked (the Novig bank's _verdicts/latest.json precedent).
A firing whose window has not advanced banks byte-identical rows with a fresh run
summary; nothing accumulates twice.

ARMING.  The kill switch is the AUTOPILOT_SHADOW_ARMED env var, set ONLY in this
job's environment (deploy/setup_autopilot_shadow.sh).  This module never passes a
dry-run flag: run_stream refuses unless the config loads as armed, and this module
additionally fails fast before touching the bank when it does not.  Arming or
disarming is therefore a job-env change, never a code change.  AUTOPILOT_ENABLE_SAVES
is never set here: saves stay off behind their Dec 21 gate.

NEVER AN ORDER PATH.  This module reads snapshots and writes ledger JSON only.
It imports no venue client and no credential of any kind beyond the bucket access
the job's service account already holds, and the package-wide no-order-path
assertion (tests/test_autopilot_shadow.py) covers this file too.  Simulated fills
exist only as rows in the shadow ledger, priced at the banked ask by the stream.
NOV-01 (the ev.novig paper loop and its schedulers) is untouched: the shadow
consumes the accumulator bank, not the paper loop's ledger.

TIMING.  Daily at 06:30 America/Chicago (after the 06:00 closing-panel job), so
each firing scores the prior ET day's completed snapshot series with closes
attached.  The 14-day live window is pinned by the job's --from-day argument.
"""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import datetime, timezone

from ev.autopilot.closes import attach_closes
from ev.autopilot.config import load_config
from ev.autopilot.stream import StoreFeedSource, run_stream

LEDGER_KEY = "Autopilot/shadow_ledger/latest.jsonl.gz"
ANNEX_KEY = "Autopilot/shadow_ledger/latest.thin_anchor_annex.jsonl.gz"
RUNS_PREFIX = "Autopilot/shadow_ledger/runs"


class DayFloorStore:
    """Delegates to a shadow_bank store, hiding accumulator snapshot day folders
    earlier than the window's first day.  Keys under the OddsAccum prefixes are
    {day}/{file}; anything else passes through untouched."""

    def __init__(self, store, from_day: str):
        self.store = store
        self.from_day = from_day

    def list(self, prefix: str) -> list:
        out = []
        for key in self.store.list(prefix):
            parts = key[len(prefix):].split("/")
            if len(parts) == 2 and parts[0] < self.from_day:
                continue
            out.append(key)
        return out

    def read(self, key: str) -> bytes:
        return self.store.read(key)

    def put(self, key: str, data: bytes) -> None:
        self.store.put(key, data)

    def write_new(self, key: str, data: bytes) -> bool:
        return self.store.write_new(key, data)


def _jsonl_gz(rows: list) -> bytes:
    text = "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows)
    return gzip.compress(text.encode("ascii"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AUT-01 Phase 0 live shadow job (banked snapshots in, "
                                             "banked shadow ledger out; shadow only, no order path)")
    ap.add_argument("--from-day", required=True,
                    help="first ET snapshot day folder of the live window (YYYY-MM-DD)")
    ap.add_argument("--source", default="gcs",
                    help="shadow_bank store kind for the accumulator bank and the ledger bank "
                         "(gcs | gcloud | dir:PATH)")
    ap.add_argument("--bucket", default=None)
    a = ap.parse_args(argv)

    cfg = load_config()
    if not cfg.armed:
        raise SystemExit("AUTOPILOT_SHADOW_ARMED is off: the live shadow job refuses to run "
                         "and banks nothing (arm via the job environment only)")

    from ev.novig import shadow_bank as sb
    store = DayFloorStore(sb.make_store(a.source, a.bucket), a.from_day)
    res = run_stream(StoreFeedSource(store), cfg)          # no dry-run: the kill switch governs
    rows = attach_closes(res["rows"], res["records"], cfg)
    annex = [r for r in rows if r["status"] == "ANNEX_THIN_ANCHOR"]

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    store.put(LEDGER_KEY, _jsonl_gz(rows))
    store.put(ANNEX_KEY, _jsonl_gz(annex))
    fired = [r["quote_observed_at"] for r in rows]
    summary = {"run_id": run_id, "job": "autopilot-shadow", "from_day": a.from_day,
               "armed": cfg.armed, "enable_saves": cfg.enable_saves,
               "funnel": res["funnel"], "rows": len(rows), "annex": len(annex),
               "filled": sum(1 for r in rows if r["status"] == "FILLED"),
               "first_fired_at": min(fired) if fired else None,
               "last_fired_at": max(fired) if fired else None,
               "ledger_key": LEDGER_KEY, "annex_key": ANNEX_KEY}
    store.write_new(f"{RUNS_PREFIX}/{run_id}.json",
                    json.dumps(summary, indent=1, sort_keys=True).encode("ascii"))
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
