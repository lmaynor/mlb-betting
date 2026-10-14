"""Shared ingest logic for the NHL backfill + nightly refresh.

    games_in_weeks()  walk the weekly schedule payloads -> de-duped game dicts
    fetch_game()      boxscore + play-by-play through the raw GCS cache
    ingest_games()    fetch + flatten a batch of games into an accumulator
    write_masters()   upsert an accumulator into the masters
"""
import gzip
import json
import logging
from datetime import date, timedelta

from mlb_core import storage
from nhl.data.settings import (FINAL_STATES, GAME_TYPES, GAMES_MASTER, GOALIE_BOX_MASTER,
                        SKATER_BOX_MASTER, TEAM_BOX_MASTER, raw_boxscore_key, raw_pbp_key,
                        shots_key)
from nhl.data import masters
from nhl.data.flatten import flatten_game

logger = logging.getLogger(__name__)


def new_acc() -> dict:
    return {"games": [], "teams": [], "skaters": [], "goalies": [], "shots": {}, "failed": []}


def games_in_weeks(client, start: date, end: date, season: int = None) -> list:
    """Walk weekly schedule payloads from `start` through `end` (inclusive).

    schedule/{date} returns the 7 days beginning at `date` plus nextStartDate, so the walk is
    ~1 call per week and skips the offseason in a single hop. Each returned game dict carries
    `_date` (the schedule day it was listed under, ET). Filters to GAME_TYPES and, when given,
    to games whose own `season` id matches.
    """
    seen = {}
    d = start
    while d <= end:
        payload = client.get_schedule_week(d.isoformat())
        nxt = None
        if payload:
            for day in payload.get("gameWeek", []) or []:
                for g in day.get("games", []) or []:
                    if g.get("gameType") not in GAME_TYPES:
                        continue
                    if season is not None and g.get("season") != season:
                        continue
                    seen[g["id"]] = {**g, "_date": day.get("date")}
            nxt = payload.get("nextStartDate")
        try:
            nd = date.fromisoformat(nxt) if nxt else None
        except ValueError:
            nd = None
        d = nd if nd and nd > d else d + timedelta(days=7)
    return list(seen.values())


def is_final(game: dict) -> bool:
    return game.get("gameState") in FINAL_STATES


def _read_json(key):
    return json.loads(storage.read_bytes(key))


def _cached(key, loader, force):
    if force or not storage.exists(key):
        return None
    try:
        return loader(key)
    except Exception as exc:
        logger.warning("cached raw unreadable at %s (%s); refetching", key, exc)
        return None


def _read_gz(key):
    return json.loads(gzip.decompress(storage.read_bytes(key)))


def fetch_game(client, game_id: int, force: bool = False):
    """Return (boxscore, pbp) for one game, through the raw cache unless `force`.

    Only FINAL games are cached -- caching a live game's payload would freeze partial stats.
    A missing play-by-play (404) yields pbp=None; the box score is still usable (names fall
    back to the abbreviated box-score form and there are no shot rows).
    """
    box = _cached(raw_boxscore_key(game_id), _read_json, force)
    if box is None or box.get("gameState") not in FINAL_STATES:
        box = client.get_boxscore(game_id)
        if box is None:
            return None, None
        if box.get("gameState") in FINAL_STATES:
            storage.write_bytes(json.dumps(box).encode(), raw_boxscore_key(game_id))
    if box.get("gameState") not in FINAL_STATES:
        return box, None

    pbp = _cached(raw_pbp_key(game_id), _read_gz, force)
    if pbp is None:
        pbp = client.get_pbp(game_id)
        if pbp is not None:
            storage.write_bytes(gzip.compress(json.dumps(pbp).encode()), raw_pbp_key(game_id))
    return box, pbp


def ingest_games(client, games: list, force: bool = False, acc: dict = None) -> dict:
    """Fetch + flatten each FINAL game into `acc`. One bad game never aborts the batch."""
    acc = acc if acc is not None else new_acc()
    for g in games:
        gid = g["id"]
        try:
            box, pbp = fetch_game(client, gid, force)
            if box is None or box.get("gameState") not in FINAL_STATES:
                continue
            flat = flatten_game(box, pbp)
        except Exception as exc:                                            # noqa: BLE001
            logger.error("game %s failed: %r", gid, exc)
            acc["failed"].append(gid)
            continue
        acc["games"].append(flat["game"])
        acc["teams"].extend(flat["teams"])
        acc["skaters"].extend(flat["skaters"])
        acc["goalies"].extend(flat["goalies"])
        acc["shots"].setdefault(flat["game"]["season_year"], []).extend(flat["shots"])
    return acc


def write_masters(acc: dict) -> dict:
    """Upsert an accumulator into the masters. Returns net rows added per master."""
    return {
        "games_added": masters.upsert(GAMES_MASTER, acc["games"]),
        "team_rows_added": masters.upsert(TEAM_BOX_MASTER, acc["teams"]),
        "skater_rows_added": masters.upsert(SKATER_BOX_MASTER, acc["skaters"]),
        "goalie_rows_added": masters.upsert(GOALIE_BOX_MASTER, acc["goalies"]),
        "shot_rows_added": masters.upsert_shots(shots_key, acc["shots"]),
    }
