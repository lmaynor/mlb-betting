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

# Added 2026-08-17 (finding B3.8): this file's own docstring/CONTEXT.md
# documented "5x/day free tier," but fast_alert_loop.py independently calls
# this exact run() on its own */15 cadence (up to 29 extra invocations/day)
# with no shared counter or cross-awareness between the two callers -- true
# call volume is ~34x/day against a free public API with no formal quota
# (so unlike ParlayAPI's credit ceiling, nothing here HARD-caps volume),
# but a shared ledger at least makes the true combined volume visible
# (mirrors snapshot_odds.py's own _credits/{month}.json pattern, daily
# instead of monthly since there's no monthly-spend concept here) and lets
# every caller back off together if BettingPros starts erroring a lot,
# rather than each caller only ever seeing its own isolated error history.
_CALL_LEDGER_PREFIX = "BettingPros/_calls"
_BACKOFF_MIN_CALLS = 3      # need at least this many recent calls to judge
_BACKOFF_ERROR_RATE = 0.6   # skip this run if >=60% of recent calls errored


def _call_ledger_key(day: str) -> str:
    return f"{_CALL_LEDGER_PREFIX}/{day}.json"


def _read_call_ledger(day: str) -> dict:
    from mlb_core.storage import exists, read_bytes
    import json as _json
    key = _call_ledger_key(day)
    if not exists(key):
        return {"date": day, "calls": 0, "recent_outcomes": []}
    try:
        d = _json.loads(read_bytes(key))
        if d.get("date") != day:
            return {"date": day, "calls": 0, "recent_outcomes": []}
        return d
    except Exception:  # noqa: BLE001
        return {"date": day, "calls": 0, "recent_outcomes": []}


def _write_call_ledger(day: str, ledger: dict) -> None:
    from mlb_core.storage import write_bytes
    import json as _json
    try:
        write_bytes(_json.dumps(ledger).encode(), _call_ledger_key(day))
    except Exception as e:  # noqa: BLE001
        log.warning("call ledger write failed (non-fatal): %s", e)


def _should_back_off(ledger: dict) -> bool:
    """True if enough recent calls have errored that this run should skip
    hitting BettingPros again -- give a struggling free API a break instead
    of every caller (the standalone schedule AND fast_alert_loop's
    independent */15 calls) hammering it at once."""
    recent = ledger.get("recent_outcomes", [])[-10:]
    if len(recent) < _BACKOFF_MIN_CALLS:
        return False
    error_rate = sum(1 for o in recent if o == "error") / len(recent)
    return error_rate >= _BACKOFF_ERROR_RATE


def run(run_date: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    snapshot_ts = now.strftime("%Y-%m-%d %H:%M:%S")
    ingested_at = now.isoformat()
    markets_arg = os.environ.get("BP_MARKETS", "player")
    n_days = int(os.environ.get("BP_DAYS", "2"))
    delay = float(os.environ.get("BP_DELAY", "0.3"))

    # Shared call ledger (finding B3.8) -- visible combined volume across
    # this job's own schedule AND fast_alert_loop.py's independent calls,
    # plus a circuit breaker if BettingPros has been erroring a lot lately.
    ledger_day = now.date().isoformat()
    ledger = _read_call_ledger(ledger_day)
    if os.environ.get("BP_SKIP_BACKOFF") != "1" and _should_back_off(ledger):
        log.warning("skipping this run: BettingPros error rate over the "
                   "last %d calls looks like a rate-limit/outage (calls "
                   "today so far: %d)", len(ledger.get("recent_outcomes", [])),
                   ledger.get("calls", 0))
        return {"status": "skipped_backoff", "rows": 0, "snapshot_ts": snapshot_ts}

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
                    # append=True -> accumulate intraday snapshots (dedup on snapshot_ts)
                    # instead of overwriting the day's partition each run.
                    total += oh.write_partition(long_df[long_df["game_date"] == d], market, d,
                                                append=True)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                log.warning("%s %s: %s", ds, name, exc)
            time.sleep(delay)
    log.info("DONE. banked %d odds_history rows @ %s | errors=%d", total, snapshot_ts, errors)

    ledger["calls"] = int(ledger.get("calls", 0)) + 1
    ledger["recent_outcomes"] = (ledger.get("recent_outcomes", []) + [
        "error" if errors else "ok"
    ])[-10:]
    _write_call_ledger(ledger_day, ledger)

    return {"status": "ok" if not errors else "partial", "rows": total,
            "snapshot_ts": snapshot_ts, "calls_today": ledger["calls"]}


def main() -> int:
    res = run()
    return 0 if res["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
