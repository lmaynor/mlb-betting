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
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
import os
import signal
import sys
import time
import zlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ev.kalshi_game_banked import BANKED_SPORTS, banked_game_rows  # noqa: E402
from ev.kalshi_game_legs import fetch_game_rows  # noqa: E402
from ev.kalshi_rfq_props import load_all_prop_indices  # noqa: E402
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


def risk_config_from_args(args) -> RFQConfig:
    """RFQConfig defaults, overridden by any --max-* flag that was passed. Defaults are unchanged when none are."""
    over = {f: getattr(args, a) for f, a in (("max_liability_per_quote_dollars", "max_liability_per_quote"),
                                             ("max_exposure_per_game_dollars", "max_exposure_per_game"),
                                             ("max_exposure_per_player_dollars", "max_exposure_per_player"),
                                             ("max_open_quotes", "max_open_quotes"))
            if getattr(args, a, None) is not None}
    return replace(RFQConfig(), **over)


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
    # Cloud Run: the PEM arrives as an env var from Secret Manager (no key file on disk); a file path still works locally.
    pem_k = "KALSHI_PROD_PRIVATE_KEY_PEM" if prod else "KALSHI_PRIVATE_KEY_PEM"
    pem_env = os.environ.get(pem_k)
    if vals.get(id_k) and pem_env:
        return vals[id_k], pem_env.encode()
    if not (vals.get(id_k) and vals.get(path_k)):
        raise SystemExit(f"{rfq_env} credentials not found ({id_k} / {path_k} or {pem_k})")
    with open(os.path.expanduser(vals[path_k]), "rb") as fh:
        return vals[id_k], fh.read()


class FeedState:
    """The fair book is the union of three feeds that refresh on different clocks: the props file (cheap, every --reload-sec),
    the BANKED game lines (zero credits, every --banked-reload-sec: BestBook ladders, else the banked ParlayAPI snapshot) and the
    live game-winner slate pulls (1 ParlayAPI credit per market per sport per pull, every --game-reload-sec), which only run for
    a sport the banked feeds could not serve. Each refresh swaps the whole book atomically with the latest rows of ALL of them."""

    def __init__(self, book: InMemoryFairBook, feed: str, hot_file: str | None, game_client=None, game_sports=(),
                 prop_sports=(), game_markets=("h2h", "spreads", "totals"), prop_loader=None, banked_sports=(), banked_loader=None):
        self.book, self.feed, self.hot_file = book, feed, hot_file
        self.game_client, self.game_sports = game_client, tuple(game_sports)
        self.prop_sports, self.game_markets = tuple(prop_sports), tuple(game_markets)
        self.banked_sports = tuple(banked_sports)
        self._prop_loader = prop_loader or load_all_prop_indices
        self._banked_loader = banked_loader or banked_game_rows
        self.props_rows: list[dict] = []
        self.banked_rows: list[dict] = []
        self.banked_ok: set = set()                        # sports the LAST banked refresh served (no live pull for these)
        self.game_rows: list[dict] = []

    def _apply(self) -> int:
        n = self.book.load_rows(self.props_rows + self.banked_rows + self.game_rows)
        if self.hot_file and os.path.exists(self.hot_file):
            with open(self.hot_file) as fh:
                for gk in json.load(fh) or []:
                    self.book.mark_hot(str(gk))
        return n

    def refresh_props(self) -> int:
        """Props = the sweep file's Kalshi-joined NFL rows (direct tickers) PLUS the banked NFL/NBA/NHL prop feeds indexed for
        resolution from the ticker alone (zero ParlayAPI credits). Either may be missing; the other still loads."""
        err = None
        try:
            rows, published = load_feed_rows(self.feed)
            self.props_rows = [dict(r, updated_ts=published) for r in rows] if published else rows
        except Exception as e:                             # noqa: BLE001
            err = e
            log.warning("props file %s unavailable: %r", self.feed, e)
        if self.prop_sports:
            indices, stats = self._prop_loader(self.prop_sports)
            if indices:                                    # keep the previous indices if every sport failed this time
                self.book.attach_prop_indices(indices)
            log.info("prop indices: %s", json.dumps(stats, default=str))
        elif err is not None:
            raise err
        return self._apply()

    def refresh_banked(self) -> int:
        """Game / spread / total legs from the free banked feeds. A sport that served rows replaces its previous rows and drops any
        live-pull rows for it; one that failed keeps its previous rows (the staleness gate ages them out) and is left to the live pull."""
        if not self.banked_sports:
            return len(self.book)
        rows, stats = self._banked_loader(self.banked_sports, time.time())
        ok = {r["sport"] for r in rows}
        self.banked_ok = ok
        if ok:
            self.banked_rows = [r for r in self.banked_rows if r["sport"] not in ok] + rows
            self.game_rows = [r for r in self.game_rows if r["sport"] not in ok]
        log.info("banked game legs: %d rows | %s", len(rows), json.dumps(stats, default=str))
        return self._apply()

    def refresh_games(self) -> int:
        live = [s for s in self.game_sports if s not in self.banked_ok]
        if self.game_client is None or not live:
            return len(self.book)
        rows, stats = fetch_game_rows(self.game_client, live, time.time(), self.game_markets)
        if rows:                                           # a failed pull keeps the previous rows (staleness gate ages them out)
            got = {r["sport"] for r in rows}
            self.game_rows = [r for r in self.game_rows if r["sport"] not in got] + rows
        log.info("game legs (live pull, %s): %d rows | %s", ",".join(live), len(rows), json.dumps(stats, default=str))
        return self._apply()


def refresh_book(book: InMemoryFairBook, feed: str, hot_file: str | None) -> int:
    """Props-only refresh (kept for callers/tests that have no game feed)."""
    return FeedState(book, feed, hot_file).refresh_props()


def split_sports(spec: str | None) -> tuple:
    """'nfl,nba' / 'nfl:nba' / 'nfl;nba' -> ('nfl', 'nba'). ':' and ';' also separate (gcloud --set-env-vars splits on commas,
    so the job passes 'nfl:nba:nhl')."""
    return tuple(x for x in (spec or "").replace(" ", "").replace(":", ",").replace(";", ",").split(",") if x)


def banked_sports_for(game_source: str, game_sports: tuple) -> tuple:
    """Sports whose game lines come from the free banked feeds first. 'live' = the old behaviour (ParlayAPI pulls only)."""
    if game_source == "live":
        return ()
    return tuple(s for s in game_sports if s in BANKED_SPORTS)


def make_game_client(sports: str):
    """ParlayAPI client for the game-winner pulls, or None (with a warning) when there is no key / no sports."""
    sports = split_sports(sports)
    if not sports:
        return None, ()
    if not os.environ.get("PARLAY_API_KEY"):
        log.warning("PARLAY_API_KEY not set: game-winner legs disabled (props feed only)")
        return None, ()
    from nba.odds.parlayapi import ParlayApiClient
    return ParlayApiClient(), sports


INTAKE_SHARDS = 8
INTAKE_QUEUE_MAXSIZE = 50_000
PREVIEW_ID_CAP = 250_000
EXECUTOR_WORKERS = 24
QUEUE_FULL_WARN_INTERVAL_SEC = 30.0

_PREVIEW_10_STRS = frozenset(("10.0000", "10.00", "10.0", "10"))
_TEN_DOLLARS = Decimal("10.00")


def is_preview_10_msg(msg: dict) -> bool:
    """True iff raw `target_cost_dollars` equals Decimal('10.00') (mirrors RFQTape.record_rfq)."""
    if not isinstance(msg, dict):
        return False
    raw = msg.get("target_cost_dollars")
    if raw is None:
        return False
    if isinstance(raw, str):
        if raw in _PREVIEW_10_STRS:
            return True
    elif not isinstance(raw, bool) and raw in (10, 10.0):
        return True
    try:
        return Decimal(str(raw)) == _TEN_DOLLARS
    except (InvalidOperation, ValueError):
        return False


def extract_rfq_id(typ: str, msg: dict) -> str:
    """Extract the RFQ identifier for sharding and preview delete matching."""
    if not isinstance(msg, dict):
        return ""
    if typ in ("rfq_created", "rfq_deleted"):
        return str(msg.get("id") or msg.get("rfq_id") or "")
    return str(msg.get("rfq_id") or msg.get("id") or msg.get("quote_id") or "")


def parse_created_ts(val) -> float | None:
    """Parse Kalshi created_ts (epoch float/int/str or ISO-8601 string) to UTC epoch seconds."""
    if val is None or isinstance(val, bool):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def format_heartbeat(connects: int, book_legs: int, stats: dict) -> str:
    """Format the watchdog heartbeat log line with shard depths, drop counters, and intake lag."""
    lag = stats.get("intake_lag_sec")
    lag_s = f"{lag:.3f}s" if lag is not None else "none"
    return (
        f"heartbeat: connects={connects} rfqs_seen={stats['rfqs_seen']} book_legs={book_legs} "
        f"q_max={stats['q_max']} q_depths={stats['q_depths']} "
        f"enqueued={stats['enqueued']} processed={stats['processed']} "
        f"dropped_preview_creates={stats['dropped_preview_creates']} "
        f"dropped_preview_deletes={stats['dropped_preview_deletes']} "
        f"dropped_queue_full={stats['dropped_queue_full']} "
        f"intake_lag={lag_s}"
    )


class IntakePipeline:
    """Decoupled O(1) producer -> N sharded bounded queues -> threaded consumer workers.

    Drops exact-$10 preview creates (and their matching deletes) at intake so the classification
    and orderbook lookup workers never see them.
    """

    def __init__(self, handler, *, n_shards: int = INTAKE_SHARDS, maxsize: int = INTAKE_QUEUE_MAXSIZE,
                 preview_cap: int = PREVIEW_ID_CAP, warn_interval_sec: float = QUEUE_FULL_WARN_INTERVAL_SEC,
                 clock=time.monotonic, wall_clock=time.time):
        self.handler = handler
        self.n_shards = max(1, int(n_shards))
        self.maxsize = int(maxsize)
        self._preview_cap = max(1, int(preview_cap))
        self._warn_interval_sec = float(warn_interval_sec)
        self._clock = clock
        self._wall_clock = wall_clock
        self._queues = [asyncio.Queue(maxsize=self.maxsize) for _ in range(self.n_shards)]
        self._preview_ids: OrderedDict[str, None] = OrderedDict()
        self._seen_creates = 0
        self._enqueued = 0
        self._processed = 0
        self._dropped_preview_creates = 0
        self._dropped_preview_deletes = 0
        self._dropped_queue_full = 0
        self._last_created_ts: float | None = None
        self._last_full_warn: float = -float("inf")
        self._workers: list[asyncio.Task] = []

    def shard_for(self, rfq_id: str) -> int:
        return zlib.crc32(rfq_id.encode("utf-8", "replace")) % self.n_shards

    def enqueue(self, typ: str, msg: dict, ts: float) -> bool:
        """O(1) producer path: preview drop check, then non-blocking put_nowait into the RFQ's shard."""
        rfq_id = extract_rfq_id(typ, msg)
        if typ == "rfq_created":
            self._seen_creates += 1
            if is_preview_10_msg(msg):
                self._dropped_preview_creates += 1
                if rfq_id and rfq_id not in self._preview_ids:
                    if len(self._preview_ids) >= self._preview_cap:
                        self._preview_ids.popitem(last=False)
                    self._preview_ids[rfq_id] = None
                return False
        elif typ == "rfq_deleted":
            if rfq_id and rfq_id in self._preview_ids:
                self._preview_ids.pop(rfq_id, None)
                self._dropped_preview_deletes += 1
                return False

        shard = self.shard_for(rfq_id)
        q = self._queues[shard]
        try:
            q.put_nowait((typ, msg, ts))
        except asyncio.QueueFull:
            self._dropped_queue_full += 1
            now_m = self._clock()
            if now_m - self._last_full_warn >= self._warn_interval_sec:
                self._last_full_warn = now_m
                log.warning("intake queue full (shard=%d maxsize=%d dropped_queue_full=%d): dropping %s",
                            shard, q.maxsize, self._dropped_queue_full, typ)
            return False
        self._enqueued += 1
        return True

    async def on_event(self, typ: str, msg: dict, ts: float) -> None:
        self.enqueue(typ, msg, ts)

    def start(self) -> list[asyncio.Task]:
        if not self._workers:
            self._workers = [
                asyncio.create_task(self._worker(i), name=f"rfq-intake-{i}")
                for i in range(self.n_shards)
            ]
        return self._workers

    async def _worker(self, shard_idx: int) -> None:
        q = self._queues[shard_idx]
        while True:
            typ, msg, ts = await q.get()
            try:
                await asyncio.to_thread(self.handler.on_event, typ, msg, ts)
            except Exception as e:                             # noqa: BLE001 -- bad event must never kill shard worker
                log.warning("intake worker %d error on %s: %r", shard_idx, typ, e)
            finally:
                self._processed += 1
                if typ == "rfq_created" and isinstance(msg, dict):
                    cts = parse_created_ts(msg.get("created_ts"))
                    if cts is not None:
                        self._last_created_ts = cts
                q.task_done()

    async def drain(self) -> None:
        await asyncio.gather(*(q.join() for q in self._queues))

    async def stop(self) -> None:
        if not self._workers:
            return
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    def counters(self) -> dict[str, int]:
        return {
            "enqueued": self._enqueued,
            "processed": self._processed,
            "dropped_preview_creates": self._dropped_preview_creates,
            "dropped_preview_deletes": self._dropped_preview_deletes,
            "dropped_queue_full": self._dropped_queue_full,
        }

    def stats(self, now: float | None = None) -> dict:
        depths = [q.qsize() for q in self._queues]
        now_ts = self._wall_clock() if now is None else now
        lag = (now_ts - self._last_created_ts) if self._last_created_ts is not None else None
        out = self.counters()
        out.update({
            "rfqs_seen": self._seen_creates,
            "q_depths": depths,
            "q_max": max(depths) if depths else 0,
            "intake_lag_sec": lag,
        })
        return out


async def amain(args) -> int:
    rfq_env, mode = resolve_run(args)
    live = mode == "live"
    if mode != "live" and os.environ.get(LIVE_ENV_FLAG) == "1":
        log.warning("%s=1 is set but mode is %r: nothing is armed, no quote will be sent", LIVE_ENV_FLAG, mode)
    key_id, pem = load_credentials(args.env_file, rfq_env)
    client = RFQReadOnlyClient(key_id, pem) if rfq_env == PROD_READONLY_ENV else RFQClient("demo", key_id, pem)
    log.info("RFQ daemon: env=%s mode=%s client=%s", rfq_env, mode, type(client).__name__)
    book = InMemoryFairBook()
    game_spec = args.game_sports if args.game_sports is not None else os.environ.get("KALSHI_RFQ_GAME_SPORTS", "nfl,nba,nhl")
    game_client, game_sports = make_game_client(game_spec)
    game_source = args.game_source or os.environ.get("KALSHI_RFQ_GAME_SOURCE", "auto")
    if game_source not in ("auto", "live"):
        raise SystemExit(f"refusing: --game-source {game_source!r} (allowed: auto, live)")
    banked_sports = banked_sports_for(game_source, split_sports(game_spec))   # free feeds first; works with no PARLAY_API_KEY
    prop_sports = tuple(x for x in (args.prop_sports if args.prop_sports is not None else os.environ.get(
        "KALSHI_RFQ_PROP_SPORTS", "nfl,nba,nhl")).replace(":", ",").replace(";", ",").replace(" ", "").split(",") if x)
    game_markets = tuple(x for x in (args.game_markets or os.environ.get("KALSHI_RFQ_GAME_MARKETS", "h2h,spreads,totals"))
                         .replace(":", ",").replace(";", ",").replace(" ", "").split(",") if x)
    # ParlayAPI bills 1 credit per market per sport per pull. Stretch the cadence to honour a daily budget instead of burning the pool.
    budget = args.max_daily_game_credits if args.max_daily_game_credits is not None else float(
        os.environ.get("KALSHI_RFQ_GAME_BUDGET", "120"))
    per_pull = max(len(game_sports) * len(game_markets), 1)
    args.game_reload_sec = max(args.game_reload_sec, per_pull * 86400.0 / max(budget, 1.0))
    log.info("game legs: banked first for %s (every %.0fs, zero credits); live pull is the fallback: %d sports x %d markets = %d "
             "credits/pull, every %.0fs = at most ~%.0f credits/day (budget %.0f); props from %s",
             list(banked_sports), args.banked_reload_sec, len(game_sports), len(game_markets), per_pull, args.game_reload_sec,
             per_pull * 86400.0 / args.game_reload_sec, budget, list(prop_sports))
    feed = FeedState(book, args.feed, args.hot_games_file, game_client, game_sports, prop_sports, game_markets,
                     banked_sports=banked_sports)
    for what, fn in (("props", feed.refresh_props), ("banked games", feed.refresh_banked), ("games", feed.refresh_games)):
        try:
            log.info("fair book after %s refresh: %d legs", what, fn())
        except Exception as e:                             # noqa: BLE001 -- the tape needs no fair book: start empty
            if mode != "record":
                raise
            log.warning("%s feed unavailable at start (%r): recording raw RFQs with what loaded; reload retries", what, e)
    max_unpriced = args.max_unpriced_legs if args.max_unpriced_legs is not None else int(
        os.environ.get("KALSHI_RFQ_MAX_UNPRICED_LEGS", "0"))
    allow_sgp = args.allow_same_game or os.environ.get("KALSHI_RFQ_ALLOW_SAME_GAME", "") == "1"
    pricer = KalshiRFQPricer(book, PricerConfig(allow_same_game_combos=allow_sgp,
                                                quote_single_leg=args.quote_single_leg, max_unpriced_legs=max_unpriced))
    log.info("pricer: max_unpriced_legs=%d game_sports=%s", max_unpriced, list(game_sports))
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=EXECUTOR_WORKERS, thread_name_prefix="rfq-worker")
    loop.set_default_executor(executor)
    tape_dir = args.tape_dir or ("data/kalshi_rfq" if rfq_env == "demo" else "data/kalshi_rfq/production")
    tape_prefix = args.tape_gcs_prefix or os.environ.get("KALSHI_RFQ_TAPE_PREFIX") or None
    tape = RFQTape(tape_dir, storage_prefix=tape_prefix)

    def _stamp_book() -> None:
        """Tape schema v2: keep the tape's book_build_id on the current fair-book vintage."""
        if mode == "record":
            tape.set_book_build_id(book.vintage_id())

    if mode == "record":
        # Tape schema v2 run header: trace every record to the exact code + config + book that made it.
        _cfg_src = {k: v for k, v in sorted(os.environ.items()) if k.startswith("KALSHI_RFQ_")}
        _cfg_src.update({"env": rfq_env, "mode": mode, "feed": args.feed,
                         "tape_prefix": tape_prefix, "max_unpriced_legs": max_unpriced,
                         "allow_same_game": allow_sgp, "quote_single_leg": bool(args.quote_single_leg)})
        tape.set_run_context(
            git_sha=os.environ.get("KALSHI_RFQ_GIT_SHA") or os.environ.get("GIT_SHA") or "unknown",
            config_hash=hashlib.sha256(json.dumps(_cfg_src, sort_keys=True, default=str).encode()).hexdigest())
        _stamp_book()
        tape.record_run({"env": rfq_env, "mode": mode, "feed": args.feed})
        if tape_prefix:
            async def _finalize_prev_day() -> None:
                """Build yesterday's derived Parquet layer (design section 3: the finalize rides the
                existing daily job). Fire-and-forget: never blocks or breaks stream start."""
                try:
                    from scripts.rfq_tape_finalize import finalize_day
                    prev = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
                    out = await asyncio.to_thread(finalize_day, prev, tape_prefix)
                    log.info("tape finalize for %s: %s", prev, json.dumps(out, default=str))
                except Exception as e:             # noqa: BLE001 -- derived layer is rebuildable, never fatal
                    log.warning("tape finalize for the previous day failed: %r", e)
            asyncio.ensure_future(_finalize_prev_day())
    if mode == "record":
        handler = RFQRecorder(
            pricer, tape, client,
            schedule=lambda delay, fn: loop.call_soon_threadsafe(
                loop.call_later, delay, lambda: loop.run_in_executor(None, fn)),
        )
        maker = None
    else:
        ledger = RFQLedger(args.db)
        gate = RFQRiskGate(risk_config_from_args(args), ledger, mode)
        maker = handler = RFQMaker(pricer, gate, ledger, client, mode)
        pricer.inventory_fn = maker.inventory
        if gate.killed:
            log.error("RFQ kill switch is SET (%s); no quotes until reset", gate.kill_reason)

    intake = IntakePipeline(handler)
    tape.set_intake_counters_fn(intake.counters)
    stream = KalshiRFQStream(rfq_env, key_id, load_private_key(pem), intake.on_event)

    async def poll():
        while True:
            try:
                for rfq in await asyncio.to_thread(client.get_rfqs, "open"):
                    await intake.on_event("rfq_created", rfq, time.time())
            except Exception as e:                         # noqa: BLE001 -- the stream is the primary feed
                log.warning("rfq poll failed: %r", e)
            await asyncio.sleep(args.poll_sec)

    async def reload():
        while True:
            await asyncio.sleep(args.reload_sec)
            try:
                await asyncio.to_thread(feed.refresh_props)
                _stamp_book()
            except Exception as e:                         # noqa: BLE001 -- keep the last good book; staleness gate rejects
                log.warning("fair book reload failed (keeping previous): %r", e)

    async def reload_banked():
        while banked_sports:
            await asyncio.sleep(args.banked_reload_sec)
            try:
                await asyncio.to_thread(feed.refresh_banked)
                _stamp_book()
            except Exception as e:                         # noqa: BLE001
                log.warning("banked game legs reload failed (keeping previous): %r", e)
        await asyncio.Event().wait()                       # nothing banked: idle (gather() must not finish)

    async def reload_games():
        while True:
            await asyncio.sleep(args.game_reload_sec)
            try:
                await asyncio.to_thread(feed.refresh_games)
                _stamp_book()
            except Exception as e:                         # noqa: BLE001
                log.warning("game legs reload failed (keeping previous): %r", e)

    async def flusher():
        while True:
            await asyncio.sleep(max(5.0, tape.flush_sec / 2))
            await asyncio.to_thread(tape.flush)

    async def watchdog():
        """Fail LOUD instead of idling silently: no WebSocket connection within 120 s means blocked egress (Kalshi's CDN
        403s some shared cloud IPs) or bad credentials. Then a heartbeat so a quiet market is distinguishable from a dead feed."""
        await asyncio.sleep(120)
        if stream.connects == 0:
            raise RuntimeError("RFQ stream never connected in 120 s: egress blocked or credentials rejected")
        while True:
            log.info("%s", format_heartbeat(stream.connects, len(book), intake.stats()))
            await asyncio.sleep(300)

    async def deadline():
        if args.max_runtime_sec:
            await asyncio.sleep(args.max_runtime_sec)
            log.info("max runtime %.0fs reached: exiting cleanly so the scheduler can start a fresh execution",
                     args.max_runtime_sec)
        else:
            await asyncio.Event().wait()

    intake.start()
    main_task = asyncio.ensure_future(asyncio.gather(stream.run(), poll(), reload(), reload_banked(), reload_games(), flusher(),
                                                  watchdog()))
    stop_task = asyncio.ensure_future(deadline())
    for sig in (signal.SIGTERM, signal.SIGINT):            # Cloud Run sends SIGTERM on shutdown: flush the tape first
        try:
            loop.add_signal_handler(sig, stop_task.cancel)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        done, _ = await asyncio.wait({main_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if main_task in done:
            main_task.result()                             # re-raise a watchdog / stream failure
    finally:
        stream.stop()
        for t in (main_task, stop_task):
            t.cancel()
        await asyncio.gather(main_task, stop_task, return_exceptions=True)
        await intake.stop()
        await asyncio.to_thread(tape.flush)
        try:
            tape.write_day_summary()                       # section 2i sidecar for the final day (best-effort)
        except Exception as e:                             # noqa: BLE001
            log.warning("final day summary write failed: %r", e)
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
    ap.add_argument("--tape-gcs-prefix", default=None,
                    help="write the tape as chunk objects under this storage prefix (default $KALSHI_RFQ_TAPE_PREFIX; "
                         "unset = local JSONL)")
    ap.add_argument("--max-runtime-sec", type=float, default=0.0,
                    help="exit cleanly after N seconds (Cloud Run Job: just under the task timeout); 0 = run forever")
    ap.add_argument("--poll-sec", type=float, default=5.0)
    ap.add_argument("--reload-sec", type=float, default=60.0)
    ap.add_argument("--game-sports", default=None,
                    help="comma list of nfl,nba,nhl game-winner legs priced from ParlayAPI h2h (needs PARLAY_API_KEY; 1 credit/"
                         "sport/pull). Default $KALSHI_RFQ_GAME_SPORTS or nfl,nba,nhl; '' disables")
    ap.add_argument("--game-markets", default=None, help="ParlayAPI game markets to pull (default $KALSHI_RFQ_GAME_MARKETS or "
                    "h2h,spreads,totals; 1 credit per market per sport per pull)")
    ap.add_argument("--game-source", choices=("auto", "live"), default=None,
                    help="auto (default, or $KALSHI_RFQ_GAME_SOURCE): game / spread / total legs from the free banked feeds (BestBook "
                         "ladders, else banked ParlayAPI) and a live ParlayAPI pull only for a sport they cannot serve; live: "
                         "ParlayAPI pulls only (the pre-2026-11-03 behaviour)")
    ap.add_argument("--banked-reload-sec", type=float, default=300.0,
                    help="how often the banked game lines are re-read (zero credits; BestBook itself refreshes hourly)")
    ap.add_argument("--game-reload-sec", type=float, default=3600.0,
                    help="minimum slate pull cadence; stretched automatically to fit --max-daily-game-credits")
    ap.add_argument("--max-daily-game-credits", type=float, default=None,
                    help="ParlayAPI credit budget per day for game legs (default $KALSHI_RFQ_GAME_BUDGET or 120)")
    ap.add_argument("--prop-sports", default=None, help="comma list of nfl,nba,nhl banked prop feeds indexed for ticker resolution "
                    "(zero credits; default $KALSHI_RFQ_PROP_SPORTS or nfl,nba,nhl; '' disables)")
    ap.add_argument("--max-unpriced-legs", type=int, default=None,
                    help="drop up to N unpriceable legs from a combo and price the rest as an upper bound (default "
                         "$KALSHI_RFQ_MAX_UNPRICED_LEGS or 0 = strict)")
    ap.add_argument("--allow-same-game", action="store_true", help="quote same-game combos (SGP); off until shadow markouts")
    ap.add_argument("--quote-single-leg", action="store_true", help="also quote single-market RFQs (off by default)")
    ap.add_argument("--max-liability-per-quote", type=float, default=None,
                    help="$ of collateral one quote may lock (default RFQConfig 1000, scaffolding-loose; was 50)")
    ap.add_argument("--max-exposure-per-game", type=float, default=None, help="$ across open quotes per game (default RFQConfig 5000, scaffolding-loose; was 150)")
    ap.add_argument("--max-exposure-per-player", type=float, default=None,
                    help="$ across open quotes per player; a combo's whole collateral counts against EACH of its players (default 2500, scaffolding-loose; was 75)")
    ap.add_argument("--max-open-quotes", type=int, default=None, help="open quote cap (default RFQConfig 100 = Kalshi's own cap; was 25)")
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
