"""
mlb.runners.track_bettingpros -- Cloud Run Job: bank FREE BettingPros snapshots.

BettingPros is a free public API, so unlike ParlayAPI (credit-metered) we can
snapshot it many times a day for nothing. This fetches TODAY's (and optionally the
next day's) lines for the configured markets and writes them straight into
odds_history at a REAL snapshot_ts (source="bettingpros"). Run a few times daily via
Cloud Scheduler -> accumulating intraday snapshots give genuine open->close line
movement / CLV, which is exactly what the model-vs-line analysis was missing.

Uses the FIXED parser (one row per line, no per-book line collapse) + the shared
snapshot emitter, so what it banks is clean.

Config via env (all optional):
    BP_MARKETS   default "player"  (bp group/names/ids; player props by default)
    BP_DAYS      how many days from today to pull, default 2 (today + tomorrow)
    BP_DELAY     seconds between markets, default 0.3

Local:
    PYTHONPATH=. BP_MARKETS=total_bases,hits python3 -m mlb.runners.track_bettingpros
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from mlb_core.odds import bettingpros as bp
from mlb.analysis import bettingpros_to_parquet as b2p
from mlb.analysis import odds_history as oh

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("track_bettingpros")


def run(run_date: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    snapshot_ts = now.strftime("%Y-%m-%d %H:%M:%S")
    ingested_at = now.isoformat()
    markets_arg = os.environ.get("BP_MARKETS", "player")
    n_days = int(os.environ.get("BP_DAYS", "2"))
    delay = float(os.environ.get("BP_DELAY", "0.3"))

    start = date.fromisoformat(run_date) if run_date else now.date()
    dates = [(start + timedelta(days=i)).isoformat() for i in range(n_days)]

    ids = bp.resolve_markets(markets_arg)
    names = {m: bp.MARKETS[m][0] for m in ids if bp.MARKETS[m][0] in b2p.BP_TO_HISTORY}
    log.info("track bettingpros @ %s | markets=%s days=%s",
             snapshot_ts, list(names.values()), dates)

    sess = bp.make_session()
    total, errors = 0, 0
    for ds in dates:
        try:
            ev_map = bp.fetch_events(sess, ds)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: fetch_events failed: %s", ds, exc); errors += 1; continue
        if not ev_map:
            log.info("%s: no events", ds); continue
        eids = list(ev_map.keys())
        for mid, name in names.items():
            _, kind = bp.MARKETS[mid]
            try:
                offers = bp.fetch_offers(sess, mid, eids)
                rows = bp.build_rows(kind, offers, ds, ev_map)
                if not rows:
                    continue
                long_df = b2p.snapshot_market_to_history(
                    name, pd.DataFrame(rows), snapshot_ts, ingested_at)
                market = b2p.BP_TO_HISTORY[name][0]
                for d in long_df["game_date"].dropna().unique():
                    total += oh.write_partition(long_df[long_df["game_date"] == d], market, d)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                log.warning("%s %s: %s", ds, name, exc)
            time.sleep(delay)
    log.info("DONE. banked %d odds_history rows @ %s | errors=%d", total, snapshot_ts, errors)
    return {"status": "ok" if not errors else "partial", "rows": total, "snapshot_ts": snapshot_ts}


def main() -> int:
    res = run()
    return 0 if res["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
