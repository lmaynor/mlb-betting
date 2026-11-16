#!/usr/bin/env python3
"""scripts/probe_action_network_league.py -- per-league discovery probe (BNK-11).

The harvest is discovery-driven: candidate type keys are extracted from each
league's own site config (nba.odds.action_network.LEAGUE_MARKET_TYPES), and
this probe decides which of them actually return rows on this endpoint.
For every candidate type it makes ONE call on the probe date and records
status / row count / books present; verified types (HTTP 200 with rows) are
written as the league's manifest (Odds/actionnetwork_{league}/
verified_markets.json), which the backfill runner then banks. It also runs
the date-depth ladder on the league's marquee type (LEAGUE_LADDER_TYPE) and
the gamelines (scoreboard) surface: games, rows, and which books carry
real (non-consensus/open) rows per ladder date -- the observed real-book
horizon that shard ranges are committed against.

DISABLED BY DEFAULT (house pattern): without BOTH --execute AND env
ACTION_NETWORK_BANK_ENABLED=1, zero calls and zero writes; prints the plan.

Usage:
    ACTION_NETWORK_BANK_ENABLED=1 python3 scripts/probe_action_network_league.py \
        --execute --league nhl --probe-date 20261114 \
        --ladder 20261114,20260115,20250115,20250423
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nba.config import action_network_manifest_key
from nba.odds.action_network import (GAMELINES_SLUG, LEAGUE_LADDER_TYPE,
                                     LEAGUE_MARKET_TYPES, ActionNetworkClient,
                                     AdaptivePacer, parse_body, payload_games)
from scripts.bank_action_network import _cache_storage_client, execution_allowed

log = logging.getLogger("probe_action_network_league")


def _rows_for(payload, pick_type):
    n, books = 0, {}
    for book_key, by_period in (payload.get("markets") or {}).items():
        outs = ((by_period or {}).get("event") or {}).get(pick_type) or []
        if outs:
            books[str(book_key)] = len(outs)
            n += len(outs)
    return n, books


def _gameline_stats(payload):
    books = {}
    rows = 0
    for g in payload_games(payload):
        for book_key, by_period in (g.get("markets") or {}).items():
            n = sum(len(v or []) for v in ((by_period or {}).get("event") or {}).values())
            if n:
                books[str(book_key)] = books.get(str(book_key), 0) + n
                rows += n
    return rows, books


def main(argv=None, environ=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--league", required=True)
    ap.add_argument("--probe-date", required=True, help="recent in-season date, YYYYMMDD")
    ap.add_argument("--ladder", default="", help="comma-separated YYYYMMDD depth-ladder dates")
    ap.add_argument("--out", default="", help="also write the manifest JSON to this local path")
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args(argv)
    environ = os.environ if environ is None else environ
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.league not in LEAGUE_MARKET_TYPES:
        print(f"ERROR: unknown league {a.league!r}")
        return 2
    table = LEAGUE_MARKET_TYPES[a.league]
    ladder_dates = [d.strip() for d in a.ladder.split(",") if d.strip()]
    plan = {"league": a.league, "candidate_types": len(table), "probe_date": a.probe_date,
            "ladder_dates": ladder_dates, "manifest": action_network_manifest_key(a.league)}
    print(json.dumps(plan, indent=1))
    allowed, reason = execution_allowed(a.execute, environ)
    if not allowed:
        print(f"mode: PLAN ONLY ({reason}); zero calls, zero writes.")
        return 0

    from mlb_core import storage
    _cache_storage_client(storage)
    client = ActionNetworkClient()
    pacer = AdaptivePacer(shard=f"probe-{a.league}",
                          wall=lambda: datetime.now(timezone.utc).isoformat())
    results = {}

    def call(fn):
        pacer.wait()
        return fn()

    # 1) type verification on the probe date
    verified = []
    for slug, pick_type in table.items():
        res = call(lambda: client.fetch(a.league, a.probe_date, pick_type))
        rec = {"http": res.status, "rows": 0, "books": {}}
        if res.status == 200:
            try:
                n, books = _rows_for(parse_body(res.body), pick_type)
                rec.update(rows=n, books=books)
            except Exception as exc:
                rec["error"] = f"{type(exc).__name__}: {exc}"
        results[slug] = rec
        if rec["rows"] > 0:
            verified.append(slug)
        log.info("%s %s -> %s rows=%d", a.league, slug, res.status, rec["rows"])

    # 2) gamelines on the probe date
    res = call(lambda: client.fetch_scoreboard(a.league, a.probe_date))
    gl = {"http": res.status, "games": 0, "rows": 0, "books": {}}
    if res.status == 200:
        payload = parse_body(res.body)
        gl["games"] = len(payload_games(payload))
        gl["rows"], gl["books"] = _gameline_stats(payload)
    results[GAMELINES_SLUG] = gl
    if gl["rows"] > 0:
        verified.append(GAMELINES_SLUG)

    # 3) date-depth ladder on the marquee type
    marquee = LEAGUE_LADDER_TYPE[a.league]
    ladder = {}
    for d in ladder_dates:
        res = call(lambda: client.fetch(a.league, d, table[marquee]))
        rec = {"http": res.status, "rows": 0, "books": {}}
        if res.status == 200:
            try:
                rec["rows"], rec["books"] = _rows_for(parse_body(res.body), table[marquee])
            except Exception as exc:
                rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["real_books"] = sorted(b for b in rec["books"] if b not in ("15", "30"))
        ladder[d] = rec
        log.info("ladder %s %s -> %s rows=%d real_books=%s", a.league, d, res.status,
                 rec["rows"], rec["real_books"])

    manifest = {
        "league": a.league, "probed_at": datetime.now(timezone.utc).isoformat(),
        "probe_date": a.probe_date, "markets": verified,
        "results": results, "ladder_type": marquee, "ladder": ladder,
        "adjustments": pacer.adjustments,
    }
    storage.write_bytes(json.dumps(manifest, indent=1).encode(),
                        action_network_manifest_key(a.league))
    if a.out:
        Path(a.out).write_text(json.dumps(manifest, indent=1))
    print(json.dumps({"league": a.league, "verified": verified,
                      "gamelines": gl, "ladder": ladder}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
