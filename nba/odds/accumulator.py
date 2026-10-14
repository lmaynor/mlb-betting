"""ParlayAPI -> GCS odds accumulator (POC).

Banks live odds snapshots forward (there is no historical-props API). Sport-
agnostic: basketball_nba (Oct-Jun), icehockey_nhl (Oct-Jun), baseball_mlb.

CREDIT GUARD (2026-10-14): every run is metered through nba.odds.credit_ledger -- a per-sport monthly
budget with a linear pace cap, a per-run cap, and a floor on the SHARED pool's x-requests-remaining header.
A sport with no configured budget (e.g. baseball_mlb, whose spend belongs to snapshot_odds' own ledger)
is REFUSED unless --no-guard is passed. Props are only bought for events starting within --window-hours,
soonest first, so a short budget always buys the most decision-relevant games.

Writes per snapshot:
  OddsAccum/{sport}/raw/{date}/{kind}_{HHMM}.json   raw API payloads
  OddsAccum/{sport}/{date}/{kind}_{HHMM}.csv        flattened rows (best-book for props)
  OddsAccum/{sport}/latest.json                     pointer + credit usage

kind=props  -> per-event props (1 credit per event x market); flattened to best-book.
kind=game_lines -> whole-slate h2h/spreads/totals (1 credit per market).

Run:
  GCS_BUCKET=... PARLAY_API_KEY=... \
    python3 -m nba.odds.accumulator --sport basketball_nba --kind props
  python3 -m nba.odds.accumulator --sport baseball_mlb --kind props --max-events 4
  python3 -m nba.odds.accumulator --sport baseball_mlb --kind game_lines
"""
import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from mlb_core import storage
from nba.config import (DFS_BOOKS, PARLAY_GAME_MARKETS, PARLAY_PROP_MARKETS, PARLAY_PROP_MARKETS_ALT,
                        TIMEZONE, oddsaccum_csv_key, oddsaccum_latest_key, oddsaccum_latest_kind_key,
                        oddsaccum_raw_key)
from nba.odds import credit_ledger, extract, parlay_extract
from nba.odds.parlayapi import ParlayApiClient

logger = logging.getLogger(__name__)


def _now():
    et = datetime.now(ZoneInfo(TIMEZONE))
    return et.date().isoformat(), et.strftime("%H%M")


def _today_et():
    return datetime.now(ZoneInfo(TIMEZONE)).date()


def _write_latest(sport, payload):
    payload = dict(payload)
    payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    blob = json.dumps(payload, default=str).encode()
    storage.write_bytes(blob, oddsaccum_latest_key(sport))
    if payload.get("kind"):
        storage.write_bytes(blob, oddsaccum_latest_kind_key(sport, payload["kind"]))


def _credits_of(client, default):
    """Credits the LAST call actually cost (x-requests-last), else the caller's estimate."""
    try:
        v = int(getattr(client, "credits_last", None))
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


def _month(today):
    return f"{today.year:04d}-{today.month:02d}"


def _start_of(ev):
    try:
        return datetime.fromisoformat(str(ev.get("commence_time", "")).replace("Z", "+00:00"))
    except ValueError:
        return None


def _in_window(slate, window_hours, now=None):
    """Events that have not started and start within window_hours, soonest first. Events with no parseable
    start time are kept (last) so a feed quirk never silently drops a game."""
    now = now or datetime.now(timezone.utc)
    horizon = now + timedelta(hours=window_hours)
    dated, undated = [], []
    for ev in slate:
        st = _start_of(ev)
        if st is None:
            undated.append(ev)
        elif now <= st <= horizon:
            dated.append((st, ev))
    dated.sort(key=lambda t: t[0])
    return [e for _, e in dated] + undated


def accumulate_game_lines(sport, client=None, guard=True):
    client = client or ParlayApiClient()
    date, hhmm = _now()
    today = _today_et()
    want = len(PARLAY_GAME_MARKETS)
    if guard:
        a = credit_ledger.allowance(sport, want, today, client.credits_remaining)
        if not a.ok:
            result = {"sport": sport, "kind": "game_lines", "date": date, "hhmm": hhmm, "skipped": True,
                      "reason": a.reason, "credits_spent": 0}
            logger.warning("game_lines SKIPPED: %s", a.reason)
            return result
    events = client.get_slate(sport, markets="game")
    spent = _credits_of(client, want)
    if guard:
        credit_ledger.record(sport, _month(today), spent)
    storage.write_bytes(json.dumps(events).encode(), oddsaccum_raw_key(sport, date, "game_lines", hhmm))
    rows = extract.flatten_game_lines(events)
    if rows:
        storage.write_csv(pd.DataFrame(rows), oddsaccum_csv_key(sport, date, "game_lines", hhmm))
    result = {"sport": sport, "kind": "game_lines", "date": date, "hhmm": hhmm,
              "events": len(events), "rows": len(rows), "credits_spent": spent,
              "credits_remaining": client.credits_remaining}
    _write_latest(sport, result)
    logger.info("game_lines: %s", result)
    return result


def accumulate_props(sport, markets=None, max_events=None, client=None, guard=True, tier="core",
                     window_hours=30.0, max_credits=0):
    client = client or ParlayApiClient()
    if markets is None:
        markets = list(PARLAY_PROP_MARKETS.get(sport, ["player_points"]))
        if tier == "full":
            markets += PARLAY_PROP_MARKETS_ALT.get(sport, [])
    date, hhmm = _now()
    today = _today_et()
    month = _month(today)
    spent = 0
    if guard:
        a0 = credit_ledger.allowance(sport, 1, today, client.credits_remaining)
        if not a0.ok:
            result = {"sport": sport, "kind": "props", "date": date, "hhmm": hhmm, "skipped": True,
                      "reason": a0.reason, "credits_spent": 0}
            logger.warning("props SKIPPED: %s", a0.reason)
            return result
    slate = client.get_slate(sport)                # discover events (1 credit)
    c = _credits_of(client, 1)
    spent += c
    if guard:
        credit_ledger.record(sport, month, c)
    slate = _in_window(slate, window_hours)
    if max_events:
        slate = slate[:max_events]
    granted = len(slate)
    if guard and slate:
        a = credit_ledger.allowance(sport, len(slate) * len(markets), today, client.credits_remaining,
                                    run_cap=max_credits)
        granted = (a.credits // len(markets)) if a.ok else 0
        if granted < len(slate):
            logger.warning("props: budget buys %d of %d events (%s)", granted, len(slate), a.reason)
        slate = slate[:granted]
    raw_objs, all_rows, event_spend = [], [], 0
    try:
        for ev in slate:
            eid = ev.get("id")
            if not eid:
                continue
            obj = client.get_event_props(sport, eid, markets)
            event_spend += _credits_of(client, len(markets))
            if not obj:
                continue
            raw_objs.append(obj)
            all_rows.extend(parlay_extract.flatten_parlay_props(obj, sport, include_one_sided=True))
    finally:
        # record real spend even if a call raised part-way: the ledger is the spend guard
        if guard:
            credit_ledger.record(sport, month, event_spend)
        spent += event_spend
    storage.write_bytes(json.dumps(raw_objs).encode(), oddsaccum_raw_key(sport, date, "props", hhmm))
    # best-book CSV: real sportsbooks only -- DFS/pick'em apps (underdog, sleeper...) carry a number, not a price
    real_rows = [r for r in all_rows if r["book"] not in DFS_BOOKS]
    best = extract.best_book_props(real_rows)
    if best:
        storage.write_csv(pd.DataFrame(best), oddsaccum_csv_key(sport, date, "props", hhmm))
    result = {"sport": sport, "kind": "props", "date": date, "hhmm": hhmm,
              "events": len(slate), "events_priced": len(raw_objs),
              "prop_rows": len(all_rows), "best_book_rows": len(best),
              "markets": markets, "credits_spent": spent,
              "credits_remaining": client.credits_remaining}
    _write_latest(sport, result)
    logger.info("props: %s", result)
    return result


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="basketball_nba",
                    help="basketball_nba | icehockey_nhl | baseball_mlb | any ParlayAPI sport key")
    ap.add_argument("--kind", choices=["props", "game_lines"], default="props")
    ap.add_argument("--markets", default="", help="comma-separated; default per sport")
    ap.add_argument("--tier", choices=["core", "full"], default="core",
                    help="core = PARLAY_PROP_MARKETS; full adds PARLAY_PROP_MARKETS_ALT (more credits/event)")
    ap.add_argument("--window-hours", type=float, default=30.0,
                    help="only buy props for events starting within this many hours")
    ap.add_argument("--max-events", type=int, default=None, help="cap events (credit guard)")
    ap.add_argument("--max-credits", type=int, default=400, help="per-run credit cap (guard)")
    ap.add_argument("--no-guard", action="store_true",
                    help="bypass the credit ledger (manual probes only -- never in a scheduled job)")
    args = ap.parse_args()
    markets = [m.strip() for m in args.markets.split(",") if m.strip()] or None
    guard = not args.no_guard
    if args.kind == "game_lines":
        accumulate_game_lines(args.sport, guard=guard)
    else:
        accumulate_props(args.sport, markets=markets, max_events=args.max_events, guard=guard,
                         tier=args.tier, window_hours=args.window_hours, max_credits=args.max_credits)


if __name__ == "__main__":
    main()
