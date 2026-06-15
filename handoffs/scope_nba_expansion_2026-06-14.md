# Scope -- NBA expansion (data-first) 2026-06-14

Branching the platform into basketball. **Data-collection-first**: grab historical
NBA data now (offseason, free, complete), stand up a year-round nightly ingest, and
prep the repo + site so an NBA betting pipeline can slot in later. Odds sourcing is
owned by the user over the coming months -- **no NBA model or pick surfaces are built
until an odds feed + a trained model exist.**

Source API: **SportsBlaze** (`https://cache.sportsblaze.com`). No auth required.
8 NBA seasons available: 2018-19 through 2025-26 (season `year` = start year).

## Confirmed decisions (2026-06-14)

1. **Repo org: additive `nba/` package.** Reuse the already-sport-agnostic
   `mlb_core.storage` GCS layer. NBA data in the SAME bucket under an `NBA/` prefix.
   MLB pipeline untouched. (Future option: lift storage into a neutral `core/` if NBA
   becomes a full second pipeline -- NOT now; the MLB image is live.)
2. **Nightly ingest: year-round daily, no-op offseason.** Self-healing; auto-starts
   when games appear (~late Oct). Mirrors the MLB Savant refresh no-op pattern.
3. **UI: light multi-sport IA prep + polish.** Sport switcher + sport-scoped
   tokens/routes so NBA slots in later without a redesign; general polish via the
   `impeccable` skill. Defer NBA pick surfaces until odds + model exist.

## SportsBlaze API reference (verified)

Base: `https://cache.sportsblaze.com`. No auth. Cache/CDN domain (watch live latency).

| Endpoint | Returns |
|---|---|
| `GET /seasons/nba` | available seasons (2018-2025) |
| `GET /teams/nba` | 30 teams: id (uuid), name, abbreviation |
| `GET /players/nba` | rosters per team: player id, name, position, number |
| `GET /schedule/nba/{year}` | season games: id, season{year,type}, teams, date, status, scores{total,periods} |
| `GET /boxscores/nba/{YYYY-MM-DD}` | per-date games w/ team + player box stats |

Boxscore stat fields (19, identical for team `statistics.total` and player `statistics.total`):
`points, minutes, assists, rebounds, rebounds_offensive, rebounds_defensive, blocks,
steals, fouls, turnovers, plus_minus, field_goals_made, field_goals_attempted,
two_points_made, two_points_attempted, three_points_made, three_points_attempted,
free_throws_made, free_throws_attempted`. Player records also carry `id, name, position,
played (bool), starter (bool)`. Game carries `live (bool)` and `status` ("Final").

Gaps vs MLB stack: **no odds**, no advanced stats (TS%/pace/PER), no play-by-play,
no shot charts, no injuries. Advanced metrics must be DERIVED from box totals.

Quirks to verify during execution:
- Empty-date boxscores return `{"events": []}` (cheap no-op -- confirmed for off-days).
- Teams dump showed Utah (UTA) and San Antonio (SA) with the SAME uuid
  `58b3bdf3-abf2-5c08-b3f9-9ca7656b95f9` -- almost certainly a fetch/transcription
  artifact; VERIFY against the real JSON before trusting team ids as keys.
- Confirm playoff games are included in `/boxscores` (date-by-date should capture them
  regardless of `season.type`); confirm `season.type` values ("Preseason","Regular
  Season","Postseason"/"Playoffs"?).
- No documented rate limit, but throttle politely (~0.3-0.5s) + retry/backoff.

## Phase 1 -- Repo scaffolding (additive, no MLB disruption)

```
nba/
  __init__.py
  config.py            NBA_PREFIX="NBA", LEAGUE="nba", season window (Oct 1 -> Jul 1),
                       SB_BASE="https://cache.sportsblaze.com"
  data/
    __init__.py
    sportsblaze.py     SbClient: get_seasons(), get_teams(), get_players(),
                       get_schedule(year), get_boxscores(date). requests + retry/backoff.
    flatten.py         flatten_boxscores(raw) -> (games_df, team_box_df, player_box_df)
    backfill.py        one-time multi-season pull -> GCS raw + masters (resume-safe)
    refresh.py         nightly incremental (yesterday) -> append to masters + sentinel
  README.md            NBA data dictionary + run instructions
tests/
  test_nba_flatten.py  parse a saved fixture -> flatten -> column/dtype assertions
```

- All GCS I/O via `mlb_core.storage` (`read_csv/write_csv/read_bytes/write_bytes/exists/
  list_keys/stat`). No new storage code.
- `nba/` importing `mlb_core.storage` is a cosmetic dependency direction; acceptable
  until a future `core/` extraction.
- Add `nba` to `setup.py` packages and a `COPY nba/ ...` line to the Dockerfile so the
  existing image can run NBA jobs.

## Phase 2 -- Backfill 7 seasons (2019-2025), one-time

`nba/data/backfill.py`:
1. `GET /seasons/nba` -> confirm available years; snapshot `NBA/raw/seasons.json`.
2. Snapshot `NBA/raw/teams.json` and `NBA/raw/players_{year}.json`.
3. For each year in 2019..2025 (7 seasons; 2018 available if we want 8):
   - iterate every date in the season window (Oct 1 -> Jul 1 next year)
   - `GET /boxscores/nba/{date}`; store raw at `NBA/raw/boxscores/{date}.json`
     (skip if exists unless `--force`); skip empty `events`.
   - flatten + accumulate.
4. Write masters (one row per grain):
   - `NBA/games_master.csv` -- game_id, season_year, season_type, date, status,
     home/away {id,abbr,name}, home/away total + q1..q4(+OT) scores.
   - `NBA/team_boxscores_master.csv` -- game_id, season_year, date, team_id, team_abbr,
     is_home, opp_id, + 19 team stats.
   - `NBA/player_boxscores_master.csv` -- game_id, season_year, date, team_id, player_id,
     name, position, starter, played, + 19 player stats.
5. Resume-safe: re-runs skip dates already present in raw + dedupe masters on
   (game_id) / (game_id, player_id).
6. Sanity checks logged: ~1230 regular-season games/season + playoffs; player rows
   per game ~16-30; no all-zero stat rows for `played=true`.

Execution: run from **Cloud Shell** (or locally if GCS ADC + bucket env set). Env:
`GCS_BUCKET=concrete-crow-445205-m4-mlb-data`. Est. ~1900 date requests total
(7 seasons x ~270 days); with raw-JSON skip it is fully resumable.

GCS layout:
```
gs://concrete-crow-445205-m4-mlb-data/NBA/
  raw/
    seasons.json
    teams.json
    players_{year}.json
    boxscores/{YYYY-MM-DD}.json
  games_master.csv
  team_boxscores_master.csv
  player_boxscores_master.csv
  last_refresh.json            (nightly sentinel)
```

## Phase 3 -- Nightly refresh + season auto-start

`nba/data/refresh.py::refresh_nightly_gcs(date=None)`:
- default date = yesterday (UTC-aware, season tz America/New_York).
- fetch `/boxscores/nba/{date}`; if `events` empty -> write sentinel `status="skipped"`,
  return (this is the offseason no-op).
- else flatten, append NEW rows to the three masters (dedupe), write
  `NBA/last_refresh.json` ({status, date, games, rows, timestamp}).
- idempotent: re-running the same date adds nothing.

Productionize as a **standalone Cloud Run Job** `nba-refresh-data` (reuse existing
image; keeps NBA off the MLB Flask service hot path). Add `if __name__ == "__main__":`
entrypoint.

Cloud Scheduler `nba-refresh-data`: `0 13 * * *` UTC daily, year-round (8am ET buffer
after west-coast finals). No-ops offseason. OAuth + Run API trigger pattern per
CONTEXT s9 (NOT OIDC). Task timeout 1800s.

Light monitoring: defer adding NBA to the MLB `monitor_ops` (it is MLB-keyed). Instead
the sentinel + a Claude scheduled reminder (~Oct 16) to verify first ingest lands.

## Phase 4 -- Docs

- CONTEXT.md: add short **s19 "NBA (data-only, pre-modeling)"** pointing to
  `nba/README.md` + this GCS layout. Keep the heavy detail in `nba/README.md` to avoid
  bloating the MLB contract doc.
- `docs/solutions/integration-issues/sportsblaze-nba.md` -- API quirks (cache domain,
  no auth, date-by-date boxscores, season year = start year, Utah/SA dup-id check,
  no odds/advanced stats).
- New dated handoff at end of execution session.

## Phase 5 -- UI (beezy.fyi): light multi-sport IA prep + polish

NBA has NO odds/picks yet -> **no NBA pick surfaces.** NBA appears as "coming soon"
only. Two tracks:

A) Multi-sport readiness (cheap now, avoids painful retrofit):
   - Sport context + switcher in nav (`MLB` active, `NBA -- soon` disabled/badge).
   - Sport-scope design tokens: key `SYSTEM_COLOR`/`SYSTEM_PILL` by sport in
     `lib/tokens.ts`; add a `Sport` type.
   - Route shape decision: namespace under `/mlb/...` with redirects from current
     paths, OR a `sport` context without URL change. Design now; implement the minimal
     version (likely context + nav, defer route moves until NBA has content).

B) General polish (sport-independent), via the `impeccable` skill:
   - Audit hero / ticker / nav / picks table / results from recent commits.
   - Accessibility, responsive, dark-mode, motion pass.

Recommend doing A lightly + B as a focused `impeccable` pass. Hold NBA-specific pages
until odds + a model land.

## Execution order

1. Phase 1 scaffolding + tests (local, committed).        [this repo, PR]
2. Phase 2 backfill run (Cloud Shell) -> verify masters.  [Cloud Shell]
3. Phase 3 refresh runner + Cloud Run Job + scheduler.    [code in repo + Cloud Shell deploy]
4. Phase 4 docs.                                          [this repo]
5. Phase 5 UI (separate effort, impeccable skill).        [beezy-vip, PR]

## What is explicitly NOT in scope now
- NBA odds ingestion (user-owned, months out).
- Any NBA model / feature builder / runner / registry entry.
- NBA pick surfaces on the site.
- core/ refactor / mlb_core rename.
- Adding NBA to MLB monitor_ops / performance / discord.
