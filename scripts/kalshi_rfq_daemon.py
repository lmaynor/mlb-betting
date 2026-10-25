"""kalshi_rfq_daemon.py -- Kalshi RFQ market maker / combo lay engine (DEMO only).

  --mode record   price every RFQ (REST poll + WebSocket), append it and our would-be quote to
                  data/kalshi_rfq/rfq_tape_{day}.jsonl, and 15 s later pull the public trade tape for that market.
                  $0 at risk: no quote is ever sent, the ledger is not touched.
  --mode shadow   full pricer + RFQRiskGate + last-look pipeline against the sqlite ledger. Quotes are NOT sent;
                  PLAN vs REJ_* reason codes and expected P&L (sum of ev * contracts on PLAN rows) accumulate.
  --mode live     sends real quotes to the DEMO exchange and auto-confirms. Needs KALSHI_RFQ_LIVE=1.

Env flips (CLI flags win over env):
  KALSHI_RFQ_ENV=demo|production-readonly   exchange to connect to (default demo)
  KALSHI_RFQ_MODE=record|shadow|live        run mode (default record)
  KALSHI_RFQ_LIVE=1                         arms --mode live (without it live refuses to start)
  KALSHI_PROD_API_KEY_ID / KALSHI_PROD_PRIVATE_KEY_PATH   production READ credentials (kept apart from the demo ones)

`production-readonly` records the REAL RFQ flow: it is record-mode only, uses RFQReadOnlyClient (no quote methods) over
a KalshiClient that refuses every non-GET before the network, and a receive-only WebSocket. Production QUOTING is not
implemented anywhere: live is demo-only. Fair values come from --feed: the sweep's
EVSweep/nfl_props/latest.json (a storage key or a local copy) or any list of rows with the keys documented in
ev.kalshi_rfq_pricer.leg_from_row. Re-read every --reload-sec. --hot-games-file is an optional JSON list of
game_keys the lineup/injury feed flagged; those games trip the last-look shield.

Requires `pip install websockets` for the stream. Spec and design notes: docs/analysis/kalshi_rfq_combo_lay_2026-10-25.md.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ev.kalshi_rfq_pricer import (  # noqa: E402
    InMemoryFairBook, KalshiRFQPricer, PricerConfig, load_feed_rows,
)
from mlb.execution.kalshi.client import PROD_READONLY_ENV, load_private_key, read_env_keys  # noqa: E402
from mlb.execution.kalshi.rfq_client import KalshiRFQStream, RFQClient, RFQReadOnlyClient  # noqa: E402
from mlb.execution.kalshi.rfq_maker import (  # noqa: E402
    LIVE_ENV_FLAG, RFQConfig, RFQLedger, RFQMaker, RFQRecorder, RFQRiskGate, RFQTape,
)

log = logging.getLogger("kalshi_rfq_daemon")


def resolve_run(args, env=None) -> tuple[str, str]:
    """-> (exchange env, mode) from CLI flags falling back to KALSHI_RFQ_ENV / KALSHI_RFQ_MODE. Refuses any unsafe
    combination BEFORE credentials are read or anything is connected."""
    env = os.environ if env is None else env
    rfq_env = (args.env or env.get("KALSHI_RFQ_ENV") or "demo").strip().lower()
    mode = (args.mode or env.get("KALSHI_RFQ_MODE") or "record").strip().lower()
    if rfq_env not in ("demo", PROD_READONLY_ENV):
        raise SystemExit(f"refusing: KALSHI_RFQ_ENV={rfq_env!r} (allowed: demo, {PROD_READONLY_ENV})")
    if mode not in ("record", "shadow", "live"):
        raise SystemExit(f"refusing: mode {mode!r} (allowed: record, shadow, live)")
    if rfq_env == PROD_READONLY_ENV and mode != "record":
        raise SystemExit(f"refusing: {PROD_READONLY_ENV} supports --mode record only (got {mode!r}); "
                         "production quoting is not implemented")
    if mode == "live" and env.get(LIVE_ENV_FLAG) != "1":
        raise SystemExit(f"--mode live requires {LIVE_ENV_FLAG}=1")
    return rfq_env, mode


def load_credentials(env_file: str | None, rfq_env: str = "demo") -> tuple[str, bytes]:
    prod = rfq_env == PROD_READONLY_ENV
    id_k, path_k = (("KALSHI_PROD_API_KEY_ID", "KALSHI_PROD_PRIVATE_KEY_PATH") if prod
                    else ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PATH"))
    keys = ("KALSHI_ENV", id_k, path_k)
    vals = {k: os.environ.get(k) for k in keys if os.environ.get(k)}
    path = env_file or os.environ.get("KEXEC_ENV_FILE")
    if path:
        for k, v in read_env_keys(path, keys).items():
            vals.setdefault(k, v)
    if not prod and vals.get("KALSHI_ENV", "demo") != "demo":
        raise SystemExit(f"refusing: KALSHI_ENV={vals.get('KALSHI_ENV')!r} (RFQ quoting is demo-only)")
    if not (vals.get(id_k) and vals.get(path_k)):
        raise SystemExit(f"{rfq_env} credentials not found ({id_k} / {path_k})")
    with open(os.path.expanduser(vals[path_k]), "rb") as fh:
        return vals[id_k], fh.read()


def refresh_book(book: InMemoryFairBook, feed: str, hot_file: str | None) -> int:
    rows, published = load_feed_rows(feed)
    if published:
        rows = [dict(r, updated_ts=published) for r in rows]
    n = book.load_rows(rows)
    if hot_file and os.path.exists(hot_file):
        with open(hot_file) as fh:
            for gk in json.load(fh) or []:
                book.mark_hot(str(gk))
    return n


async def amain(args) -> int:
    rfq_env, mode = resolve_run(args)
    live = mode == "live"
    if mode != "live" and os.environ.get(LIVE_ENV_FLAG) == "1":
        log.warning("%s=1 is set but mode is %r: nothing is armed, no quote will be sent", LIVE_ENV_FLAG, mode)
    key_id, pem = load_credentials(args.env_file, rfq_env)
    client = RFQReadOnlyClient(key_id, pem) if rfq_env == PROD_READONLY_ENV else RFQClient("demo", key_id, pem)
    log.info("RFQ daemon: env=%s mode=%s client=%s", rfq_env, mode, type(client).__name__)
    book = InMemoryFairBook()
    n = refresh_book(book, args.feed, args.hot_games_file)
    log.info("fair book loaded: %d legs from %s", n, args.feed)
    pricer = KalshiRFQPricer(book, PricerConfig(allow_same_game_combos=args.allow_same_game,
                                                quote_single_leg=args.quote_single_leg))
    loop = asyncio.get_running_loop()
    tape_dir = args.tape_dir or ("data/kalshi_rfq" if rfq_env == "demo" else "data/kalshi_rfq/production")
    if mode == "record":
        handler = RFQRecorder(pricer, RFQTape(tape_dir), client,
                              schedule=lambda delay, fn: loop.call_later(delay, lambda: loop.run_in_executor(None, fn)))
        maker = None
    else:
        ledger = RFQLedger(args.db)
        gate = RFQRiskGate(RFQConfig(), ledger, mode)
        maker = handler = RFQMaker(pricer, gate, ledger, client, mode)
        pricer.inventory_fn = maker.inventory
        if gate.killed:
            log.error("RFQ kill switch is SET (%s); no quotes until reset", gate.kill_reason)

    async def on_event(typ, msg, ts):                      # REST calls block: keep them off the event loop
        await asyncio.to_thread(handler.on_event, typ, msg, ts)

    stream = KalshiRFQStream(rfq_env, key_id, load_private_key(pem), on_event)

    async def poll():
        while True:
            try:
                for rfq in await asyncio.to_thread(client.get_rfqs, "open"):
                    await on_event("rfq_created", rfq, time.time())
            except Exception as e:                         # noqa: BLE001 -- the stream is the primary feed
                log.warning("rfq poll failed: %r", e)
            await asyncio.sleep(args.poll_sec)

    async def reload():
        while True:
            await asyncio.sleep(args.reload_sec)
            try:
                await asyncio.to_thread(refresh_book, book, args.feed, args.hot_games_file)
            except Exception as e:                         # noqa: BLE001 -- keep the last good book; staleness gate rejects
                log.warning("fair book reload failed (keeping previous): %r", e)

    try:
        await asyncio.gather(stream.run(), poll(), reload())
    finally:
        stream.stop()
        if maker is not None and live:
            log.info("shutdown: cancelled %d open quotes", maker.cancel_all_open_quotes())
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Kalshi RFQ maker / combo lay engine (DEMO only)")
    ap.add_argument("--mode", choices=["record", "shadow", "live"], default=None, help="default: $KALSHI_RFQ_MODE or record")
    ap.add_argument("--env", choices=["demo", PROD_READONLY_ENV], default=None,
                    help="default: $KALSHI_RFQ_ENV or demo; production-readonly = record-only, GET-only")
    ap.add_argument("--feed", default="EVSweep/nfl_props/latest.json")
    ap.add_argument("--hot-games-file", default=None)
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--db", default=None, help="ledger sqlite (default: KEXEC_DB / ~/.kalshi_exec/ledger.sqlite)")
    ap.add_argument("--tape-dir", default=None, help="default data/kalshi_rfq (production-readonly: data/kalshi_rfq/production)")
    ap.add_argument("--poll-sec", type=float, default=5.0)
    ap.add_argument("--reload-sec", type=float, default=60.0)
    ap.add_argument("--allow-same-game", action="store_true", help="quote same-game combos (SGP); off until shadow markouts")
    ap.add_argument("--quote-single-leg", action="store_true", help="also quote single-market RFQs (off by default)")
    ap.add_argument("--reset-kill", action="store_true", help="clear kill_reason:kalshi_rfq and exit (manual reset only)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.reset_kill:
        RFQRiskGate(RFQConfig(), RFQLedger(args.db), "shadow").reset()
        print("kill switch cleared")
        return 0
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
