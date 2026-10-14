"""Settings for the NHL box-score / play-by-play / shots ingest (nhl.data.{nhl_api,flatten,ingest,refresh,backfill}).

Shared vocabulary (GCS master keys, API base, final states, timezone) is IMPORTED from nhl.config, which is owned
by the team/player-crosswalk work and must not be edited from here. Only constants specific to the ingest
live in this file. Everything the ingest modules need is re-exported here so they have one import point.
"""
import os

from nhl.config import (API_BASE, FINAL_STATES, GAME_TYPES, GAMES_MASTER, GOALIE_BOX_MASTER,  # noqa: F401
                        LAST_REFRESH, MASTER_KEYS, REQUEST_DELAY_SEC, SKATER_BOX_MASTER,
                        TEAM_BOX_MASTER, TIMEZONE, raw_boxscore_key)

NHL_API_BASE = API_BASE
USER_AGENT = "beezy.fyi data ingest"
NHL_PREFIX = "NHL"

# Season 2019 = 2019-20 (id 20192020). 2026 is the live season: its backfill covers games to date and the
# nightly refresh continues from there.
BACKFILL_SEASONS = list(range(2019, 2027))

# Season walk window. NOT a fixed Sep->Jul: the 2019-20 playoffs were a bubble that ran Aug-Sep 2020, and
# 2020-21 started in January. Walk Aug 1 (start year) -> Oct 15 (next year) and keep only games whose own
# `season` field matches the requested season.
WALK_START_MONTH, WALK_START_DAY = 8, 1
WALK_END_MONTH, WALK_END_DAY = 10, 15

# Nightly refresh re-fetches this many trailing days (NHL scorekeepers correct stats a day or two after the
# fact; re-upserting makes the masters self-correcting).
REFRESH_LOOKBACK_DAYS = int(os.environ.get("NHL_REFRESH_LOOKBACK_DAYS", "2"))

# -- Shot-event types we keep from play-by-play --------------------------------
SHOT_EVENTS = {
    "shot-on-goal": "sog",
    "missed-shot": "miss",
    "blocked-shot": "block",
    "goal": "goal",
}

# Attacking-goal x coordinate (feet from centre ice) for distance/angle features.
GOAL_X = 89.0


# -- GCS key helpers owned by this ingest ----------------------------------------
def raw_pbp_key(game_id: int) -> str:
    # play-by-play is ~135KB/game of repetitive JSON -- stored gzipped (see nhl.data.ingest)
    return f"{NHL_PREFIX}/raw/pbp/{game_id}.json.gz"


def shots_key(season_year: int) -> str:
    # Per-season so the nightly rewrite only touches the current season (~85k rows), not every season's shots.
    return f"{NHL_PREFIX}/shots/shots_{season_year}.csv"


SHOTS_KEY_COLS = ["game_id", "event_id"]
