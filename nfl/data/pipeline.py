"""The nightly NFL data job: mirror -> derived tables -> data contracts, in that order.

    python -m nfl.data.pipeline [--mode refresh|backfill] [--seasons 2018-2026] [--skip-derived] [--skip-contracts]

This is what the `nfl-refresh-data` Cloud Run Job runs (deploy/setup_nfl_refresh.sh). Order matters: the derived
tables read the pbp the ingest just wrote, and the contracts judge both. Each stage is independent in failure terms:
a failed ingest does not stop the contracts from running (they are exactly how a stale mirror is reported), and a
failed contract does NOT fail the job -- it is a statement about upstream data, surfaced by monitor_ops from
NFL/contracts_latest.json, not a crash of this process.

Exit status: 1 if the ingest failed or any derived build errored, else 0.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def run_pipeline(mode: str = "refresh", seasons: Optional[list] = None, derive: bool = True, contracts: bool = True,
                 force: bool = False, ingest_fn: Optional[Callable] = None, derive_fn: Optional[Callable] = None,
                 contracts_fn: Optional[Callable] = None) -> dict:
    from nfl.data import contracts as C
    from nfl.data import derived as D
    from nfl.data import ingest as I
    ingest_fn = ingest_fn or I.run
    derive_fn = derive_fn or D.run
    contracts_fn = contracts_fn or C.run_contracts

    out: dict = {"mode": mode}
    try:
        out["ingest"] = ingest_fn(mode, seasons=seasons, force=force)
    except Exception as exc:                # noqa: BLE001 -- the later stages must still run and report
        logger.exception("ingest crashed")
        out["ingest"] = {"status": "error", "failures": [{"item": "ingest", "error": f"{type(exc).__name__}: {exc}"}]}
    if derive:
        try:
            # backfill derives the same window; refresh only the current season (D.run's default)
            out["derived"] = derive_fn(seasons if mode == "backfill" else None, force)
        except Exception as exc:            # noqa: BLE001
            logger.exception("derive crashed")
            out["derived"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    if contracts:
        try:
            rep = contracts_fn(write=True)
            out["contracts"] = {"ok": rep["ok"], "status": rep["status"], "n_fail": rep["n_fail"], "n_warn": rep["n_warn"]}
            logger.info("%s", C.summarize(rep))
        except Exception as exc:            # noqa: BLE001
            logger.exception("contracts crashed")
            out["contracts"] = {"ok": False, "status": "fail", "error": f"{type(exc).__name__}: {exc}"}
    bad_ingest = out["ingest"].get("status") == "error"
    bad_derive = out.get("derived", {}).get("status") == "error"
    out["status"] = "error" if (bad_ingest or bad_derive) else "ok"
    return out


def main(argv=None) -> int:
    from nfl.data.ingest import _parse_seasons
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=("refresh", "backfill"), default="refresh")
    ap.add_argument("--seasons")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--skip-derived", action="store_true")
    ap.add_argument("--skip-contracts", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = run_pipeline(args.mode, _parse_seasons(args.seasons), not args.skip_derived, not args.skip_contracts,
                       args.force)
    print(json.dumps(out, indent=1, default=str))
    return 0 if out["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
