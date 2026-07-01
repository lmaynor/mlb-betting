"""
mlb.runners.walkforward_batch -- Cloud Run Job: rolling walk-forward for all systems.

The rolling walk-forward (mlb.analysis.walkforward.rolling) retrains a model at each
monthly cutoff and scores the next month cold -- ~15 retrains x 4 systems on up to
268k rows. That's too long for a Cloud Shell session (VM recycles ~20min and kills
nohup). This runs it server-side and PERSISTS results to GCS so nothing is lost:

    Analysis/walkforward/<run_date>/<SYSTEM>_summary.json     (pooled roi/z/clv/lines)
    Analysis/walkforward/<run_date>/<SYSTEM>_pooled_bets.csv   (every bet, for inspection)

Config via env (all optional):
    WF_SYSTEMS   "all" (default) or comma list (K,OUTS,BATTER_HITS,BATTER_TB)
    WF_START     default 2024-05-01
    WF_END       default 2026-06-01
    WF_STEP      months per window, default 1
    WF_EDGE      edge-bucket threshold to pool, default 0.10
    WF_SELECT    best | consensus (default consensus -- the clean market test)
    WF_OUT       output prefix, default Analysis/walkforward/<run_date>

Local:
    PYTHONPATH=. WF_SYSTEMS=BATTER_TB python3 -m mlb.runners.walkforward_batch
"""

from __future__ import annotations

import logging
import os
from datetime import date

from mlb.analysis import walkforward as wf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("walkforward_batch")


def run(run_date: str | None = None) -> dict:
    run_date = run_date or date.today().isoformat()
    systems_arg = os.environ.get("WF_SYSTEMS", "all")
    systems = list(wf.WF_SYS) if systems_arg == "all" else systems_arg.split(",")
    start = os.environ.get("WF_START", "2024-05-01")
    end = os.environ.get("WF_END", "2026-06-01")
    step = int(os.environ.get("WF_STEP", "1"))
    edge = float(os.environ.get("WF_EDGE", "0.10"))
    select = os.environ.get("WF_SELECT", "consensus")
    out = os.environ.get("WF_OUT", f"Analysis/walkforward/{run_date}")

    log.info("walkforward batch | systems=%s [%s..%s] step=%d edge=%.2f select=%s -> %s",
             systems, start, end, step, edge, select, out)
    results, errors = {}, 0
    for s in systems:
        try:
            r = wf.rolling(s, start, end, step_months=step, edge=edge,
                           select=select, out_prefix=out)
            results[s] = r.get("summary", {})
            log.info("%s DONE: %s", s, results[s])
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log.exception("%s ERROR %s", s, exc)
    log.info("BATCH DONE. systems=%d errors=%d out=%s", len(systems), errors, out)
    return {"status": "ok" if not errors else "partial", "out": out, "results": results}


def main() -> int:
    res = run()
    return 0 if res["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
