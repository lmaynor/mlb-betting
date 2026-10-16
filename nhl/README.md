# NHL data package

Data-collection-first hockey expansion, mirroring `nba/`: additive, reuses `mlb_core.storage`, all data
under the `NHL/` prefix of the shared bucket. **There is no NHL model, odds feed, or betting runner here** --
this is box-score / play-by-play / shots ingest plus the team and player name crosswalks that later odds joins need.

## Source: the official NHL web API

`https://api-web.nhle.com/v1`, no auth. Undocumented but stable and widely used; it rate-limits per IP, so the
client throttles (`NHL_REQUEST_DELAY`, 0.25s), retries 5xx/network errors with backoff, and honours `Retry-After`
on 429. SportsBlaze (the NBA source) has **no** NHL box scores.

| Endpoint | Used for |
|---|---|
| `schedule/{date}` | one WEEK of games from that date + `nextStartDate` (the season walk is ~1 call/week) |
| `gamecenter/{id}/boxscore` | per-player box stats, goalie splits, team SOG, final state |
| `gamecenter/{id}/play-by-play` | every event: shots with x/y + strength, goals, `rosterSpots` (full names) |
| `roster/{team}/current`, `standings/now` | the players / teams crosswalks |

## Layout

```
nhl/
  config.py          shared vocabulary: API_BASE, FINAL_STATES, GAME_TYPES, *_MASTER keys, MASTER_KEYS, TEAMS table
  data/
    settings.py      re-exports the shared names from config + the ingest's own constants (WALK_*, SHOT_EVENTS, ...)
    nhl_api.py       NhlClient for the ingest: schedule week / boxscore / pbp
    client.py        NhlClient for the crosswalks: roster / standings / schedule / landing
    flatten.py       box + pbp -> game / team / skater / goalie / shot rows
    ingest.py        schedule walk, raw cache, fetch+flatten a batch, write masters
    masters.py       upsert(key, rows) -- merge + dedupe + write; an unreadable master RAISES
    refresh.py       nightly: re-fetch the trailing 2 days, then the crosswalk refresh
    backfill.py      resumable multi-season pull
    players.py       NHL/players_master.csv   (player_id <-> full name <-> headshot), uses mlb_core.data.player_names
    teams.py         NHL/teams_master.csv     (abbrev <-> team id <-> Kalshi code <-> logos)
```

## GCS (`NHL/`)

```
raw/boxscores/{game_id}.json         FINAL games only (a live payload would freeze partial stats)
raw/pbp/{game_id}.json.gz            ~135KB/game gzipped
games_master.csv                     one row / game        key game_id
team_boxscores_master.csv            one row / (game, team)
skater_boxscores_master.csv          one row / (game, skater)
goalie_boxscores_master.csv          one row / (game, goalie)
shots/shots_{season}.csv             one row / shot attempt (sog/miss/block/goal), per season so the nightly
                                     rewrite touches ~85k rows, not every season
players_master.csv  teams_master.csv last_refresh.json
```

## Gotchas (all verified on live data)

- **Box-score names are abbreviated** ("A. Johansson" -- DET dressed two). Join on the integer `player_id`; full
  names come from pbp `rosterSpots`.
- **Shootout scores:** the official score credits the shootout winner +1 goal. `*_goals_ex_so` and
  `*_p1/p2/p3/ot` are recomputed from goal events (None, not 0, when there is no pbp). Kalshi counts the shootout
  goal; most books do not.
- **`blocked-shot` events are owned by the SHOOTING team**; the blocker is `blocking_player_id`.
- **`situationCode`** = away goalie, away skaters, home skaters, home goalie (a 0 goalie digit = empty net).
  `skaters_for/against/empty_net` are decoded relative to the shooting team.
- **Shot geometry:** `dist_ft`/`angle_deg` point at the goal the shooting team ATTACKS (the home team defends
  `homeTeamDefendingSide`); behind-the-net shots are mirrored in front (approximation).
- **A partial roster crosswalk is not a success.** With one team's roster missing, a namesake on another team can
  resolve "exactly" to the wrong player. `refresh` therefore reports `partial` (and exits 1) when
  `failed_teams` is non-empty, and runs the crosswalk AFTER game ingest so a throttle can never block box scores.
- The nightly refresh re-fetches the trailing 2 days on purpose: scorekeepers correct stats a day or two later and the
  upsert keeps the latest copy.

## Running

```bash
# Nightly refresh (what a Cloud Run Job would run):
GCS_BUCKET=concrete-crow-445205-m4-mlb-data python3 -m nhl.data.refresh

# Backfill (2 API calls/game, ~3,000 calls/season):
GCS_BUCKET=concrete-crow-445205-m4-mlb-data python3 -m nhl.data.backfill --seasons 2019-2026

# Local smoke test (no bucket -> writes under MLB_BASE_DATA):
MLB_BASE_DATA=/tmp/nhl python3 -m nhl.data.backfill --seasons 2026 --limit 20
```

**Run one NHL job at a time** (the API throttles per IP; a backfill and a roster pull overlapping caused 429s).

## Deploying (Cloud Run Jobs)

`deploy/setup_nhl_jobs.sh` provisions `nhl-refresh-data` (nightly, 12:30 UTC) and `nhl-backfill`. It is
idempotent and has modes so the backfill and the nightly refresh never overlap (the API throttles per IP):

```bash
PROJECT_ID=concrete-crow-445205-m4 MODE=jobs      ./deploy/setup_nhl_jobs.sh   # create/update both Jobs
gcloud run jobs execute nhl-backfill --region=us-central1 --project=concrete-crow-445205-m4 --async
PROJECT_ID=concrete-crow-445205-m4 MODE=scheduler ./deploy/setup_nhl_jobs.sh   # AFTER the backfill finished
PROJECT_ID=concrete-crow-445205-m4 MODE=pause     ./deploy/setup_nhl_jobs.sh   # pause the nightly (before a re-backfill)
```

The jobs run a SEPARATE image, `gcr.io/<project>/mlb-betting-feeds`. A Cloud Run Job stores the `:latest` tag and
resolves it to a digest at **each execution** (verified: `mlb-betting`'s existing jobs list `:latest` but their
executions run `@sha256:...`), so pushing a new `mlb-betting:latest` silently changes what every existing scheduled
job runs next. Build the feeds image from a CLEAN checkout of `main` (`deploy/deploy_service.sh` also commits and
pushes `CONTEXT.md` and the current branch, so never run it from a tree with other people's uncommitted work):

```bash
gcloud builds submit --config=cloudbuild.yaml \
  --substitutions=_IMAGE=gcr.io/concrete-crow-445205-m4/mlb-betting-feeds --project=concrete-crow-445205-m4 .
```

NHL jobs spend no ParlayAPI credits and are deliberately NOT named `odds-bank-*`.

## xG and the saves / goals / assists / points research models (2026-10-16, research only)

`nhl/xg/` fits expected goals on OUR shots (no MoneyPuck / NST / Evolving Hockey: their terms bar commercial or automated use,
see docs/solutions/conventions/nhl-data-source-licensing.md). `nhl/props/{xgfeat,countmodel,bp_eval,eval_xg}.py` turn that into
leakage-safe rolling features, a generic walk-forward count model, and a scorer against the historical BettingPros lines.
NOTHING here is wired to a job, a scheduler, the `bets` table or Discord; output is local files only.

```bash
# 1) walk-forward xG for every shot + per skater / goalie / team game tables (needs local copies of NHL/shots/*.csv)
PYTHONPATH=. python -m nhl.xg.build --data-dir <dir with NHL/> --out-dir <dir>
PYTHONPATH=. python -m nhl.xg.evaluate --data-dir <dir with NHL/>            # temporal holdout report
# 2) walk-forward models + BettingPros scoring (needs <data-dir>/bp/{saves,goals,points,assists}/*.parquet)
PYTHONPATH=. python -m nhl.props.eval_xg --data-dir <dir> --xg-dir <xg out> --out-dir <dir> --json report.json
```

Pull the inputs with `gcloud storage cp` (NOT `gsutil -m`, see docs/solutions/runtime-errors/gsutil-m-silent-hang-python-3-14.md).
Verdicts and numbers: docs/analysis/nhl_xg_saves_goals_validation_2026-10-16.md.

## Not done yet

No `monitor_ops` wiring (the sentinel `NHL/last_refresh.json` is the health signal for now: `status` is `ok`,
`skipped` on no-game days, or `partial` -- which also exits 1 so the job shows failed).
