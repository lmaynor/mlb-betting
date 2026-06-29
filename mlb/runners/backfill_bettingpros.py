"""
mlb.runners.backfill_bettingpros -- Cloud Run Job: backfill BettingPros odds to GCS.

Runs the multi-market BettingPros API backfill server-side (no Cloud Shell
session to babysit) and writes one partitioned CSV per (market, date) to GCS:

    Odds/bettingpros/{market_name}/{YYYY-MM-DD}.csv

Partitioning by date makes resume cheap (list existing keys per market, skip
done dates) and avoids rewriting multi-hundred-MB files. Re-executing the job
resumes idempotently -- safe after a timeout or retry.

Config via env vars (all optional):
    BP_START      default 2024-04-01
    BP_END        default today (UTC)
    BP_MARKETS    default "all"   (groups: player,lines,innings,all or ids)
    BP_PREFIX     default "Odds/bettingpros"
    BP_DELAY      default 0.4     (seconds between dates)

Local run (writes to BASE_DATA when MLB_GCS_BUCKET unset):
    PYTHONPATH=. python3 -m mlb.runners.backfill_bettingpros
"""

from __future__ import annotations

import io
import logging
import os
import random
import time
from datetime import date

import pandas as pd

from mlb_core import storage
from mlb_core.odds import bettingpros as bp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_bettingpros")


def _key(prefix: str, market_id: int, ds: str) -> str:
    name, _ = bp.MARKETS[market_id]
    return f"{prefix}/{name}/{ds}.csv"


def _done_dates(prefix: str, market_id: int) -> set:
    """Dates already written for this market, from existing GCS/local keys."""
    name, _ = bp.MARKETS[market_id]
    done = set()
    for key in storage.list_keys(f"{prefix}/{name}/"):
        stem = key.rsplit("/", 1)[-1]
        if stem.endswith(".csv"):
            done.add(stem[:-4])
    return done


def _write(prefix: str, market_id: int, ds: str, rows: list) -> None:
    _, kind = bp.MARKETS[market_id]
    df = pd.DataFrame(rows, columns=bp.headers(kind))
    storage.write_csv(df, _key(prefix, market_id, ds))


def main() -> int:
    start = os.environ.get("BP_START", "2024-04-01")
    end = os.environ.get("BP_END", date.today().isoformat())
    markets_arg = os.environ.get("BP_MARKETS", "all")
    prefix = os.environ.get("BP_PREFIX", "Odds/bettingpros").rstrip("/")
    delay = float(os.environ.get("BP_DELAY", "0.4"))

    markets = bp.resolve_markets(markets_arg)
    sess = bp.make_session()

    done = {m: _done_dates(prefix, m) for m in markets}
    dates = [d for d in bp.date_range(start, end) if bp.in_season(d)]
    log.info("backfill %s -> %s | %d in-season dates x %d markets -> gs prefix %s",
             start, end, len(dates), len(markets), prefix)

    total_rows = 0
    errors = 0
    for ds in dates:
        need = [m for m in markets if ds not in done[m]]
        if not need:
            continue
        try:
            ev_map = bp.fetch_events(sess, ds)
            if not ev_map:
                log.info("%s: no events", ds)
                continue
            eids = list(ev_map.keys())
            day_rows = 0
            for mid in need:
                name, kind = bp.MARKETS[mid]
                offers = bp.fetch_offers(sess, mid, eids)
                rows = bp.build_rows(kind, offers, ds, ev_map)
                if rows:
                    _write(prefix, mid, ds, rows)
                    day_rows += len(rows)
                done[mid].add(ds)
                time.sleep(0.2)
            total_rows += day_rows
            log.info("%s: %d rows across %d markets", ds, day_rows, len(need))
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log.exception("%s: ERROR %s", ds, exc)
        time.sleep(delay * random.uniform(0.6, 1.4))

    log.info("DONE. total_rows=%d errors=%d prefix=%s", total_rows, errors, prefix)
    # Non-zero exit on errors so a Cloud Run Job retry kicks in (resume is idempotent).
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
