---
module: nba
tags: [sportsblaze, nba, api, ingest]
problem_type: integration-gotcha
---

# SportsBlaze NBA API quirks

Source for the NBA expansion. `https://cache.sportsblaze.com`, **no auth**.
Client: `nba/data/sportsblaze.py`. Flatten: `nba/data/flatten.py`.

## Endpoints

- `GET /seasons/nba` -- 8 seasons (2018-19 .. 2025-26).
- `GET /teams/nba` -- 30 teams (uuid, name, abbreviation).
- `GET /players/nba` -- **current rosters only** (no per-season history). Historical
  player identity comes from the boxscore player rows themselves (id+name per game).
- `GET /schedule/nba/{year}` -- `year` is the **season start year** (2025 = 2025-26).
- `GET /boxscores/nba/{YYYY-MM-DD}` -- per-date team + player box stats.

## Gotchas

- **Season `year` = start year.** 2025 means the 2025-26 season (Oct 2025 -> Jun 2026).
- **Two "empty day" responses, both handled.** In-season off-days return HTTP 200 with
  `{"events": []}`. Out-of-window dates (early preseason, deep offseason) return **HTTP
  404**. The client maps 404 -> None and the backfill/refresh coerce both to
  `{"events": []}`, so neither is fatal. Expect a burst of 404 warnings at the start of
  each season window during backfill -- harmless.
- **Stats live at the EVENT level, not under teams.** Team totals are at
  `event.statistics.{away,home}.total`; player rows at `event.players.{away,home}[]`
  (each with `statistics.total`). `event.teams.{away,home}` carries only id/name/abbr.
- **19 stat fields**, identical for team and player totals. `minutes` is a `"MM:SS"`
  **string**, not a number -- kept as-is in the masters.
- **Periods 5+ are OT.** `scores.periods` is keyed "1".."N"; flatten sums periods >= 5
  into `*_ot`.
- **`season.type`** in ("Preseason", "Regular Season", "Playoffs"). Playoffs ARE present
  in `/boxscores` (verified June 2026 Finals dates); iterate the full Oct 1 -> Jul 1
  window to capture them.
- **Team ids are unique** (30 distinct uuids). An earlier WebFetch *summary* showed Utah
  and San Antonio sharing a uuid -- that was a summarizer artifact, NOT real. Confirmed
  unique against raw JSON. (Lesson: WebFetch summarizes via an LLM; pull raw JSON with
  curl when exact structure/values matter.)
- **Cache/CDN domain** (`cache.sportsblaze.com`) -- fine for historical/nightly batch;
  watch latency/freshness if ever used for live in-game data.
- **No documented rate limit**, but throttle anyway (`NBA_REQUEST_DELAY`, default 0.4s).

## Gaps (not provided)

No odds, no advanced stats (TS%/pace/PER), no play-by-play, no shot charts, no injuries.
Advanced metrics must be DERIVED from box totals downstream.
