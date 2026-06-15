"""ParlayAPI -> GCS odds accumulator (POC).

Banks live odds snapshots forward (there is no historical-props API). Sport-
agnostic: works for basketball_nba (in season ~Oct-Jun) and baseball_mlb (now).

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
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from mlb_core import storage
from nba.config import (PARLAY_PROP_MARKETS, TIMEZONE, oddsaccum_csv_key,
                        oddsaccum_latest_key, oddsaccum_raw_key)
from nba.odds import extract, parlay_extract
from nba.odds.parlayapi import ParlayApiClient

logger = logging.getLogger(__name__)


def _now():
    et = datetime.now(ZoneInfo(TIMEZONE))
    return et.date().isoformat(), et.strftime("%H%M")


def _write_latest(sport, payload):
    payload = dict(payload)
    payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    storage.write_bytes(json.dumps(payload, default=str).encode(), oddsaccum_latest_key(sport))


def accumulate_game_lines(sport, client=None):
    client = client or ParlayApiClient()
    date, hhmm = _now()
    events = client.get_slate(sport, markets="game")
    storage.write_bytes(json.dumps(events).encode(), oddsaccum_raw_key(sport, date, "game_lines", hhmm))
    rows = extract.flatten_game_lines(events)
    if rows:
        storage.write_csv(pd.DataFrame(rows), oddsaccum_csv_key(sport, date, "game_lines", hhmm))
    result = {"sport": sport, "kind": "game_lines", "date": date, "hhmm": hhmm,
              "events": len(events), "rows": len(rows),
              "credits_remaining": client.credits_remaining}
    _write_latest(sport, result)
    logger.info("game_lines: %s", result)
    return result


def accumulate_props(sport, markets=None, max_events=None, client=None):
    client = client or ParlayApiClient()
    markets = markets or PARLAY_PROP_MARKETS.get(sport, ["player_points"])
    date, hhmm = _now()
    slate = client.get_slate(sport)            # discover events (1 credit)
    if max_events:
        slate = slate[:max_events]
    raw_objs, all_rows = [], []
    for ev in slate:
        eid = ev.get("id")
        if not eid:
            continue
        obj = client.get_event_props(sport, eid, markets)
        if not obj:
            continue
        raw_objs.append(obj)
        all_rows.extend(parlay_extract.flatten_parlay_props(obj, sport))
    storage.write_bytes(json.dumps(raw_objs).encode(), oddsaccum_raw_key(sport, date, "props", hhmm))
    best = extract.best_book_props(all_rows)
    if best:
        storage.write_csv(pd.DataFrame(best), oddsaccum_csv_key(sport, date, "props", hhmm))
    result = {"sport": sport, "kind": "props", "date": date, "hhmm": hhmm,
              "events": len(slate), "events_priced": len(raw_objs),
              "prop_rows": len(all_rows), "best_book_rows": len(best),
              "markets": markets, "credits_remaining": client.credits_remaining}
    _write_latest(sport, result)
    logger.info("props: %s", result)
    return result


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="basketball_nba",
                    help="basketball_nba | baseball_mlb | any ParlayAPI sport key")
    ap.add_argument("--kind", choices=["props", "game_lines"], default="props")
    ap.add_argument("--markets", default="", help="comma-separated; default per sport")
    ap.add_argument("--max-events", type=int, default=None, help="cap events (credit guard)")
    args = ap.parse_args()
    markets = [m.strip() for m in args.markets.split(",") if m.strip()] or None
    if args.kind == "game_lines":
        accumulate_game_lines(args.sport)
    else:
        accumulate_props(args.sport, markets=markets, max_events=args.max_events)


if __name__ == "__main__":
    main()
