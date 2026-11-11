"""kalshi_resting_daemon.py -- Kalshi resting combo maker daemon (DEMO ONLY).

Posts resting post_only NO limit orders on already-created MVE combo tickers via
ev.kalshi_resting.RestingEngine, polls reconciliation every 30s (the kill switch),
and revalidates every resting order on each fair-book refresh.

DEMO-ONLY BY CONSTRUCTION: resolve_resting_run() refuses to start unless the
client env is the demo environment AND the base URL is the demo host
(https://external-api.demo.kalshi.co). Production, production-readonly,
public-data, or any non-demo base URL fail closed at startup, before credentials
are read or anything connects.

Env / flags (mirrors scripts/kalshi_rfq_daemon.py conventions):
  KALSHI_RESTING_ENV=demo            exchange env (anything else refuses)
  KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PATH / KALSHI_PRIVATE_KEY_PEM
                                     demo credentials (env file via --env-file or
                                     KEXEC_ENV_FILE), never logged
  --feed                             fair-value rows for the InMemoryFairBook
  --poll-sec                         discovery poll cadence (default 5s)
  --reconcile-sec                    reconciliation cadence (default 30s, Argon)
  --max-runtime-sec                  exit cleanly after N seconds (0 = forever)

Discovery here is a REST poll of open MVE markets; each market's legs are priced
off the fair book, and tickers younger than the 10-minute toxicity window are
skipped by the engine. A production deployment would swap the poll for the
rfq_created stream; the engine consumes the same ComboCandidate either way.
"""

import argparse
import logging
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ev.kalshi_resting import (  # noqa: E402
    DEMO_BASE_URL, DemoOnlyError, KalshiRequestVenue, RestingEngine, RestingLimits,
    assert_demo_only,
)
from ev.kalshi_rfq_pricer import InMemoryFairBook, load_feed_rows  # noqa: E402
from mlb.execution.kalshi.client import KalshiClient, read_env_keys  # noqa: E402

log = logging.getLogger("kalshi_resting_daemon")


def resolve_resting_run(env_name: str | None, base_url: str | None) -> tuple[str, str]:
    """-> (env, base_url) or raise SystemExit. The demo-only gate: called before
    credentials are read. Defaults are the demo env and the demo base URL."""
    env_name = (env_name or "demo").strip().lower()
    base_url = base_url or DEMO_BASE_URL
    try:
        assert_demo_only(env_name, base_url)
    except DemoOnlyError as e:
        raise SystemExit(f"refusing: {e}") from e
    return env_name, base_url


def load_credentials(env_file: str | None) -> tuple[str, bytes]:
    keys = ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PATH")
    vals = {k: os.environ.get(k) for k in keys if os.environ.get(k)}
    path = env_file or os.environ.get("KEXEC_ENV_FILE")
    if path:
        for k, v in read_env_keys(path, keys).items():
            vals.setdefault(k, v)
    pem_env = os.environ.get("KALSHI_PRIVATE_KEY_PEM")
    if vals.get("KALSHI_API_KEY_ID") and pem_env:
        return vals["KALSHI_API_KEY_ID"], pem_env.encode()
    if not (vals.get("KALSHI_API_KEY_ID") and vals.get("KALSHI_PRIVATE_KEY_PATH")):
        raise SystemExit("demo credentials not found (KALSHI_API_KEY_ID / "
                         "KALSHI_PRIVATE_KEY_PATH or KALSHI_PRIVATE_KEY_PEM)")
    with open(os.path.expanduser(vals["KALSHI_PRIVATE_KEY_PATH"]), "rb") as fh:
        return vals["KALSHI_API_KEY_ID"], fh.read()


def build_engine(client: KalshiClient, limits: RestingLimits | None = None) -> RestingEngine:
    """Engine over the request-level venue adapter (PR-1 seam; see
    ev.kalshi_resting.KalshiRequestVenue). Refuses a non-demo client."""
    assert_demo_only(client.env, client.base_url)
    return RestingEngine(venue=KalshiRequestVenue(client), limits=limits or RestingLimits())


def candidates_from_markets(markets: list, book: InMemoryFairBook, now: float) -> list:
    """Open MVE market objects -> ComboCandidates priced off the fair book.
    Markets whose legs do not all resolve are carried with unpriced legs and are
    rejected by tier admission (never silently dropped from the audit trail)."""
    from ev.kalshi_resting import ComboCandidate, RestingLeg

    out = []
    for m in markets:
        legs = []
        unpriced = 0
        for sel in m.get("mve_selected_legs") or []:
            ticker = str(sel.get("market_ticker") or sel.get("ticker") or "")
            fv = book.get(ticker, now) if ticker else None
            if fv is None:
                unpriced += 1
                legs.append(RestingLeg(ticker=ticker, side=str(sel.get("side") or "yes").lower(),
                                       game_key="", sport="", stat=""))
                continue
            legs.append(RestingLeg(
                ticker=ticker, side=str(sel.get("side") or "yes").lower(),
                game_key=fv.game_key, sport=fv.sport, stat=fv.stat, n_books=fv.n_books,
                disp=fv.disp, p_cons_yes=fv.p_cons_yes, p_model_yes=fv.p_model_yes,
                player_norm=fv.player_norm, exact_priced=not fv.pooled, protected=fv.protected))
        created = m.get("created_time") or m.get("created_ts") or 0
        try:
            created_ts = float(created)
        except (TypeError, ValueError):
            from datetime import datetime
            try:
                created_ts = datetime.fromisoformat(str(created).replace("Z", "+00:00")).timestamp()
            except ValueError:
                created_ts = 0.0
        out.append(ComboCandidate(market_ticker=str(m.get("ticker") or ""), legs=tuple(legs),
                                  created_ts=created_ts,
                                  unpriced_legs=unpriced or None))
    return out


def run_loop(engine: RestingEngine, client: KalshiClient, book: InMemoryFairBook,
             args) -> int:
    """Discovery -> consider; every reconcile-sec, the kill-switch poll. SIGTERM /
    SIGINT cancel everything resting before exit."""
    stop = {"flag": False}

    def _stop(signum, frame):  # noqa: ARG001
        stop["flag"] = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass
    started = time.time()
    last_reconcile = 0.0
    while not stop["flag"]:
        now = time.time()
        if args.max_runtime_sec and now - started >= args.max_runtime_sec:
            log.info("max runtime %.0fs reached: exiting cleanly", args.max_runtime_sec)
            break
        try:
            markets = client.request("GET", "/markets",
                                     params={"status": "open", "mve_filter": "true", "limit": 200},
                                     auth=False).get("markets", [])
            for cand in candidates_from_markets(markets, book, now):
                decision = engine.consider(cand, now)
                if decision["action"] == "POSTED":
                    log.info("posted resting NO on %s at %.2f", cand.market_ticker,
                             decision["order"].no_price)
        except Exception as e:  # noqa: BLE001 -- a discovery failure must not kill the kill switch
            log.warning("discovery poll failed: %r", e)
        if now - last_reconcile >= args.reconcile_sec:
            last_reconcile = now
            result = engine.reconcile(now)
            if not result["ok"]:
                log.error("RECONCILIATION KILL SWITCH TRIPPED: %s", result["problems"])
                break
        time.sleep(args.poll_sec)
    if engine.orders:
        log.info("shutdown: cancelling %d resting orders", engine.cancel_all(time.time()))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Kalshi resting combo maker (DEMO only)")
    ap.add_argument("--env", default=None, help="default: $KALSHI_RESTING_ENV or demo (anything else refuses)")
    ap.add_argument("--base-url", default=None, help="default: the demo base URL (anything else refuses)")
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--feed", default="EVSweep/nfl_props/latest.json")
    ap.add_argument("--poll-sec", type=float, default=5.0)
    ap.add_argument("--reconcile-sec", type=float, default=30.0)
    ap.add_argument("--max-runtime-sec", type=float, default=0.0)
    ap.add_argument("--max-open-orders", type=int, default=None)
    ap.add_argument("--max-total-collateral", type=float, default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    env_name, base_url = resolve_resting_run(
        args.env or os.environ.get("KALSHI_RESTING_ENV"), args.base_url)
    key_id, pem = load_credentials(args.env_file)
    client = KalshiClient(env_name, key_id, pem, base_url=base_url)
    assert_demo_only(client.env, client.base_url)     # defence in depth on the built client

    book = InMemoryFairBook()
    try:
        rows, published = load_feed_rows(args.feed)
        book.load_rows([dict(r, updated_ts=published) for r in rows] if published else rows)
        log.info("fair book: %d legs from %s", len(book), args.feed)
    except Exception as e:  # noqa: BLE001 -- an empty book prices nothing; the engine fails closed
        log.warning("fair book unavailable (%r): starting empty, nothing will be admitted", e)

    limits = RestingLimits()
    if args.max_open_orders is not None or args.max_total_collateral is not None:
        from dataclasses import replace
        limits = replace(limits,
                         max_open_orders=args.max_open_orders or limits.max_open_orders,
                         max_total_collateral=args.max_total_collateral or limits.max_total_collateral)
    engine = build_engine(client, limits)
    log.info("resting daemon: env=%s base=%s limits=%s", env_name, base_url, limits)
    return run_loop(engine, client, book, args)


if __name__ == "__main__":
    sys.exit(main())
