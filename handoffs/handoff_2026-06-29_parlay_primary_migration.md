# Handoff -- 2026-06-29 -- ParlayAPI-primary odds + BettingPros historical + odds_history

Big session. Reworked the entire MLB odds pipeline: a historical-odds backfill
(BettingPros), a normalized analytics store (odds_history), and a live-provider
migration from SGO to ParlayAPI. Builds on
`roadmap_2026-06-29_cross_system_odds_and_roi.md` and
`handoff_2026-06-29_bettingpros_api.md`. **The ParlayAPI cutover is LIVE.**

## TL;DR of what's live now
- **Live odds = ParlayAPI** (`ODDS_PRIMARY=parlay`). The snapshot is a per-game
  MERGE: ParlayAPI covered markets (HR/K/OUTS/HITS/TB/ER + game ML) converted to
  SGO shape, plus SGO inning markets (NRFI/1I-3way/F5/F5-ML/F1H) spliced in. The
  9 runners + `sgo.py` extractors + settlement are UNCHANGED (adapter writes the
  same SGO-shaped `Odds/sgo/latest.json`).
- **8 snapshots/day** (was 4), concentrated 18:00-23:00 UTC (lineup->close). SGO
  fetched on 4 runs, carried forward on 4 (SGO stays ~4x/day, free tier).
- **Books = denylist**: every US book qualifies; only offshore excluded.
- **Credits paced evenly** to ~19,500/mo (implicit guard, no header needed).
- **odds_history**: historical from BettingPros, forward from ParlayAPI.

## Architecture

```
ParlayAPI (parlay-api.com)                      SGO (api.sportsgameodds.com)
  get_slate + get_event_props                     fetch_mlb_slate (inning markets)
        |  mlb_core/odds/parlay_adapter.py              |
        |  -> SGO-shaped events (per-event game_pk)     |
        +------------------ merge_events ---------------+   (keyed on eventID==game_pk)
                              |
        mlb/runners/snapshot_odds.py (ODDS_PRIMARY=parlay, 8x/day)
                              |
              Odds/sgo/latest.json  (SGO shape -> runners unchanged)
                              |  (same pull also banks OddsAccum/)
        ParlayAPI OddsAccum --> mlb/analysis/parlayapi_to_history.py --\
        BettingPros backfill --> mlb/analysis/bettingpros_to_parquet.py-+--> Odds/history/ (odds_history Parquet)
```

## Components shipped (all on main via PR #26)

| File | Role |
|---|---|
| `mlb_core/odds/parlay_adapter.py` | ParlayAPI->SGO shape + `merge_events` + `inning_odds_only` |
| `mlb/runners/snapshot_odds.py` | `ODDS_PRIMARY` merge path, 8x/4x cadence, `day_offset`, credit guard, OddsAccum write |
| `mlb_core/odds/sgo.py` | book DENYLIST (`OFFSHORE_BOOKS`); +US books in priority/canonical |
| `mlb/analysis/odds_history.py` | Parquet store (write_partition/read_history/coverage_report) |
| `mlb/analysis/bettingpros_to_parquet.py` | BettingPros CSV -> odds_history (historical) |
| `mlb/analysis/parlayapi_to_history.py` | ParlayAPI OddsAccum -> odds_history (forward) |
| `mlb_core/data/id_resolver.py` | (date,teams)->game_pk, (name,team,date)->MLBAM id |
| `scripts/bettingpros_api.py` + Cloud Run Job `mlb-backfill-bettingpros` | historical scrape |
| `deploy/{deploy_service,add_snapshot_schedulers,setup_backfill_bettingpros}.sh` | wiring |
| tests | `test_parlay_adapter`, `test_parlayapi_to_history`, `test_snapshot_odds`, `test_bettingpros_odds` |

## Key facts / contracts (verified against live payloads)
- **SGO eventID == MLB game_pk** for MLB. Batter runners match
  `int(odds_info["event_id"]) == int(game_pk)`. The adapter sets
  `eventID = str(game_pk)` (resolved per-event from commence_time ET). Merge keys
  on eventID, so a matchup on consecutive days does not collide.
- **ParlayAPI shapes** (payload 2026-06-29): HR market `player_home_runs` uses
  outcome name **"Yes"/"No"** (not Over/Under); outs key is **`player_outs`**
  (not player_pitcher_outs); ~8 US books returned (draftkings, fanduel, betmgm,
  caesars, bet365, betrivers, fanatics, hardrock + offshore bovada/pinnacle/novig).
- **ParlayAPI lacks inning markets** (player_*/h2h only) -> SGO stays the
  NRFI/F5/F1H source.
- **Credit guard**: `OddsAccum/baseball_mlb/_credits/{month}.json`; allowance =
  `PARLAY_CREDIT_CEILING(19500) * day/days_in_month`; over-pace runs skip
  per-event props (game lines only). Header credit tracking is blind
  (`x-requests-remaining` returns null) -- the implicit tally is the source of truth.
- **Settlement unchanged** -- MLB Stats API only, provider-independent.

## Cutover state (DONE) + rollback
- `ODDS_PRIMARY=parlay` set on the `mlb-betting` service.
- 8 snapshot jobs (`mlb-snapshot-1555/1855/2025/2125/2155/2305/0125/0325`).
  Old `mlb-snapshot-{morning,afternoon,evening,pregame}` DELETED.
  **VERIFY no stragglers**: `gcloud scheduler jobs list | grep snapshot` -> exactly 8.
- Shadow-diff before flip showed ParlayAPI coverage >= SGO (K doubled) and inning
  splice exact (NRFI/F5 13==13).
- **Rollback (instant, no redeploy)**:
  `gcloud run services update mlb-betting --region=us-central1 --update-env-vars ODDS_PRIMARY=sgo`
  then `LEGACY=1 PROJECT_ID=... ./deploy/add_snapshot_schedulers.sh`.

## Open / next
- [ ] Confirm `ODDS_PRIMARY=parlay` and exactly 8 snapshot jobs (delete any
      leftover pregame/afternoon).
- [ ] Schedule `parlayapi_to_history` daily (forward odds_history feed, recent window).
- [ ] BettingPros backfill (`mlb-backfill-bettingpros`) still running (~2024-06 as of
      this write); resumes idempotently on the transient api.bettingpros.com timeouts.
- [ ] Run P0.3 `bettingpros_to_parquet` once the backfill completes -> odds_history history.
- [ ] Squash-merge `analysis/nrfi-historical-odds` to unify the NRFI work (`mlb/analysis/`).
- [ ] Watch first live ParlayAPI cycle + paper track for priced-game drop.

## Pointers
- Provider contract: `CONTEXT.md` s8 (+ s4/s5/s9 updated this session).
- Cutover runbook: `RUNBOOKS.md` (odds provider).
- Prior: `handoff_2026-06-29_bettingpros_api.md`, `roadmap_2026-06-29_cross_system_odds_and_roi.md`.
