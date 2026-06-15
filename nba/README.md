# NBA data package

Data-collection-first basketball expansion. This package fetches and flattens
SportsBlaze NBA box-score data into the shared GCS data lake. **There is no NBA
model, odds feed, or betting runner yet** -- odds sourcing is a separate, later
effort. See `handoffs/scope_nba_expansion_2026-06-14.md` for the full plan.

## Source: SportsBlaze

Base `https://cache.sportsblaze.com`, **no auth**. Cache/CDN domain.

| Endpoint | Returns |
|---|---|
| `GET /seasons/nba` | available seasons (2018-19 .. 2025-26; `year` = start year) |
| `GET /teams/nba` | 30 teams (uuid, name, abbreviation) |
| `GET /players/nba` | current rosters (uuid, name, position, number) |
| `GET /schedule/nba/{year}` | season games + scores + status |
| `GET /boxscores/nba/{YYYY-MM-DD}` | per-date team + player box stats |

19 stat fields (identical for team and player `statistics.total`): points, minutes,
plus_minus, field_goals_made/attempted, two_points_made/attempted,
three_points_made/attempted, free_throws_made/attempted, rebounds (+offensive/defensive),
assists, steals, blocks, turnovers, fouls. `minutes` is a `"MM:SS"` string.

`season.type` in ("Preseason", "Regular Season", "Playoffs"). Empty days return
`{"events": []}`. Gaps: no odds, no advanced stats, no play-by-play, no injuries.

## Layout

```
nba/
  config.py            endpoints, GCS keys, season window, STAT_FIELDS
  data/
    sportsblaze.py     SbClient (retry/backoff, 404 -> None)
    flatten.py         flatten_boxscores(raw) -> (games, team_box, player_box)
    masters.py         upsert(key, rows) -- merge+dedupe+write a master
    backfill.py        one-time multi-season pull (resume-safe)
    refresh.py         nightly incremental (yesterday); no-op offseason
```

## GCS (shared bucket, `NBA/` prefix)

```
NBA/
  raw/
    seasons.json  teams.json  players.json
    schedule_{year}.json
    boxscores/{YYYY-MM-DD}.json      raw daily dumps (idempotent cache)
  games_master.csv                   one row / game
  team_boxscores_master.csv          one row / (game, team)
  player_boxscores_master.csv        one row / (game, player)
  last_refresh.json                  nightly sentinel
```

Master keys: games on `game_id`; team_box on `(game_id, team_id)`; player_box on
`(game_id, player_id)`. Re-runs are safe (dedupe keeps the latest).

## Running

```bash
# One-time backfill (Cloud Shell, shared bucket):
GCS_BUCKET=concrete-crow-445205-m4-mlb-data \
    python3 -m nba.data.backfill --seasons 2019-2025

# Nightly refresh (what the Cloud Run Job runs):
GCS_BUCKET=concrete-crow-445205-m4-mlb-data python3 -m nba.data.refresh

# Local dry run (no bucket -> writes under MLB_BASE_DATA):
python3 -m nba.data.backfill --seasons 2024
```

The backfill caches each day's raw JSON, so a re-run reads from GCS instead of
re-hitting the API (use `--force` to refetch). ~1,900 date requests for 7 seasons
on a cold run (~0.4s throttle).
