# Handoff 2026-10-17 -- live lines, rosters, games list and Yesterday archive for the NFL / NHL / NBA picks boards

Branch `feat/live-lines` (worktree `/Users/lmaynor/mlb-betting-livelines`), cut from origin/main `5c95df4`. Python only; nothing under `beezy-vip/`
touched; **nothing deployed, no gcloud run/scheduler command executed, nothing written to the production bucket or DB** (every probe read GCS /
public APIs only and wrote under the session scratchpad). Architecture notes: CONTEXT.md s13 "Live lines, rosters, games, archive".

## Why the boards were empty (all verified 2026-10-17)

1. NFL / NHL / NBA had no usable prices: ParlayAPI is banked twice a day (NHL / NBA, and those schedulers are paused) or by the sweep (NFL, scheduled
   only that morning). The NHL banked snapshot was 7.5 h old and every event in it had already started; the NBA one the same.
2. Nothing recomputed `projections_latest.json` on a schedule (the script the docstring named did not exist). The web thread only recomputes after
   90 min stale and gets no CPU after responding.
3. A player's team came from his last game-log row: retired Kopitar (LAK) was in the served NHL board (blocked shots, points, shots on goal); 17
   offseason NBA movers were dropped from the lines as `ambiguous_team`.
4. Row order was priced-first across all days and the proxy sends no `limit` (route default 300, cap 700): any cut removed near-day rows.

## What was built

| Piece | Files |
|---|---|
| Live BettingPros feed -> ParlayAPI event shape | `ev/bettingpros_events.py`, `mlb/runners/bettingpros_live_props.py` (reuses `ev/bettingpros_live.py` -- an offers -> Quote adapter from PR #55 that the brief did not know existed -- and `mlb_core/odds/bettingpros_props.py`) |
| Line union + roster + games + order + archive | `ev/daily_projections.py` (`union_events`, `resolve_team`, `order_and_cut`, `build_games`, `merge_archive` / `write_archive` / `get_archive`) |
| Active rosters | `ev/rosters.py` (cache `Rosters/{sport}.json`) |
| API params | `mlb/runners/public_api.py::get_sport_projections` (`date`, `day`, `games`, `lines_source`, `lines_stale`, `archive`), `main.py` route |
| Jobs | `deploy/setup_bettingpros_live.sh`, `deploy/setup_daily_projections_job.sh` (NOT executed) |
| Monitoring | `mlb/runners/monitor_ops.py::_check_board_feeds` (board > 3 h old, BP file > 6 h old in season; missing file = silent) |
| Tests | `tests/test_bettingpros_events.py`, `test_rosters.py`, `test_daily_projections_live.py`, `test_deploy_live_lines.py` (+ edits to `test_daily_projections.py`, `test_monitor_ops.py`) |
| Docs | CONTEXT.md (s2, s3, s9, s13, s21, API table), four `docs/solutions/` notes |

## Real probe numbers (2026-10-17 ~01:00-01:10 CT, read-only; local result files, bucket untouched)

BettingPros live crawl (single-threaded, 0.8 s +-40% between pages, 1.0 s between markets):

| sport | window | games listed / priced | requests | cycle | quotes kept |
|---|---|---|---|---|---|
| NHL | today + 3 | 31 / 14 | 121 | 137-145 s | 6,339 |
| NFL | today + 7 | 14 / 13 | 75 | 61-98 s | 7,028 (incl. anytime TD) |
| NBA | today + 7 | 36 / 13 | 99 | 99-102 s | 825 |

Priced rows through `build_line_rows` with the real game logs (rows = player x stat at its main line):

| sport | ParlayAPI only (today's path) | BettingPros only | union, no roster | union + roster | roster effect |
|---|---|---|---|---|---|
| NFL | 366 (11 games) | 797 | 829 | **835** | +6 recovered (`ambiguous_team` 8 -> 2) |
| NHL | 118 (14 games; snapshot 456 min old, outside the 240 min live window so the union uses BP alone) | 1,084 | 1,084 | **1,084** | 0 movers quoted |
| NBA | **0** (all 12 banked events already started) | 340 | 340 | **404** | +64 recovered (17 players), `ambiguous_team` 64 -> 0 |

What the API served at probe time: NFL 39 rows / 0 priced, NHL 576 / 0, NBA 0 rows. Full `run_sport` (write=False) after the change: NFL 911 rows
(835 priced) 14 games; NHL 1,317 (1,084) 20 games; NBA 1,182 (404) 28 games; nothing cut at MAX_ROWS 2400; payloads 0.94 / 1.36 / 1.21 MB.
Agreement BP vs ParlayAPI on NFL props both priced: 278 of 330 same consensus line, median |dP(over)| 0.2 pp, p90 1.0 pp, median 6 books each.
Kopitar: present in the served board; reproduced without the roster (3 rows at the old quota of 6) and absent with it. Roster pulls: NFL 1,685 active
(32 teams), NHL 785 (32; one 429 on TBL recovered by the client), NBA 630 (30, from the fresh `nba-espn-players` crosswalk). Job simulation (all three
sports, real masters, injected local BP files): ~90 s total, peak RSS 788 MB.

## Payload / API contract

`GET /api/public/projections?sport=&market=&limit=&day=&date=` (`limit` 1-1200, default 300). New / changed fields:

- `games`: `[{game_key, away, home, start (ISO UTC), n_rows, n_priced}]`, every upcoming game of the window (schedule plus games only the lines know).
- `lines_source`: `['parlayapi','bettingpros']` -- the feeds actually used; `lines_age_minutes` / `lines_stale` describe the NEWEST used feed
  (`lines_stale` = priced rows exist and that age > 240 min). A feed that is down or too old appears only in `summary.sources` with its reason.
- `archive` (false live / true for `date=`), `date` / `day` echoed, `horizon_hours`. `rows` order = nearest Central game day first, priced before
  rotation within a day, display order after that. `lines_stale` and `lines_source` are now forwarded by the route (the route dropped `lines_stale`).
- `summary` gains `sources`, `merge`, `roster`, `rows_cut`, `games`, `roster_recovered`, `roster_team_changed`, `rotation_off_roster`, `rotation_team_changed`.
- `date=YYYY-MM-DD` (Central game day): the archived board, same shape; `{status:'unavailable', rows:[], archive:true}` when none. `date=<today>` works
  and includes games already under way. `day=` filters the live board. Bad format / impossible date -> 400.
- Horizons: NFL 168 h, NHL 72 h (was 48), NBA 168 h (was 48). `ROTATION_PER_TEAM` per sport 5 / 4 / 4, priced players count toward the quota.
  `MAX_ROWS` 2400 (was 1200). **The proxy must pass `day=` or `limit=`: the default 300 plus the new order means page 1 is today only.**

## Deploy order (nothing below has been run)

Pre: merge the PR. Everything runs from a CLEAN checkout of origin/main (`build_all_images.sh` refuses a dirty tree or HEAD != origin/main).

1. Images. Read `deploy/build_all_images.sh`'s warning first: a Cloud Run JOB resolves its image tag at every execution, so retagging
   `mlb-betting:latest` changes every scheduled MLB job's next run. The new jobs run from the feeds image, so:
   `PROJECT_ID=concrete-crow-445205-m4 SKIP_PROD=1 bash deploy/build_all_images.sh` (feeds / ev / bp-crawl only; note the printed digest).
2. Web service (the `date` / `day` / `games` params and the 5-min process cache are service code): the usual `deploy/deploy_service.sh` from a clean
   main, or `gcloud run services update mlb-betting --image=gcr.io/concrete-crow-445205-m4/mlb-betting-feeds@<digest from step 1>` (same Dockerfile;
   avoids retagging production :latest -- this variant is untested). The service and the payload are mutually tolerant, so steps 2-4 can go in any order.
3. `PROJECT_ID=concrete-crow-445205-m4 CONFIRM_IMAGE_HAS_LIVE_LINES=1 bash deploy/setup_bettingpros_live.sh` (3 jobs, 5 schedulers; ~110 BettingPros
   requests per sport per hour; no ParlayAPI credits).
4. `PROJECT_ID=concrete-crow-445205-m4 CONFIRM_IMAGE_HAS_LIVE_LINES=1 bash deploy/setup_daily_projections_job.sh`.
5. First data now instead of waiting for the next hour:
   `for s in nhl nfl nba; do gcloud run jobs execute bp-live-$s --region=us-central1 --wait; done` then
   `gcloud run jobs execute daily-projections --region=us-central1 --wait`.
6. Prove the REAL scheduler path (manual `jobs execute` uses your credentials): `gcloud scheduler jobs run daily-projections-30m --location=us-central1`,
   `gcloud scheduler jobs run bp-live-nhl-hourly --location=us-central1`, then `gcloud scheduler jobs describe <name> --format='yaml(status,lastAttemptTime)'`
   (`status.code` must be empty).
7. Check: `curl -H "X-API-Key: $K" "$API/api/public/projections?sport=nba&limit=5" | jq '{status,lines_source,lines_age_minutes,count,games:(.games|length)}'`
   and `...&date=<yesterday CT>` (will be `unavailable` until the job has run through a full game day: **there is no first-run backfill of the archive**;
   it can only record from the first compute on).
8. Rollback: `PAUSE=1 ... setup_daily_projections_job.sh` and `PAUSE=1 ... setup_bettingpros_live.sh` stop the schedulers; the service's own
   background recompute (90 min stale gate) resumes as before. Deleting `Odds/bettingpros_live/*/latest.json` drops the BP feed with no code change.

## Decisions worth a second look

- BettingPros books kept: `ev.bettingpros_live.LIVE_BOOKS` (draftkings, fanduel, betmgm, caesars, bet365, fanatics, betrivers, espnbet=theScore,
  hardrock, novig, prophetx). Fliff, PartyCasino (a BetMGM skin) and SugarHouse (a BetRivers clone) are out so no book is counted twice.
- Union rule: only feeds inside the 240-min live window count unless none is (then all inside 1200, flagged stale), so a 10 h-old ParlayAPI snapshot never dilutes a fresh
  BettingPros consensus; per (book, stat) the strictly fresher feed wins, ties and unknown ages go to ParlayAPI.
- `lines_age_minutes` is the crawl's fetch time, not BettingPros' own quote stamps (`newest_quote_update` is in `latest.json` for a staleness audit).
- Roster as team source applies only when the roster team is one of the two clubs of the quoted game; a quoted player on no roster, or on a partial pull,
  keeps the old last-game-log behaviour. The NHL roster API omits injured-reserve players: they leave the rotation rows (wanted), never the lines.
- A priced archive row is never replaced by a no-line rotation row (a book pulling a prop must not erase the posted price).
- No dated raw copy of the BettingPros payload is kept (hourly x 3 sports x ~0.7 MB; not "cheap"). Say so if you want it for line-history / CLV work.
- Added unasked: `day=` filter, `monitor_ops` board-freshness check, `PROJ_CACHE_TTL_SEC` 30 -> 5 min (a 30-min process cache doubled the lag behind the job).

## Risks / what I could NOT verify

- **Never ran on Cloud Run.** Egress from GCP to `api.bettingpros.com` (the existing `bp-backfill-*` jobs do reach it), `api-web.nhle.com` (rate-limits per
  IP; one 429 locally) and ESPN from the `daily-projections` job is unverified; the roster pull falls back to the cache then to the old behaviour, and
  `fetch_nhl` retries failed teams once, but a throttled IP on the first run leaves no roster until the next 6 h refresh.
- **No write to the real bucket was exercised.** Archive, `latest.json` and roster-cache writes were tested on a local directory and an in-memory store;
  GCS permissions of `mlb-betting-sa` on `EVSweep/`, `Rosters/`, `Odds/bettingpros_live/` are assumed (it already writes `EVSweep/` and `Odds/`).
- **BettingPros terms of use / the shared public web key** (`BP_API_KEY`, embedded in their frontend, overridable by env) were not reviewed; this is the same
  free API the existing backfill uses, now hit ~300 times an hour (121 + 75 + 99 requests). Not a ParlayAPI-credit risk, but an availability and terms risk. The crawl fails closed
  (keeps the last file) on an outage and `monitor_ops` pages after 6 h.
- Early-season BettingPros books are thin: 263 of 404 NBA priced rows have ONE two-sided book (so no confluence, which needs 3). NHL goalie saves had zero
  BettingPros offers at probe time (goalies unconfirmed). Coverage should thicken toward game day; I did not re-measure.
- Only `UTH` / `NOR` needed a BettingPros team-code fix today; a new code (a relocated club, a preseason squad) drops its event, counted in
  `events_unknown_team` and logged as a WARNING per event -- watch the first runs.
- NFL anytime-TD points are normalised to 0.5 (BettingPros stamps 1). I did not compare BettingPros' anytime-TD prices against ParlayAPI's.
- Historical BettingPros `touchdown-scored` partitions are corrupt (see `docs/solutions/integration-issues/bettingpros-anytime-td-per-game-offer-shape.md`);
  NOT fixed, the `bp-backfill-nfl` job and its output are untouched. Recoverable from the `side` column; do not use them until that is done.
- The model is unchanged and still `validated: false`: more lines make the board fuller, not the picks better. NFL / NBA have no edge evidence (CONTEXT s20/s21).
- Preseason NBA game logs (the 2026-27 season has no rows yet) come from 2025-26: rosters fix the TEAM, not rotations, minutes or injuries.
- `ROTATION_PER_TEAM` 6 -> 5/4/4 and the priced-count quota mean fewer no-line rows per game than before even without a roster: intended, but visible.
- Tests run on the Python 3.14 venv; production is 3.11 (all new code is `from __future__ import annotations` clean; not run on 3.11).
- The frontend still sends no `limit`/`day`; until it does, the first 300 rows are the nearest day only (by design, see the contract above).
