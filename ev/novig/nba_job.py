"""
ev.novig.nba_job -- the Novig NBA PAPER executor as a Cloud Run Job (python -m ev.novig.nba_job).

The NBA twin of the NFL paper executor (whose driver lives inline in that job's spec), built step for
step on ev.novig.nhl_job: same ledger bank, same risk gates, same paper venue and trading key, same
fail-closed shape. The differences are only where the sport forces them:

  * the fair comes from ev.novig.nba_snapshot (the NBA pipeline's served boards: projections_latest's
    p_over_blend plus odds_board_latest's per-book strips for the leave-Novig-out admission accounting)
    -- NBA has no published sweep consensus;
  * legs come from the NBA catalog (load_novig_legs(..., "NBA"): rebounds + threes only, the two
    officials-allowlisted trained-signal stats; points / assists / combos / *_PROTECTED stay unmapped);
  * snapshot freshness is 45 min (the boards republish every 30 min, so the NFL driver's 25 min age
    gate would starve this job by construction -- the same reasoning as the NHL driver);
  * NO forward-CLV gate status line: ev.novig.clv_gate is NFL machinery (its closes are NFL closing
    lines). NBA fills bank into the shared ledger under the same mode; the NFL job's status step reads
    them as bets with no usable NFL close and drops them from its slices (counted, never priced).

PAPER ONLY. Orders go only to the Novig paper sandbox via the paper venue (production is refused by the
client itself). The production public catalog is read for books. Exit codes mirror the NFL driver:
0 ok / pass ran (zero orders is a pass outcome when the gate rejects everything), 1 broken (no snapshot,
pass or banking failed), 3 no fresh in-window snapshot before the deadline.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from ev.novig import exec as nx
from ev.novig import nba_snapshot as nsnap
from ev.novig import shadow_bank as sb
from ev.novig.client import NovigClient
from ev.novig.paper_orders import PaperRiskGate
from ev.novig.shadow_job import _filled, games_in_window
from ev.props_config import props_config
from ev.props_exec import load_novig_paper_venue
from mlb.execution.kalshi.ledger import Ledger
from mlb.execution.kalshi.risk import RiskGate

log = logging.getLogger("ev.novig.nba_job")


def jlog(step, **kw):
    print(json.dumps(dict(executor_step=step, sport="nba", **kw)), flush=True)


def _paper_counts(ledger):
    with ledger._lock:
        n = ledger._c.execute("SELECT COUNT(*) FROM orders WHERE mode=?", (nx.PAPER_MODE,)).fetchone()[0]
        f = ledger._c.execute("SELECT COUNT(*) FROM orders WHERE mode=? AND fill_count>0",
                              (nx.PAPER_MODE,)).fetchone()[0]
    return int(n), int(f)


def _write_paper_env() -> None:
    """Same bootstrap as the NFL paper job: materialize ~/.novig/paper.env from the job's secret env.
    Skipped when the env vars are absent (local/test runs with an injected venue); a genuinely missing
    key still fails loudly at load_novig_paper_venue's preflight."""
    if not (os.environ.get("NOVIG_PAPER_TRADING_KEY_ID") and os.environ.get("NOVIG_PAPER_TRADING_KEY_PATH")):
        return
    home_novig = os.path.expanduser("~/.novig")
    os.makedirs(home_novig, mode=0o700, exist_ok=True)
    env_path = os.path.join(home_novig, "paper.env")
    with open(env_path, "w") as fh:
        fh.write("NOVIG_PAPER_TRADING_KEY_ID=" + os.environ["NOVIG_PAPER_TRADING_KEY_ID"] + "\n")
        fh.write("NOVIG_PAPER_TRADING_KEY_PATH=" + os.environ["NOVIG_PAPER_TRADING_KEY_PATH"] + "\n")
    os.chmod(env_path, 0o600)


def run(args, *, store=None, nc=None, paper_venue=None, clock=None, sleep=time.sleep) -> int:
    clock = clock or (lambda: datetime.now(timezone.utc))
    cfg = props_config()
    store = store or sb.make_store(args.store, args.bucket)
    _write_paper_env()

    now0 = clock()
    day = sb.et_day(now0)
    prev = (now0.astimezone(sb._ET) - timedelta(days=1)).date().isoformat()
    nxt = (now0.astimezone(sb._ET) + timedelta(days=1)).date().isoformat()
    ledger = Ledger(":memory:")
    rs = sb.restore_into(ledger, store, days=[prev, day, nxt], tables=("orders", "fills", "kv"))
    known = rs["known"]
    jlog("restore", restored=str(rs["restored"]), chunks=rs["chunks"], days=[prev, day, nxt])
    pn_before, pf_before = _paper_counts(ledger)
    jlog("paper_ledger_before", orders=pn_before, filled=pf_before)

    paper_venue = paper_venue or load_novig_paper_venue()
    paper_risk = PaperRiskGate(cfg, ledger, nx.PAPER_MODE)
    jlog("venue", ok=True, paper_risk_killed=paper_risk.killed, paper_kill_reason=paper_risk.kill_reason)

    nc = nc or NovigClient("public-data")            # no credentials: GET /v3/public/* only
    risk = RiskGate(cfg, ledger, nx.MODE)
    from ev.novig.gate import load_exec_gate
    ngate = load_exec_gate()                          # None unless NOVIG_GATE_ENABLED=1; restrictions only
    jlog("gate", exec_gate_enabled=ngate is not None)

    deadline = time.monotonic() + args.wait_fresh_sec
    snap = None
    while True:
        now = clock()
        proj, board = nsnap.read_latest()
        if proj is None:
            jlog("snapshot", ok=False, reason="missing")
            return 1
        built = nsnap.build_snapshot(proj, board)
        in_window = games_in_window(built, cfg, now, args.horizon_hours)
        age = nsnap.snapshot_age_min(proj, now)
        jlog("snapshot", age_min=round(age, 1), games_in_window=in_window, events=len(built["events"]),
             points=sum(len(e["points"]) for e in built["events"]))
        if in_window and age <= args.max_snapshot_age_min:
            snap = built
            break
        if time.monotonic() >= deadline:
            jlog("snapshot", ok=False, reason="no fresh in-window snapshot before deadline")
            return 3
        sleep(30.0)

    exit_code = 0
    filled_new = 0
    try:
        legs = nx.load_novig_legs(nc, clock(), "NBA")
        paper_legs = nx.load_novig_legs(paper_venue.client, clock(), "NBA")
        paper_venue.set_legs(paper_legs)
        by_stat = {}
        for leg in legs.values():
            by_stat[leg.stat] = by_stat.get(leg.stat, 0) + 1
        jlog("legs", production=len(legs), paper=len(paper_legs), by_stat=by_stat)
        cands = nsnap.candidates_nba(snap["events"], legs, cfg, clock())
        cand_by_stat = {}
        for leg, _p, _m in cands:
            cand_by_stat[leg.stat] = cand_by_stat.get(leg.stat, 0) + 1
        jlog("candidates", n=len(cands), by_stat=cand_by_stat)
        n_fill_before = _filled(ledger)
        res = nx.run_pass(snap_ts=snap["ts"], cands=cands, cfg=cfg, nc=nc, ledger=ledger, risk=risk,
                          clock=clock, pace_s=args.pace_s, paper_venue=paper_venue, paper_risk=paper_risk,
                          gate=ngate)
        po = res["paper_orders"]
        jlog("pass", considered=res["considered"], hist=res["hist"], executed=len(res["executed"]),
             paper_orders_sent=len(po), paper_statuses=[p.get("status") for p in po],
             paper_filled_units=sum(float(p.get("units") or 0) for p in po),
             paper_filled_cost=sum(float(p.get("cost") or 0) for p in po), killed=res["killed"])
        for e in res["executed"]:
            jlog("executed", **e)
        filled_new = _filled(ledger) - n_fill_before
    except Exception as e:                                                            # noqa: BLE001
        log.exception("nba executor pass failed")
        jlog("pass", ok=False, error_class=type(e).__name__, error=str(e)[:200])
        exit_code = 1

    try:
        res = sb.bank(ledger, store, known=known)
        jlog("bank", new_rows=res["new_rows"], chunks=[c["key"] for c in res["chunks"]])
    except Exception as e:                                                            # noqa: BLE001
        log.exception("banking failed")
        jlog("bank", ok=False, error_class=type(e).__name__)
        exit_code = 1

    pn_after, pf_after = _paper_counts(ledger)
    jlog("paper_ledger_after", orders=pn_after, filled=pf_after,
         new_paper_orders=pn_after - pn_before, new_paper_filled=pf_after - pf_before,
         new_shadow_filled=filled_new)
    jlog("done", exit_code=exit_code)
    return exit_code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Novig NBA PAPER executor (paper sandbox only; never live)")
    ap.add_argument("--store", default="gcs", help="gcs | gcloud | dir:PATH  (the bank)")
    ap.add_argument("--bucket", default=None)
    ap.add_argument("--wait-fresh-sec", type=float, default=1500.0,
                    help="poll this long for a fresh in-window NBA snapshot before exiting 3")
    ap.add_argument("--max-snapshot-age-min", type=float, default=45.0,
                    help="refuse a served NBA board older than this (the boards republish every 30 min)")
    ap.add_argument("--horizon-hours", type=float, default=30.0, help="only act on games starting within this window")
    ap.add_argument("--pace-s", type=float, default=0.7, help="pause between Novig book reads (same as the NFL loop)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    return run(a)


if __name__ == "__main__":
    raise SystemExit(main())
