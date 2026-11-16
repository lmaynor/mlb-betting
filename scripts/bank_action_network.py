#!/usr/bin/env python3
"""scripts/bank_action_network.py -- Action Network harvest runner (BNK-11).

Banks, per ET slate date and league: game lines (the scoreboard endpoint,
one call per date) + every verified prop/milestone type (the markets
endpoint, exactly ONE customPickType per call) from api.actionnetwork.com
into Odds/actionnetwork_{league}/{market}/{date}.json.gz (raw, gzipped,
byte-for-byte) with the normalized parquet beside it. Raw lands BEFORE any
row is derived. The gamelines call doubles as the per-date probe: a date
whose slate is empty costs one call and skips its prop types.

DISABLED BY DEFAULT (house pattern): without BOTH --execute AND env
ACTION_NETWORK_BANK_ENABLED=1 this script makes ZERO calls and ZERO writes
and prints the plan. Sharding: each shard owns a disjoint date range and a
checkpoint banked in the bucket
(Odds/actionnetwork_{league}/checkpoints/<shard>.json), so any shard is
resumable from either execution site (local VM or Cloud Run). Idempotent:
partitions whose raw object already exists are skipped. Market set: the
league's candidate table (nba.odds.action_network.LEAGUE_MARKET_TYPES),
intersected with the league's verified-markets manifest in the bucket when
one exists (written by scripts/probe_action_network_league.py).

Pacing: ~1 req/s per stream via AdaptivePacer. On throttle signals the
pacer steps DOWN and logs the adjustment (owner's rule, Nov 16: adjust, do
not hammer); the hard stop is only an outright block (3 consecutive 403s)
or a 10-partition failure streak. --time-budget-s makes a run stop cleanly
at a date boundary (Cloud Run task timeouts chain this way).

Usage (plan only -- safe anywhere):
    python3 scripts/bank_action_network.py --shard proving \
        --start 20261109 --end 20261116

Execute:
    ACTION_NETWORK_BANK_ENABLED=1 python3 scripts/bank_action_network.py \
        --execute --shard proving --start 20261109 --end 20261116
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # runnable as `python scripts/...`

from nba.config import (action_network_checkpoint_key, action_network_manifest_key,
                        action_network_parquet_key, action_network_prefix,
                        action_network_raw_key)
from nba.odds.action_network import (GAMELINES_SLUG, LEAGUE_MARKET_TYPES,
                                     ActionNetworkClient, AdaptivePacer,
                                     fetch_with_policy, gzip_body, normalize_gamelines,
                                     normalize_payload, parse_body, payload_games)

log = logging.getLogger("bank_action_network")
ENABLE_ENV = "ACTION_NETWORK_BANK_ENABLED"
FAILURE_STREAK_STOP = 10


def execution_allowed(execute_flag: bool, environ) -> tuple:
    if not execute_flag:
        return False, "no --execute flag"
    if environ.get(ENABLE_ENV) != "1":
        return False, f"{ENABLE_ENV} is not set to 1"
    return True, f"both gates present (--execute and {ENABLE_ENV}=1)"


def date_range(start: str, end: str):
    d = datetime.strptime(start, "%Y%m%d").date()
    last = datetime.strptime(end, "%Y%m%d").date()
    while d <= last:
        yield d
        d += timedelta(days=1)


def resolve_markets(league: str, spec: str, manifest_markets=None) -> list:
    """Ordered market slugs for a run: gamelines first (it is the per-date
    probe), then the league's prop types. The verified manifest, when given,
    narrows the candidate table to types proven to return rows."""
    table = LEAGUE_MARKET_TYPES[league]
    if spec == "all":
        slugs = list(table)
    elif spec == "main":
        slugs = [s for s in table if "_milestone_" not in s]
    elif spec == "milestones":
        slugs = [s for s in table if "_milestone_" in s]
    else:
        slugs = [s.strip() for s in spec.split(",") if s.strip()]
        unknown = [s for s in slugs if s not in table and s != GAMELINES_SLUG]
        if unknown:
            raise ValueError(f"unknown markets for {league}: {unknown}")
    if manifest_markets is not None:
        keep = set(manifest_markets)
        slugs = [s for s in slugs if s in keep]
    if GAMELINES_SLUG not in slugs and spec != "milestones":
        slugs = [GAMELINES_SLUG, *slugs]
    return slugs


def _cache_storage_client(storage):
    """Process-local client cache for mlb_core.storage.

    mlb_core.storage builds a fresh google.cloud.storage.Client on EVERY
    call; from this VM that costs ~1-2s per operation in credential/project
    resolution, which would dominate a ~14k-partition sweep. The write path
    stays mlb_core.storage; only the client construction is memoized, inside
    this process. (Measured Nov 16: warm fetch 0.29s vs ~4.4s/partition
    end-to-end before this cache.)"""
    orig = storage._gcs_client
    cache = {}

    def cached():
        if "client" not in cache:
            cache["client"] = orig()
        return cache["client"]

    storage._gcs_client = cached


class ShardRun:
    def __init__(self, args, environ):
        self.league = args.league
        self.shard = args.shard
        self.site = args.site
        self.start, self.end = args.start, args.end
        self.time_budget_s = args.time_budget_s
        self.t0 = time.monotonic()
        self.wall = lambda: datetime.now(timezone.utc).isoformat()
        self.pacer = AdaptivePacer(shard=self.shard, base_interval=args.base_interval,
                                   wall=self.wall)
        self.client = ActionNetworkClient()
        self.policy_state = {"consecutive_403": 0}
        self.failure_streak = 0
        self.empty_streak = 0
        self.empty_stepped = False
        self.stopped = ""
        self.log_path = Path(args.log) if args.log else None
        from mlb_core import storage
        _cache_storage_client(storage)
        self.storage = storage
        self.ckpt = self._load_checkpoint()
        self.banked = self._banked_partitions()
        manifest = self._load_manifest()
        self.markets = resolve_markets(self.league, args.markets, manifest)
        log.info("shard %s league %s: %d markets (%s)", self.shard, self.league,
                 len(self.markets), "manifest-narrowed" if manifest else "full candidate table")

    # -- state -------------------------------------------------------------
    def _load_manifest(self):
        try:
            raw = self.storage.read_bytes(action_network_manifest_key(self.league))
            markets = json.loads(raw.decode("utf-8")).get("markets")
            if isinstance(markets, list) and markets:
                return markets
        except Exception:
            pass
        return None

    def _load_checkpoint(self):
        key = action_network_checkpoint_key(self.shard, self.league)
        try:
            raw = self.storage.read_bytes(key)
            ck = json.loads(raw.decode("utf-8"))
            if ck.get("shard") == self.shard and ck.get("league") == self.league:
                log.info("resuming shard %s (%s) from banked checkpoint (%d dates recorded)",
                         self.shard, self.league, len(ck.get("dates", {})))
                return ck
        except Exception:
            pass
        return {"shard": self.shard, "league": self.league, "site": self.site,
                "start": self.start, "end": self.end,
                "status": "running", "started_at": self.wall(), "dates": {},
                "adjustments": [], "totals": {"calls": 0, "rows": 0, "partitions": 0}}

    def _banked_partitions(self):
        banked = set()
        prefix = action_network_prefix(self.league)
        try:
            for key in self.storage.list_keys(f"{prefix}/"):
                parts = key.split("/")
                if len(parts) == 4 and parts[3].endswith(".json.gz"):
                    banked.add((parts[2], parts[3][:-len(".json.gz")]))
        except Exception as exc:
            log.warning("banked-partition listing failed (%s); relying on checkpoint", exc)
        return banked

    def _save_checkpoint(self):
        self.ckpt["updated_at"] = self.wall()
        self.ckpt["adjustments"] = self.pacer.adjustments
        try:
            self.storage.write_bytes(
                json.dumps(self.ckpt, default=str).encode(),
                action_network_checkpoint_key(self.shard, self.league))
        except Exception as exc:
            log.error("checkpoint write failed: %s", exc)

    def _log_line(self, rec):
        rec = {"ts": self.wall(), "shard": self.shard, "league": self.league, **rec}
        log.info("%s", json.dumps(rec, default=str))
        if self.log_path:
            with self.log_path.open("a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")

    # -- per-partition work --------------------------------------------------
    def _fetch_partition(self, date_iso: str, yyyymmdd: str, slug: str):
        """Fetch + bank one (date, market). Returns (record, payload|None)."""
        if slug == GAMELINES_SLUG:
            call = lambda: self.client.fetch_scoreboard(self.league, yyyymmdd)  # noqa: E731
            pick_type = ""
        else:
            pick_type = LEAGUE_MARKET_TYPES[self.league][slug]
            call = lambda: self.client.fetch(self.league, yyyymmdd, pick_type)  # noqa: E731
        out = fetch_with_policy(call, self.pacer, self.policy_state)
        self.ckpt["totals"]["calls"] += out.attempts
        rec = {"status": "", "rows": 0, "books": {}, "http": None, "attempts": out.attempts,
               "notes": out.notes}
        if out.hard_stop:
            rec["status"] = "hard_stop_403"
            self.stopped = "403 wall (3 consecutive)"
            return rec, None
        res = out.result
        rec["http"] = res.status if res else None
        if out.gave_up or res is None or res.status != 200:
            rec["status"] = "failed"
            self.failure_streak += 1
            if self.failure_streak >= FAILURE_STREAK_STOP:
                self.stopped = f"failure streak {self.failure_streak}"
            return rec, None
        self.failure_streak = 0
        # RAW FIRST: the untouched body lands before any derived row exists.
        self.storage.write_bytes(gzip_body(res.body),
                                 action_network_raw_key(slug, date_iso, self.league))
        self.banked.add((slug, date_iso))
        try:
            payload = parse_body(res.body)
        except Exception as exc:
            rec["status"] = "bad_json"
            rec["notes"] = [*out.notes, f"{type(exc).__name__}: {exc}"]
            return rec, None
        scraped_at = self.wall()
        if slug == GAMELINES_SLUG:
            rows = normalize_gamelines(payload, date_iso=date_iso, scraped_at=scraped_at)
        else:
            rows = normalize_payload(payload, league=self.league, date_iso=date_iso,
                                     market_slug=slug, pick_type=pick_type,
                                     scraped_at=scraped_at)
        rec["rows"] = len(rows)
        for r in rows:
            rec["books"][str(r["book_id"])] = rec["books"].get(str(r["book_id"]), 0) + 1
        if rows:
            import pandas as pd
            self.storage.write_parquet(pd.DataFrame(rows),
                                       action_network_parquet_key(slug, date_iso, self.league))
        rec["status"] = "banked"
        self.ckpt["totals"]["rows"] += len(rows)
        self.ckpt["totals"]["partitions"] += 1
        return rec, payload

    def _date_is_empty(self, date_iso: str, yyyymmdd: str, day_rec: dict) -> bool:
        """Process the gamelines partition for a date; True when the date has
        no games (off-day: the prop calls for the date are skipped)."""
        slug = GAMELINES_SLUG
        if (slug, date_iso) in self.banked and slug in day_rec.get("markets", {}):
            return bool(day_rec.get("empty"))
        if (slug, date_iso) in self.banked:
            # banked earlier but no record (checkpoint loss): read the raw back
            try:
                payload = parse_body(gzip.decompress(self.storage.read_bytes(
                    action_network_raw_key(slug, date_iso, self.league))))
                empty = len(payload_games(payload)) == 0
                day_rec["games"] = len(payload_games(payload))
                day_rec["empty"] = empty
                return empty
            except Exception:
                return False
        rec, payload = self._fetch_partition(date_iso, yyyymmdd, slug)
        day_rec.setdefault("markets", {})[slug] = rec
        if payload is None:
            return False     # failure already recorded; do not call the date empty
        empty = len(payload_games(payload)) == 0
        day_rec["games"] = len(payload_games(payload))
        day_rec["empty"] = empty
        return empty

    # -- main loop -------------------------------------------------------------
    def run(self) -> int:
        for d in date_range(self.start, self.end):
            if self.stopped:
                break
            if self.time_budget_s and (time.monotonic() - self.t0) > self.time_budget_s:
                self.ckpt["status"] = "partial-time-budget"
                log.warning("time budget reached at %s", d.isoformat())
                break
            date_iso, yyyymmdd = d.isoformat(), d.strftime("%Y%m%d")
            day_rec = self.ckpt["dates"].setdefault(date_iso, {"markets": {}})
            empty = self._date_is_empty(date_iso, yyyymmdd, day_rec)
            if self.stopped:
                self._save_checkpoint()
                break
            if empty:
                self.empty_streak += 1
                if self.empty_streak >= 10 and not self.empty_stepped:
                    self.pacer.on_throttle("empty-response spike (10 consecutive)")
                    self.empty_stepped = True
                self._log_line({"date": date_iso, "market": "*", "status": "empty-date"})
                self._save_checkpoint()
                continue
            self.empty_streak, self.empty_stepped = 0, False
            for slug in self.markets:
                if slug == GAMELINES_SLUG or (slug, date_iso) in self.banked:
                    continue
                rec, _ = self._fetch_partition(date_iso, yyyymmdd, slug)
                day_rec["markets"][slug] = rec
                self._log_line({"date": date_iso, "market": slug, **rec})
                if self.stopped:
                    break
                if self.time_budget_s and (time.monotonic() - self.t0) > self.time_budget_s:
                    self.ckpt["status"] = "partial-time-budget"
                    log.warning("time budget reached mid-date %s", date_iso)
                    break
            self._save_checkpoint()
        if self.stopped:
            self.ckpt["status"] = f"stopped: {self.stopped}"
        elif self.ckpt.get("status") != "partial-time-budget":
            done = all((d.isoformat() in self.ckpt["dates"])
                       for d in date_range(self.start, self.end))
            self.ckpt["status"] = "complete" if done else "partial"
        self._save_checkpoint()
        summary = {"shard": self.shard, "league": self.league, "status": self.ckpt["status"],
                   "dates_recorded": len(self.ckpt["dates"]),
                   "totals": self.ckpt["totals"],
                   "adjustments": self.pacer.adjustments,
                   "elapsed_s": round(time.monotonic() - self.t0)}
        print(json.dumps(summary, default=str))
        return 3 if self.stopped else 0


def build_plan(args) -> dict:
    markets = resolve_markets(args.league, args.markets)
    n_dates = sum(1 for _ in date_range(args.start, args.end))
    return {"shard": args.shard, "league": args.league, "site": args.site,
            "start": args.start, "end": args.end,
            "dates": n_dates, "markets": len(markets),
            "calls_upper_bound": n_dates * len(markets),
            "target_prefix": f"{action_network_prefix(args.league)}/",
            "checkpoint": action_network_checkpoint_key(args.shard, args.league)}


def main(argv=None, environ=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--league", default=os.environ.get("AN_LEAGUE", "nba"))
    ap.add_argument("--shard", default=os.environ.get("AN_SHARD", ""))
    ap.add_argument("--site", default=os.environ.get("AN_SITE", "local"))
    ap.add_argument("--start", default=os.environ.get("AN_START", ""))
    ap.add_argument("--end", default=os.environ.get("AN_END", ""))
    ap.add_argument("--markets", default=os.environ.get("AN_MARKETS", "all"))
    ap.add_argument("--time-budget-s", type=int,
                    default=int(os.environ.get("AN_TIME_BUDGET_S", "0") or 0))
    ap.add_argument("--base-interval", type=float, default=1.0)
    ap.add_argument("--log", default=os.environ.get("AN_LOG", ""))
    ap.add_argument("--execute", action="store_true",
                    help=f"actually call Action Network + bank. Also requires env {ENABLE_ENV}=1")
    a = ap.parse_args(argv)
    environ = os.environ if environ is None else environ
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.league not in LEAGUE_MARKET_TYPES:
        print(f"ERROR: unknown league {a.league!r} (known: {sorted(LEAGUE_MARKET_TYPES)})")
        return 2
    if not a.shard or not a.start or not a.end:
        print("ERROR: --shard, --start and --end are required (or AN_SHARD/AN_START/AN_END).")
        return 2
    plan = build_plan(a)
    print(json.dumps(plan, indent=1))
    allowed, reason = execution_allowed(a.execute, environ)
    if not allowed:
        print(f"mode: PLAN ONLY ({reason}); zero API calls, zero writes. "
              f"To execute: {ENABLE_ENV}=1 python3 scripts/bank_action_network.py --execute "
              f"--league {a.league} --shard {a.shard} --start {a.start} --end {a.end}")
        return 0
    print("mode: EXECUTE (both gates present)")
    return ShardRun(a, environ).run()


if __name__ == "__main__":
    sys.exit(main())
