"""
ev.ownbettor.paper_job -- AUT-10: the scheduled PAPER operation of the AUT-09 bettor.

    job  ownbettor-paper   python -m ev.ownbettor.paper_job   (every 30 min, America/Chicago)

WHAT ONE FIRING DOES
  1. Restores the bettor's audit dir from the bucket (OwnBettor/audit/: the per-day
     JSONL files + state.json, so the kill switch, daily caps and loss-stop state
     persist across firings -- the audit dir is the bettor's only state).
  2. Settlement pass: every paper fill not yet settled is checked against the
     Kalshi market's PUBLIC result (unsigned read-only host, no credentials);
     a decided market books its realized P&L into the audit as a settlement.
     Paper fills are all-in costed (price*count + fee, the PaperVenue
     convention), so a win pays fill_count * $1.00 minus that cost and a loss
     books the whole cost.
  3. Signal pass: reads the AUT-01 shadow ledger (Autopilot/shadow_ledger/
     latest.jsonl.gz) READ-ONLY and runs every actionable (FILLED) signal the
     audit has not already received through the unchanged AUT-09 pipeline
     (Kelly sizing -> rule gate -> risk gate -> verified-table resolution ->
     executable re-check -> paper fill against Kalshi's live public book).
     Already-received signals are never re-processed: the audit is the dedupe.
  4. Banks the audit dir back to the bucket and writes a create-only run
     summary under OwnBettor/runs/.

PAPER ONLY -- THE FOUR LIVE LOCKS ARE NOT TOUCHED.  This module never sets
OWNBETTOR_LIVE_ARMED, never constructs the live adapter, and refuses to run at
all (exit 2) if the loaded config is live-armed.  Its own arm flag is
OWNBETTOR_PAPER_ARMED=1, set only in the Cloud Run job's environment (the
RES-11 shadow pattern): removing it, or pausing the scheduler, disarms the
paper operation.  Without it the module exits 3 having done nothing.

MAPPING (AUT-11): before the signal pass, main() loads the verified Kalshi
player-prop table from OwnBettor/mapping/kalshi_props.json and refreshes it
inside the firing only when it is stale (ev.ownbettor.propmap; a cached table
with a daily refresh check -- no new job or scheduler).  The table composes
behind the recorded table in the resolver chain; the run summary records its
status under "mapping".  A table failure degrades to the recorded table alone,
never to a failed firing.

ISOLATION: reads AUT-01's banked ledger object only -- no contact with AUT-01's
code, jobs, schedulers or evaluator, and none with PRD-04 or RES-11.  Writes
only under OwnBettor/.  Stood up Nov 16, 2026 at Luc's direction ("run this
automated tool, record methods and findings, and do daily retrospectives with
Argon"); the daily record lives in ~/workspace/beezy/ownbettor-daily/.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
from datetime import datetime, timezone

from ev.novig.shadow_bank import make_store
from ev.ownbettor import propmap
from ev.ownbettor.audit import AuditLog, PLACED_STATUSES
from ev.ownbettor.config import BettorConfig, load_config
from ev.ownbettor.propmap import PropTableResolver
from ev.ownbettor.resolver import ChainedResolver, RecordedTableResolver
from ev.ownbettor.runner import Bettor
from ev.ownbettor.signals import signals_from_rows
from ev.ownbettor.venues.kalshi import KalshiPaperAdapter, PublicBookSource

ARM_ENV = "OWNBETTOR_PAPER_ARMED"
AUDIT_PREFIX = "OwnBettor/audit"
RUNS_PREFIX = "OwnBettor/runs"
LEDGER_KEY = "Autopilot/shadow_ledger/latest.jsonl.gz"


# ---------------------------------------------------------------------- sources
class KalshiResultSource:
    """Market results from Kalshi's PUBLIC production data host: unsigned,
    read-only, no credentials (the same host the paper book source uses)."""

    def __init__(self):
        from mlb.execution.kalshi.client import KalshiClient
        self._client = KalshiClient("public-data", None, None)

    def result(self, ticker: str) -> str | None:
        """'yes' / 'no' once the market is decided; None while it is not (or on
        any read failure -- an unsettled fill simply waits for the next firing)."""
        try:
            market = self._client.get_market(ticker)
        except Exception:                                        # noqa: BLE001
            return None
        r = str((market or {}).get("result") or "").strip().lower()
        return r if r in ("yes", "no") else None


# ------------------------------------------------------------------ pure helpers
def processed_signal_ids(events: list) -> set:
    """Signals the audit has already received (the dedupe set for the signal pass)."""
    return {e.get("signal_id") for e in events if e.get("event") == "signal_received"}


def unsettled_fills(events: list) -> list:
    """Paper fills (simulated order_results with contracts filled) that no
    settlement row has booked yet, joined to their intended ticker/side/day."""
    intents = {e.get("signal_id"): e for e in events if e.get("event") == "order_intended"}
    settled = {e.get("signal_id") for e in events if e.get("event") == "settlement"}
    out = []
    for e in events:
        if e.get("event") != "order_result" or e.get("status") not in PLACED_STATUSES:
            continue
        if not e.get("simulated") or float(e.get("fill_count") or 0) <= 0:
            continue
        sid = e.get("signal_id")
        if sid in settled or sid not in intents:
            continue
        out.append({"signal_id": sid, "ticker": intents[sid].get("ticker"),
                    "side": intents[sid].get("side"),
                    "day": intents[sid].get("day") or e.get("day"),
                    "fill_count": float(e.get("fill_count") or 0),
                    "filled_cost": float(e.get("filled_cost") or 0.0)})
    return out


def settlement_pnl(fill: dict, result: str) -> float:
    """Realized P&L of one paper fill.  filled_cost is all-in (price*count + fee),
    so a win returns fill_count * $1.00 against it; a loss forfeits it."""
    if result == fill.get("side"):
        return round(fill["fill_count"] - fill["filled_cost"], 4)
    return round(-fill["filled_cost"], 4)


# ---------------------------------------------------------------------- banking
def _download_audit(store, audit_dir: str) -> list:
    os.makedirs(audit_dir, exist_ok=True)
    got = []
    for key in store.list(AUDIT_PREFIX + "/"):
        name = key[len(AUDIT_PREFIX) + 1:]
        if "/" in name or not name:
            continue
        with open(os.path.join(audit_dir, name), "wb") as f:
            f.write(store.read(key))
        got.append(name)
    return got


def _upload_audit(store, audit_dir: str) -> list:
    put = []
    for name in sorted(os.listdir(audit_dir)):
        if name == "state.json" or (name.startswith("audit-") and name.endswith(".jsonl")):
            with open(os.path.join(audit_dir, name), "rb") as f:
                store.put(f"{AUDIT_PREFIX}/{name}", f.read())
            put.append(name)
    return put


def _read_ledger_rows(store) -> list:
    text = gzip.decompress(store.read(LEDGER_KEY)).decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ------------------------------------------------------------------------- run
def run_job(store, result_source=None, book_source=None, cfg: BettorConfig | None = None,
            prop_table: dict | None = None, prop_status: dict | None = None) -> dict:
    cfg = cfg or load_config()
    downloaded = _download_audit(store, cfg.audit_path)
    audit = AuditLog(cfg.audit_path, mode="dry_run")
    resolvers = [RecordedTableResolver.from_file(cfg.mapping_path)]
    if prop_table is not None:
        resolvers.append(PropTableResolver(prop_table))
    resolver = resolvers[0] if len(resolvers) == 1 else ChainedResolver(resolvers)
    adapter = KalshiPaperAdapter(book_source or PublicBookSource(), cfg)
    bettor = Bettor(cfg, adapter, resolver, audit=audit)

    # 1) settlements first, so the loss stop and the bankroll record are current
    booked = []
    if result_source is not None:
        for fill in unsettled_fills(audit.all_events()):
            result = result_source.result(fill["ticker"])
            if result is None:
                continue
            pnl = settlement_pnl(fill, result)
            bettor.record_settlement(fill["signal_id"], pnl, fill["day"],
                                     note=f"paper settle: kalshi result={result} "
                                          f"ticker={fill['ticker']}")
            booked.append({"signal_id": fill["signal_id"], "pnl_usd": pnl, "result": result})

    # 2) new signals from the AUT-01 shadow ledger (read-only)
    rows = _read_ledger_rows(store)
    actionable = signals_from_rows(rows)
    seen = processed_signal_ids(audit.all_events())
    outcomes: dict = {}
    n_new = 0
    for signal in actionable:
        if signal.signal_id in seen:
            continue
        n_new += 1
        out = bettor.process_signal(signal)
        outcomes[out["outcome"]] = outcomes.get(out["outcome"], 0) + 1

    # 3) bank the audit back + a create-only run summary
    uploaded = _upload_audit(store, cfg.audit_path)
    events = audit.all_events()
    summary = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "paper",
        "audit_files_restored": downloaded,
        "ledger_rows": len(rows),
        "actionable_in_ledger": len(actionable),
        "signals_new": n_new,
        "outcomes": outcomes,
        "settlements_booked": booked,
        "mapping": prop_status,
        "realized_pnl_total": round(sum(float(e.get("pnl_usd") or 0.0) for e in events
                                        if e.get("event") == "settlement"), 4),
        "killed": audit.kill is not None,
        "audit_files_banked": uploaded,
    }
    store.write_new(f"{RUNS_PREFIX}/run-{summary['ts'].replace(':', '')}.json",
                    json.dumps(summary, indent=1, sort_keys=True).encode("utf-8"))
    return summary


def main(argv=None) -> int:
    if os.environ.get(ARM_ENV) != "1":
        print(f"refused: {ARM_ENV} is not set (the paper operation is disarmed)", file=sys.stderr)
        return 3
    cfg = load_config()
    if cfg.live_armed:
        print("refused: OWNBETTOR_LIVE_ARMED is set; the paper operation never runs "
              "under a live-armed config", file=sys.stderr)
        return 2
    store = make_store("gcs")
    # AUT-11: the verified Kalshi prop table, banked in the bucket and refreshed
    # inside this firing only when stale (never raises; see propmap.load_or_refresh).
    prop_table, prop_status = propmap.load_or_refresh(store)
    summary = run_job(store, KalshiResultSource(), PublicBookSource(), cfg,
                      prop_table=prop_table, prop_status=prop_status)
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
