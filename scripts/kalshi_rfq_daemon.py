"""kalshi_rfq_daemon.py -- Kalshi RFQ market maker / combo lay engine (DEMO only).

  --mode record   price every RFQ (REST poll + WebSocket), append it and our would-be quote to
                  data/kalshi_rfq/rfq_tape_{day}.jsonl, and 15 s later pull the public trade tape for that market.
                  $0 at risk: no quote is ever sent, the ledger is not touched.
  --mode shadow   full pricer + RFQRiskGate + last-look pipeline against the sqlite ledger. Quotes are NOT sent;
                  PLAN vs REJ_* reason codes and expected P&L (sum of ev * contracts on PLAN rows) accumulate.
  --mode live     sends real quotes to the DEMO exchange and auto-confirms. Needs KALSHI_RFQ_LIVE=1.

Production is refused in code (RFQClient / KalshiClient). Fair values come from --feed: the sweep's
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
from mlb.execution.kalshi.client import load_private_key, read_env_keys  # noqa: E402
from mlb.execution.kalshi.rfq_client import KalshiRFQStream, RFQClient  # noqa: E402
from mlb.execution.kalshi.rfq_maker import (  # noqa: E402
    LIVE_ENV_FLAG, RFQConfig, RFQLedger, RFQMaker, RFQRecorder, RFQRiskGate, RFQTape,
)

log = logging.getLogger("kalshi_rfq_daemon")


def load_credentials(env_file: str | None) -> tuple[str, bytes]:
    keys = ("KALSHI_ENV", "KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PATH")
    vals = {k: os.environ.get(k) for k in keys if os.environ.get(k)}
    path = env_file or os.environ.get("KEXEC_ENV_FILE")
    if path:
        for k, v in read_env_keys(path, keys).items():
            vals.setdefault(k, v)
    if vals.get("KALSHI_ENV", "demo") != "demo":
        raise SystemExit(f"refusing: KALSHI_ENV={vals.get('KALSHI_ENV')!r} (RFQ quoting is demo-only)")
    if not (vals.get("KALSHI_API_KEY_ID") and vals.get("KALSHI_PRIVATE_KEY_PATH")):
        raise SystemExit("demo credentials not found (KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PATH)")
    with open(os.path.expanduser(vals["KALSHI_PRIVATE_KEY_PATH"]), "rb") as fh:
        return vals["KALSHI_API_KEY_ID"], fh.read()


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
    live = args.mode == "live"
    if live and os.environ.get(LIVE_ENV_FLAG) != "1":
        raise SystemExit(f"--mode live requires {LIVE_ENV_FLAG}=1")
    key_id, pem = load_credentials(args.env_file)
    client = RFQClient("demo", key_id, pem)
    book = InMemoryFairBook()
    n = refresh_book(book, args.feed, args.hot_games_file)
    log.info("fair book loaded: %d legs from %s", n, args.feed)
    pricer = KalshiRFQPricer(book, PricerConfig(allow_same_game_combos=args.allow_same_game,
                                                quote_single_leg=args.quote_single_leg))
    loop = asyncio.get_running_loop()
    if args.mode == "record":
        handler = RFQRecorder(pricer, RFQTape(args.tape_dir), client,
                              schedule=lambda delay, fn: loop.call_later(delay, lambda: loop.run_in_executor(None, fn)))
        maker = None
    else:
        ledger = RFQLedger(args.db)
        gate = RFQRiskGate(RFQConfig(), ledger, args.mode)
        maker = handler = RFQMaker(pricer, gate, ledger, client, args.mode)
        pricer.inventory_fn = maker.inventory
        if gate.killed:
            log.error("RFQ kill switch is SET (%s); no quotes until reset", gate.kill_reason)

    async def on_event(typ, msg, ts):                      # REST calls block: keep them off the event loop
        await asyncio.to_thread(handler.on_event, typ, msg, ts)

    stream = KalshiRFQStream("demo", key_id, load_private_key(pem), on_event)

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
    ap.add_argument("--mode", choices=["record", "shadow", "live"], default="record")
    ap.add_argument("--feed", default="EVSweep/nfl_props/latest.json")
    ap.add_argument("--hot-games-file", default=None)
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--db", default=None, help="ledger sqlite (default: KEXEC_DB / ~/.kalshi_exec/ledger.sqlite)")
    ap.add_argument("--tape-dir", default="data/kalshi_rfq")
    ap.add_argument("--poll-sec", type=float, default=5.0)
    ap.add_argument("--reload-sec", type=float, default=60.0)
    ap.add_argument("--allow-same-game", action="store_true", help="quote same-game combos (SGP); off until shadow markouts")
    ap.add_argument("--quote-single-leg", action="store_true", help="also quote single-market RFQs (off by default)")
    ap.add_argument("--reset-kill", action="store_true", help="clear kill_reason:kalshi_rfq and exit (manual reset only)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.reset_kill:
        RFQRiskGate(RFQConfig(), RFQLedger(args.db), args.mode if args.mode != "record" else "shadow").reset()
        print("kill switch cleared")
        return 0
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
