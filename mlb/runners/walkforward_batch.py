"""
mlb.runners.walkforward_batch -- Cloud Run Job: COMPLETE walk-forward analysis.

Runs the rolling walk-forward (train pre-cutoff, score next month cold) for every
count market, in BOTH configurations, and persists everything to GCS so a full
overnight analysis survives Cloud Shell VM recycles:

  ungated  (min_books=1, max_spread=1.0)  -- raw, includes swapped/stale-quote
                                              contamination (the +30% artifact)
  gated    (min_books=4, max_spread=0.10) -- only markets where >=4 books agree
                                              within 10pts -- the CLEAN edge test

Outputs (Analysis/walkforward/<run_date>/):
  <config>/<SYSTEM>_summary.json      per-system pooled roi/z/clv/lines
  <config>/<SYSTEM>_pooled_bets.csv    every bet, for inspection
  REPORT.json  +  REPORT.csv           consolidated ungated-vs-gated comparison

The morning read is REPORT.csv: if gated roi_cons collapses toward 0 vs ungated,
the prop 'edge' was the OVER/UNDER data inconsistency (artifact). If gated stays
positive with z>=3 and CLV>=0, it's a real edge worth productionizing.

Config via env (all optional):
    WF_SYSTEMS   "all" (default) or comma list (K,OUTS,BATTER_HITS,BATTER_TB)
    WF_START     default 2024-05-01
    WF_END       default 2026-06-01
    WF_STEP      months per window, default 1
    WF_EDGE      edge-bucket threshold to pool, default 0.10
    WF_SELECT    best | consensus (default consensus)
    WF_CONFIGS   "both" (default) | "gated" | "ungated"
    WF_OUT       output prefix, default Analysis/walkforward/<run_date>

Local:
    PYTHONPATH=. WF_SYSTEMS=BATTER_TB python3 -m mlb.runners.walkforward_batch
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date

from mlb.analysis import walkforward as wf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("walkforward_batch")

# (label, kwargs) -- ungated shows the artifact, gated shows the clean edge.
ALL_CONFIGS = {
    "ungated": {"min_books": 1, "max_spread": 1.0},
    "gated":   {"min_books": 4, "max_spread": 0.10},
}


def run(run_date: str | None = None) -> dict:
    run_date = run_date or date.today().isoformat()
    systems_arg = os.environ.get("WF_SYSTEMS", "all")
    systems = list(wf.WF_SYS) if systems_arg == "all" else systems_arg.split(",")
    start = os.environ.get("WF_START", "2024-05-01")
    end = os.environ.get("WF_END", "2026-06-01")
    step = int(os.environ.get("WF_STEP", "1"))
    edge = float(os.environ.get("WF_EDGE", "0.10"))
    select = os.environ.get("WF_SELECT", "consensus")
    cfg_arg = os.environ.get("WF_CONFIGS", "both")
    configs = list(ALL_CONFIGS) if cfg_arg == "both" else [cfg_arg]
    out = os.environ.get("WF_OUT", f"Analysis/walkforward/{run_date}")

    log.info("COMPLETE walkforward | systems=%s configs=%s [%s..%s] step=%d edge=%.2f "
             "select=%s -> %s", systems, configs, start, end, step, edge, select, out)

    report, errors = [], 0
    for system in systems:
        for cfg in configs:
            kw = ALL_CONFIGS[cfg]
            tag = f"{system}/{cfg}"
            try:
                r = wf.rolling(system, start, end, step_months=step, edge=edge, select=select,
                               out_prefix=f"{out}/{cfg}", **kw)
                s = r.get("summary")
                if s:
                    s = {"config": cfg, **s}
                    report.append(s)
                    log.info("%s DONE: n=%s roi_cons=%.4f z=%.1f clv=%.2f",
                             tag, s["n_bets"], s["roi_cons"], s["z"], s["clv"])
                else:
                    log.warning("%s: %s", tag, r.get("error"))
            except Exception as exc:  # noqa: BLE001
                errors += 1
                log.exception("%s ERROR %s", tag, exc)

    # consolidated report -- the morning read
    try:
        from mlb_core import storage
        import pandas as pd
        if report:
            cols = ["system", "config", "n_bets", "roi_best", "z_best", "roi_cons", "z",
                    "win_rate", "clv", "avg_n_books"]
            rep_df = pd.DataFrame(report)[[c for c in cols if c in report[0]]] \
                .sort_values(["system", "config"])
            storage.write_csv(rep_df, f"{out}/REPORT.csv")
            log.info("\n=== CONSOLIDATED REPORT ===\n%s", rep_df.to_string(index=False))
        storage.write_bytes(json.dumps(report, indent=2).encode(), f"{out}/REPORT.json")
        log.info("wrote consolidated -> %s/REPORT.{csv,json}", out)
    except Exception as exc:  # noqa: BLE001
        log.exception("consolidated report write failed: %s", exc)

    log.info("BATCH DONE. systems=%d configs=%s errors=%d out=%s",
             len(systems), configs, errors, out)
    return {"status": "ok" if not errors else "partial", "out": out, "n_results": len(report)}


def main() -> int:
    res = run()
    return 0 if res["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
