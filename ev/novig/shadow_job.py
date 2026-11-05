"""
ev.novig.shadow_job -- the Novig SHADOW loop as a Cloud Run Job (python -m ev.novig.shadow_job), plus the CLV-gate status line.

WHAT IT IS.  One scheduled firing of the same shadow pass `python -m ev.props_exec run --novig` runs on the laptop, minus
everything the laptop needed that a job should not have:

  * consensus comes from the cloud sweep's own `EVSweep/nfl_props/consensus.json` (ev.sweep_nfl_props publishes it every
    cycle); there is NO Kalshi join (the Novig pass reads only the snapshot's per-book fair prices, and Kalshi's CDN blocks
    some Cloud Run egress IPs),
  * Novig data is the PUBLIC read-only catalog (`NovigClient("public-data")`: no key, GET /v3/public/* only),
  * state is restored from the GCS bank (ev.novig.shadow_bank) at start and banked again after every pass, so the
    per-day position-hold markers and caps survive between firings and every fill is durable,
  * the Novig PAPER-sandbox order mirror is NOT available here (it needs the paper trading key, which is not in Secret
    Manager); the venue is never constructed.

NEVER LIVE.  This module has no order client, no signing, no credentials, and passes `paper_venue=None` to
ev.novig.exec.run_pass.  Everything it writes is a SHADOW record (a paper fill against the production public book) under
`Novig/shadow_ledger/`.

TIMING.  Props consensus older than `cons_max_age_min` (25 min) is rejected by the gates, and the sweep republishes every
30-120 min.  A firing is therefore scheduled a few minutes AFTER a sweep cycle (deploy/setup_novig_shadow.sh) and works only
while the published snapshot is fresh; it exits early when no game is inside the window or no fresh snapshot arrives.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from ev.novig import clv_gate as cg
from ev.novig import exec as nx
from ev.novig import shadow_bank as sb
from ev.props_config import props_config
from mlb.execution.kalshi.ledger import Ledger
from mlb.execution.kalshi.risk import RiskGate

log = logging.getLogger("ev.novig.shadow_job")
CONSENSUS_KEY = "EVSweep/nfl_props/consensus.json"
VERDICT_MAX_AGE_H = 24.0                 # reuse the banked verdict when no new fill arrived and it is younger than this
STATUS_TAG = "NOVIG_CLV_GATE"


# ----------------------------------------------------------------------------------------------- snapshot
def load_snapshot(read=None) -> dict | None:
    """The sweep's published book consensus {'ts', 'cycle', 'events': [...]}, or None (missing / unreadable)."""
    if read is None:
        from mlb_core import storage
        read = storage.read_bytes
    try:
        snap = json.loads(read(CONSENSUS_KEY))
        datetime.fromisoformat(snap["ts"])
        return snap if isinstance(snap.get("events"), list) else None
    except Exception:                                                                # noqa: BLE001
        log.exception("consensus snapshot unavailable")
        return None


def snapshot_age_min(snap: dict, now: datetime) -> float:
    return (now - datetime.fromisoformat(snap["ts"])).total_seconds() / 60.0


def games_in_window(snap: dict, cfg, now: datetime, horizon_h: float) -> int:
    """Games in the snapshot that have not reached the pre-kick buffer and kick off within `horizon_h`."""
    n = 0
    for ev in snap.get("events", []):
        try:
            mins = (datetime.fromisoformat(ev["commence"]) - now).total_seconds() / 60.0
        except (KeyError, ValueError):
            continue
        if cfg.close_buffer_min < mins <= horizon_h * 60.0:
            n += 1
    return n


# ----------------------------------------------------------------------------------------------- CLV-gate status
def format_status(verdicts: dict, banked_rows: dict | None = None, cached: bool = False) -> str:
    """One ASCII line: how far each Novig slice is from the forward-CLV gate.  Slices with no filled bets are collapsed."""
    cov = verdicts.get("coverage", {})
    sl = verdicts.get("slices", {})
    open_n = sum(1 for v in sl.values() if v.get("open"))
    parts = [f"{STATUS_TAG} filled_bets={cov.get('filled_bets', 0)} usable_close={cov.get('with_usable_close', 0)} "
             f"open_slices={open_n}/{len(sl)} min_n={verdicts.get('rule', {}).get('min_n')} "
             f"computed_at={str(verdicts.get('computed_at'))[:19]}{' (cached)' if cached else ''}"]
    f = lambda x: "n/a" if x is None else f"{x:+.2f}%"                               # noqa: E731
    empty = []
    for k, v in sl.items():
        if not v.get("n_bets"):
            empty.append(k)
            continue
        parts.append(f"{k}: {v.get('status')} n={v.get('n')}/{v.get('n_bets')} days={v.get('n_days')} "
                     f"meanCLV={f(v.get('mean_clv_pct'))} loCI={f(v.get('ci_lo'))} consCLV={f(v.get('mean_clv_cons_pct'))}")
    if empty:
        parts.append(f"no_fills: {','.join(empty)}")
    if banked_rows:
        parts.append("banked_rows=" + ",".join(f"{k}:{v}" for k, v in sorted(banked_rows.items())))
    return " | ".join(parts)


def _cached_verdicts(store, now: datetime):
    try:
        v = json.loads(store.read(sb.VERDICT_KEY))
        age_h = (now - datetime.fromisoformat(v["computed_at"])).total_seconds() / 3600.0
        return v if -1.0 <= age_h <= VERDICT_MAX_AGE_H else None
    except Exception:                                                                # noqa: BLE001
        return None


def gate_status(store, *, now: datetime | None = None, new_fills: int = 0, source=None, gate_cfg=None) -> str:
    """Compute (or reuse) the forward-CLV verdict from the BANKED ledger and return the status line.  Never raises: a
    failure is reported in the line, because the status line must not be able to fail the shadow run.
    Recomputed when this run produced a new fill or the cached verdict is missing/old; the raw closing-line payloads are
    only read when there is at least one filled bet."""
    now = now or datetime.now(timezone.utc)
    try:
        cfg = gate_cfg or cg.load_gate_config()
        cached = None if new_fills else _cached_verdicts(store, now)
        if cached is not None:
            return format_status(cached, cached=True)
        led = sb.banked_ledger(store, tables=("orders", "decisions", "fills"))
        bets = cg.ledger_bets(led._c)
        series = books = None
        if bets:
            from nfl.props import lines as L
            lister, reader = source or L.storage_source()
            days = sorted({b["day"] for b in bets})
            series, books, _ = L.build_from_source(lister, reader, days[0], days[-1], 0.0, False)
        v = cg.compute_verdicts(bets, series, books, cfg, now)
        store.put(sb.VERDICT_KEY, json.dumps(v, sort_keys=True, default=str).encode("ascii", "backslashreplace"))
        return format_status(v, sb.summarise(led))
    except Exception as e:                                                           # noqa: BLE001
        log.exception("clv gate status failed")
        return f"{STATUS_TAG} unavailable: {type(e).__name__}: {str(e)[:160]}"


# ----------------------------------------------------------------------------------------------- the run
def run(args, *, store=None, nc=None, snapshot_fn=None, clock=None, sleep=time.sleep, ledger=None, gate_source=None) -> int:
    """One firing.  Returns the process exit code: 0 ok / nothing to do, 1 when a game was in the window but no pass
    could run (Novig unreachable, ...) or the bank could not be written -- so a broken job is a FAILED execution."""
    clock = clock or (lambda: datetime.now(timezone.utc))
    snapshot_fn = snapshot_fn or load_snapshot
    store = store or sb.make_store(args.store, args.bucket)
    cfg = props_config()
    t_end = time.monotonic() + args.max_runtime_sec
    wait_step = max(1.0, min(30.0, args.loop_seconds))
    waits_left = int(args.wait_fresh_sec // wait_step)
    day = sb.et_day(clock())
    prev = (clock().astimezone(sb._ET) - timedelta(days=1)).date().isoformat()
    nxt = (clock().astimezone(sb._ET) + timedelta(days=1)).date().isoformat()

    ledger = ledger or Ledger(":memory:")
    # Orders/fills/kv are what the caps and the one-position-per-player/stat-per-day rule read; decisions are history
    # (never read by a pass), so they are neither restored nor re-read: everything this process records is new.
    rs = sb.restore_into(ledger, store, days=[prev, day, nxt], tables=("orders", "fills", "kv"))
    known = rs["known"]
    log.info("NOVIG shadow job: restored %s from %s chunk(s) of the bank (days %s..%s)", rs["restored"], rs["chunks"], prev, nxt)

    if nc is None:
        from ev.novig.client import NovigClient
        nc = NovigClient("public-data")            # no credentials: GET /v3/public/* only (the client enforces it)
    risk = RiskGate(cfg, ledger, nx.MODE)
    from ev.novig.gate import load_exec_gate
    ngate = load_exec_gate()                        # None unless NOVIG_GATE_ENABLED=1; restrictions only

    legs, legs_loaded = None, 0.0
    passes = pass_errors = filled_new = 0
    bank_errors = 0
    hist: dict = {}
    window_seen = snapshot_missing = False
    n_fill_before = _filled(ledger)

    def _bank():
        nonlocal bank_errors
        try:
            res = sb.bank(ledger, store, known=known)
            if res["chunks"]:
                log.info("banked %s", json.dumps({"new_rows": res["new_rows"], "chunks": [c["key"] for c in res["chunks"]]}))
        except Exception:                                                            # noqa: BLE001
            bank_errors += 1
            log.exception("BANKING FAILED -- rows stay in this process only")

    while True:
        now = clock()
        snap = snapshot_fn()
        if snap is None:
            log.error("no consensus snapshot (EVSweep/nfl_props/consensus.json): the sweep has never published or GCS is unreadable")
            snapshot_missing = True
            break
        in_window = games_in_window(snap, cfg, now, args.horizon_hours)
        if not in_window:
            log.info("no NFL game between +%.0f min and +%.0f h of the sweep snapshot: nothing to do", cfg.close_buffer_min, args.horizon_hours)
            break
        window_seen = True
        age = snapshot_age_min(snap, now)
        if age > cfg.cons_max_age_min - args.fresh_margin_min:
            if waits_left > 0:
                waits_left -= 1
                log.info("snapshot is %.1f min old (limit %.0f - margin %.0f); waiting for the sweep", age, cfg.cons_max_age_min, args.fresh_margin_min)
                sleep(wait_step)
                continue
            log.info("snapshot is %.1f min old and no fresher one arrived: stopping", age)
            break
        try:
            if legs is None or time.monotonic() - legs_loaded > 600:
                legs, legs_loaded = nx.load_novig_legs(nc, clock()), time.monotonic()
            cands = nx.candidates(snap["events"], legs, cfg, clock())
            ns = nx.run_pass(snap_ts=snap["ts"], cands=cands, cfg=cfg, nc=nc, ledger=ledger, risk=risk, clock=clock,
                             pace_s=args.pace_s, paper_venue=None, paper_risk=None, gate=ngate)
            passes += 1
            for k, v in ns["hist"].items():
                hist[k] = hist.get(k, 0) + v
            log.info("NOVIG pass %s legs=%s candidates=%s hist=%s executed=%s", passes, len(legs), ns["considered"],
                     ns["hist"], len(ns["executed"]))
            for e in ns["executed"]:
                log.info("  NOVIG %s", e)
        except Exception:                                                            # noqa: BLE001
            pass_errors += 1
            log.exception("novig pass failed")
        _bank()
        if risk.killed or time.monotonic() + args.loop_seconds >= t_end:
            break
        sleep(args.loop_seconds)

    _bank()
    filled_new = _filled(ledger) - n_fill_before
    status = gate_status(store, now=clock(), new_fills=filled_new, source=gate_source)
    print(status, flush=True)
    log.info("NOVIG shadow job done: passes=%s pass_errors=%s new_filled_orders=%s hist=%s bank_errors=%s",
             passes, pass_errors, filled_new, hist, bank_errors)
    if bank_errors or snapshot_missing:
        return 1
    if window_seen and passes == 0 and pass_errors:
        return 1
    return 0


def _filled(ledger: Ledger) -> int:
    with ledger._lock:
        return int(ledger._c.execute("SELECT COUNT(*) FROM orders WHERE mode=? AND fill_count>0", (nx.MODE,)).fetchone()[0])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Novig SHADOW loop as a Cloud Run job (paper fills vs the public book; never live)")
    ap.add_argument("--store", default="gcs", help="gcs | gcloud | dir:PATH  (the bank)")
    ap.add_argument("--bucket", default=None)
    ap.add_argument("--max-runtime-sec", type=float, default=1500.0, help="stop starting passes after this (keep < the schedule gap)")
    ap.add_argument("--loop-seconds", type=float, default=120.0)
    ap.add_argument("--wait-fresh-sec", type=float, default=240.0, help="wait this long for a fresh sweep snapshot at start")
    ap.add_argument("--fresh-margin-min", type=float, default=2.0, help="stop trading when the snapshot is within this of the age limit")
    ap.add_argument("--horizon-hours", type=float, default=30.0, help="only act on games kicking off within this window")
    ap.add_argument("--pace-s", type=float, default=0.7, help="pause between Novig book reads (same as the laptop loop)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    return run(a)


if __name__ == "__main__":
    raise SystemExit(main())
