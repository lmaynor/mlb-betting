"""Fetch an NBA odds snapshot from The Odds API and land it in GCS.

Manual / CLI only -- NO scheduler yet. Odds are only useful in-season alongside a
model, and the free tier is 500 requests/month, so we do not burn credits on an
offseason cron. Wire a scheduler when an NBA model goes live.

Writes:
  NBA/odds/raw/{date}/{kind}_{HHMM}.json   raw API payloads (game_lines | props)
  NBA/odds/{date}/{kind}_{HHMM}.csv        flattened rows (best-book for props)
  NBA/odds/latest.json                     pointer to the most recent snapshot

Credit cost: game_lines = 1; props = 1 per event (guard with max_events).

Run (from Cloud Run / anywhere the API is reachable):
    GCS_BUCKET=concrete-crow-445205-m4-mlb-data THE_ODDS_API_KEY=... \
        python3 -m nba.odds.snapshot --kind game_lines
    python3 -m nba.odds.snapshot --kind props --max-events 6
"""
import argparse
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from mlb_core import storage
from nba.config import (ODDS_LATEST, TIMEZONE, odds_csv_key, odds_raw_key)
from nba.odds import extract
from nba.odds.theoddsapi import OddsApiClient

logger = logging.getLogger(__name__)


def _now():
    et = datetime.now(ZoneInfo(TIMEZONE))
    return et.date().isoformat(), et.strftime("%H%M")


def _write_latest(payload: dict):
    payload = dict(payload)
    payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    storage.write_bytes(json.dumps(payload, default=str).encode(), ODDS_LATEST)


def snapshot_game_lines(client: OddsApiClient = None) -> dict:
    client = client or OddsApiClient()
    date, hhmm = _now()
    events = client.get_game_lines()
    storage.write_bytes(json.dumps(events).encode(), odds_raw_key(date, "game_lines", hhmm))
    rows = extract.flatten_game_lines(events)
    if rows:
        storage.write_csv(pd.DataFrame(rows), odds_csv_key(date, "game_lines", hhmm))
    result = {"kind": "game_lines", "date": date, "hhmm": hhmm,
              "events": len(events), "rows": len(rows),
              "credits_remaining": client.credits_remaining}
    _write_latest(result)
    logger.info("game_lines snapshot: %s", result)
    return result


def snapshot_props(client: OddsApiClient = None, max_events: int = None) -> dict:
    client = client or OddsApiClient()
    date, hhmm = _now()
    events = client.get_events()
    if max_events:
        events = events[:max_events]
    raw_objs, all_rows = [], []
    for ev in events:
        obj = client.get_event_player_props(ev["id"])
        if not obj:
            continue
        raw_objs.append(obj)
        all_rows.extend(extract.flatten_player_props(obj))
    storage.write_bytes(json.dumps(raw_objs).encode(), odds_raw_key(date, "props", hhmm))
    best = extract.best_book_props(all_rows)
    if best:
        storage.write_csv(pd.DataFrame(best), odds_csv_key(date, "props", hhmm))
    result = {"kind": "props", "date": date, "hhmm": hhmm,
              "events_priced": len(raw_objs), "prop_rows": len(all_rows),
              "best_book_rows": len(best), "credits_remaining": client.credits_remaining}
    _write_latest(result)
    logger.info("props snapshot: %s", result)
    return result


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["game_lines", "props"], default="game_lines")
    ap.add_argument("--max-events", type=int, default=None,
                    help="cap props events (each costs 1 credit)")
    args = ap.parse_args()
    if args.kind == "game_lines":
        snapshot_game_lines()
    else:
        snapshot_props(max_events=args.max_events)


if __name__ == "__main__":
    main()
