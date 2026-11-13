"""Bank raw NHL shift charts to GCS for line combination derivation.

What is banked:
    Raw JSON shift-chart responses from the NHL stats API (including typeCode 517 shift rows).
    Each payload contains metadata and shift rows under "data", where typeCode 517 indicates
    a real skater or goalie shift on the ice (typeCode 505 goal events are ignored by parsers).

Why:
    Nothing else we bank records who shared the ice. Downstream module nhl.data.lines
    derives line combinations (forward lines and defense pairs) from who is on the ice together
    during 5v5 play across trailing games.

Source endpoint:
    api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId=<game_id>
    Reuses StatsClient from nhl.data.toi_splits (do NOT write a new client and do NOT change the pacing:
    1.0s default delay between requests, 429 handled via Retry-After in nhl.data.client).

Sink key:
    GCS key NHL/raw/shiftcharts/<game_id>.json.gz via nhl.data.settings.raw_shiftchart_key(game_id).
    Stored as gzip-compressed JSON bytes.

Run commands:
    # Bank trailing 10 games per team from games master
    PYTHONPATH=. python -m nhl.data.shiftcharts_bank --window 10

    # Bank trailing 10 games per team as of a specific date
    PYTHONPATH=. python -m nhl.data.shiftcharts_bank --window 10 --as-of 2024-11-20

    # Bank specific game IDs
    PYTHONPATH=. python -m nhl.data.shiftcharts_bank --game-ids 2024020001,2024020002

    # Force re-fetching and overwriting existing banked charts
    PYTHONPATH=. python -m nhl.data.shiftcharts_bank --game-ids 2024020001 --force
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
from typing import Sequence

import pandas as pd

from mlb_core import storage
from nhl.data.settings import FINAL_STATES, GAMES_MASTER, raw_shiftchart_key
from nhl.data.toi_splits import StatsClient

log = logging.getLogger("nhl.data.shiftcharts_bank")


def trailing_window_ids(
    games: pd.DataFrame,
    window: int = 10,
    as_of: str | None = None,
) -> list[int]:
    """Return the sorted union of game IDs for the trailing `window` games per team.

    Filter to regular-season games (game_type == 2) in FINAL_STATES, optionally cut off
    at date <= as_of. For each team in home_abbr or away_abbr, sort by (date, game_id)
    and keep the last `window` games.
    """
    if games is None or games.empty:
        return []

    df = games[(games["game_type"] == 2) & (games["state"].isin(FINAL_STATES))].copy()
    if df.empty:
        return []

    df["date"] = df["date"].astype(str)
    if as_of is not None:
        df = df[df["date"] <= str(as_of)]
        if df.empty:
            return []

    teams = set(df["home_abbr"].dropna()).union(set(df["away_abbr"].dropna()))
    selected_ids: set[int] = set()

    for team in sorted(teams):
        team_games = df[(df["home_abbr"] == team) | (df["away_abbr"] == team)]
        team_games = team_games.sort_values(["date", "game_id"])
        if window > 0:
            team_games = team_games.tail(window)
        else:
            team_games = team_games.iloc[0:0]
        selected_ids.update(int(gid) for gid in team_games["game_id"])

    return sorted(selected_ids)


def bank_game(
    game_id: int,
    client: StatsClient | None = None,
    force: bool = False,
) -> str:
    """Bank the raw shift chart for a single game to GCS if not already present.

    Returns:
        "already": shift chart already exists in storage and force is False.
        "missing": API returned empty/falsy payload or no "data" rows (nothing written).
        "banked": successfully fetched and wrote gzipped JSON to storage.
    """
    key = raw_shiftchart_key(int(game_id))
    if not force and storage.exists(key):
        return "already"

    cl = client or StatsClient()
    payload = cl.shiftcharts(int(game_id))

    if not payload or not payload.get("data"):
        return "missing"

    compressed = gzip.compress(json.dumps(payload).encode("utf-8"))
    storage.write_bytes(compressed, key)
    return "banked"


def run(
    window: int = 10,
    as_of: str | None = None,
    game_ids: Sequence[int | str] | None = None,
    delay: float = 1.0,
    force: bool = False,
    client: StatsClient | None = None,
) -> dict[str, int]:
    """Bank shift charts for a trailing window or explicit list of game IDs.

    Returns:
        A dict with counts: requested, already, banked, missing, failed.
    """
    if game_ids is not None:
        ids = [int(x) for x in game_ids.split(",") if x.strip()] if isinstance(game_ids, str) else [int(x) for x in game_ids]
    else:
        df = storage.read_csv(GAMES_MASTER, low_memory=False)
        ids = trailing_window_ids(df, window=window, as_of=as_of)

    summary = {
        "requested": len(ids),
        "already": 0,
        "banked": 0,
        "missing": 0,
        "failed": 0,
    }
    cl = client or StatsClient(delay=delay)

    for gid in ids:
        try:
            status = bank_game(gid, client=cl, force=force)
            if status in summary:
                summary[status] += 1
            else:
                log.warning("unexpected status %r for game %s", status, gid)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed banking game %s: %s", gid, exc)
            summary["failed"] += 1

    return summary


def main(argv: Sequence[str] | None = None) -> int:
    """CLI to bank NHL shift charts to GCS."""
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--window", type=int, default=10, help="trailing games window per team (default: 10)")
    ap.add_argument("--as-of", dest="as_of", help="YYYY-MM-DD cutoff date for games")
    ap.add_argument("--game-ids", help="comma list of game ids to bank (overrides trailing window)")
    ap.add_argument("--delay", type=float, default=1.0, help="delay in seconds between requests (default: 1.0)")
    ap.add_argument("--force", action="store_true", help="re-fetch and overwrite existing shift charts")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    game_ids = [int(x) for x in a.game_ids.split(",") if x.strip()] if a.game_ids else None
    summary = run(window=a.window, as_of=a.as_of, game_ids=game_ids, delay=a.delay, force=a.force)
    log.info("summary: %s", summary)

    if summary["requested"] > 0 and summary["failed"] == summary["requested"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
