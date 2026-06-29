# Handoff -- 2026-06-29 -- BettingPros historical odds via the public JSON API

Feeds the BettingPros track in `roadmap_2026-06-29_cross_system_odds_and_roi.md`
(the `odds_history` program). This is the **raw historical-odds store** that
P0.3 (`bettingpros_to_parquet.py`) will normalize into the `odds_history`
Parquet schema. Player-prop historical depth was called out there as "the hard
part / the bet" -- this delivers it.

## Status

- SHIPPED + on `main`: the API client, the multi-market backfill, and the
  `mlb-backfill-bettingpros` Cloud Run Job.
- RUNNING: the Job is executing the full backfill (2024-04-01 -> 2026-06-29,
  all 29 markets) writing to `gs://<bucket>/Odds/bettingpros/`.
- The Selenium page-scrape path is RETIRED for backfill (see below).

## The problem we solved

`scripts/bettingpros_hr_backfill.py` (Selenium) could only ever see ~5 players:
the player-props page **virtualizes** its list (only a handful of rows mounted
in the DOM at once), and synthetic scroll events did not drive the Vue virtual
scroller. Dead end.

Fix: skip the rendered page entirely and hit the **public JSON API**
(`api.bettingpros.com/v3`, `x-api-key` is the embedded web key). `events` ->
`offers`, paginated, returns every player/game. 314 HR players/day vs. 5.

## Architecture (single source of truth, no drift)

```
mlb_core/odds/bettingpros.py     SHARED CORE: API client + per-shape parsers +
                                 readers. No file I/O. Used by both paths below.
  |                                  |
  v                                  v
scripts/bettingpros_api.py       mlb/runners/backfill_bettingpros.py
  local CLI -> data/bettingpros/   Cloud Run Job -> GCS partitioned:
  {market}_odds.csv                Odds/bettingpros/{market}/{YYYY-MM-DD}.csv
```

- **Cloud Run Job** `mlb-backfill-bettingpros` (provisioned by
  `deploy/setup_backfill_bettingpros.sh`): 1Gi/1CPU, `task-timeout=43200s`
  (12h), `max-retries=3`, GCS-only (no Cloud SQL). Config via env:
  `BP_START/BP_END/BP_MARKETS/BP_PREFIX/BP_DELAY`.
- **Partitioned by date** so resume is cheap: the runner lists existing keys per
  market and skips done dates -> **idempotent on timeout/retry/re-execute**. No
  giant-file rewrites.
- Why a Job, not Cloud Shell: Cloud Shell recycles idle VMs (~20min) and kills
  `nohup` + wipes `/tmp` -- a 12h unattended backfill is impossible there. The
  local run got 24 dates before the session dropped.

## 29 markets, 6 offer shapes

Registry: `mlb_core.odds.bettingpros.MARKETS` (id -> name, kind). Groups:
`player` (15), `lines` (3), `innings` (11), `all` (29).

| kind | markets | row builder |
|---|---|---|
| player_ou | home_runs(299), hits, runs, rbi, hits_runs_rbis, singles, doubles, triples, total_bases, steals, strikeouts, earned_runs, hits_allowed, walks_allowed, outs_recorded | 1 row/player, Over/Under |
| moneyline | moneyline(122), 1st/5th_inning_moneyline, first_to_score | 1 row/game, Away/Home odds |
| spread | run_line(176), 1st/5th_inning_spread | 1 row/game, Away/Home odds + ±line |
| total | total_runs(175), 1st/2nd/5th_inning_runs | 1 row/game, Over/Under |
| team_total | team_total_runs(277), fifth_inning_team_runs | 1 row/team (2/game), Over/Under |
| yesno | run_in_1st_inning(369) | 1 row/game, Yes/No |

These map to the roadmap's per-system historical-odds sources (HR, K, OUTS,
BATTER_HITS, BATTER_TB, PITCHER_ER props; F5/GAME/F1H/NRFI lines).

## Data caveats (carry into P0.3 normalization)

1. **`Consensus` is the canonical historical odds column** -- BettingPros'
   market-wide consensus, fully populated, stable across seasons. `Open` and
   `Best Odds` (best:true line) also captured. Do NOT roll your own average;
   Consensus already is it.
2. **Major books (DraftKings/FanDuel/BetMGM/Caesars/bet365) populate only on
   SETTLED dates** -- a same-day slate shows only geo-unrestricted books
   (theScore/Fliff/Hard Rock). Confirmed: 2024-05-01 had full DK lines;
   2026-06-28 (today) had none. Historical backtest data is fine.
3. **Player `Team` reflects CURRENT team, not historical** (e.g. Colin Rea shows
   CHC on a 2024 row). Join to game data on **player name + date**, never Team.
4. **Empty markets**: some markets (hits_allowed, walks_allowed, outs_recorded,
   hits_runs_rbis, first_to_score, some inning runs) weren't offered in early
   2024 -- added in 2025+. They share shapes with populated markets; they just
   fill on later dates. Empty (market,date) writes no file and is re-attempted
   on every resume (harmless no-op).
5. API hard-caps `offers?limit` at **10** -- the client paginates.
6. Offshore/DFS books (Pinnacle, ProphetX, Novig, PrizePicks, Kalshi, ...) are
   intentionally dropped to keep the schema stable. ESPNBet is absent
   (rebranded to theScore Bet, book id 33).

## Reader API (consume the store)

`mlb_core.odds.bettingpros` (reads through `mlb_core.storage`, GCS or local):
- `read_market(market, start=None, end=None)` -> concatenated DataFrame.
  `market` accepts an id (299) or name ("home_runs").
- `list_market_dates(market)` -> sorted dates present.
- `coverage_report(markets=None)` -> per-market `{n_dates, first, last,
  per_season}`. Use this for the roadmap's coverage-gating before any backtest
  ("never let a backtest silently run on thin data").

P0.3's `bettingpros_to_parquet.py` should call `read_market` per market, map
player/team -> game_pk (norm_team + MLB schedule), and write the normalized
`odds_history` rows.

## Run / monitor

```
PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_backfill_bettingpros.sh
gcloud run jobs execute mlb-backfill-bettingpros --region=us-central1
# status / logs
gcloud run jobs executions list --job=mlb-backfill-bettingpros --region=us-central1 --limit=1
# output
BUCKET=$(gcloud secrets versions access latest --secret=mlb-gcs-bucket)
gsutil ls "gs://$BUCKET/Odds/bettingpros/"
```

Local CLI (Cloud Shell; egress to bettingpros is blocked on the Mac):
`python3 scripts/bettingpros_api.py {markets|daily DATE|backfill} --markets all`.

## Branches / git hygiene

- `feat/bp-job` -> merged to `main`: the Job + shared core + CLI refactor.
- `feat/bp-loader`: reader/coverage API + this handoff (this commit).
- `feat/bettingpros-hr-api`, `feat/bettingpros-markets`: earlier, merged.
- `feat/bettingpros-cloud-run-job`: ABANDONED (accidentally based on the NRFI
  analysis branch; would have polluted `main`). Ignore/delete.
- `analysis/nrfi-historical-odds`: cleaned (commit 9593c23) -- untracked ~16MB
  of bulk/generated data (data/bettingpros, nrfi_preds*, yrfi_master_2026, and
  11 junk files committed under a literal Windows `C:\Users\...\Baseball_Data`
  path) + gitignored. It still PREDATES the bettingpros work on `main`, so when
  PR'ing the NRFI work, **squash-merge** to keep removed data out of history.

## Open / next

- [ ] Verify the Job finishes clean (`DONE. total_rows=... errors=0`); spot-check
      `coverage_report()` per market for thin seasons.
- [ ] P0.3: `mlb/analysis/bettingpros_to_parquet.py` -- normalize `read_market`
      output into the `odds_history` Parquet schema (roadmap s3), with a
      coverage report per market.
- [ ] Decide odds_history store (Parquet-in-GCS recommended) -- roadmap open Q.
- [ ] `data/bettingpros/` is gitignored on the analysis branch only; add the same
      ignore to `main` if anyone runs the local CLI there.
- [ ] Retire `scripts/bettingpros_hr_backfill.py` (Selenium) once the Job output
      is validated -- superseded by the API path.
