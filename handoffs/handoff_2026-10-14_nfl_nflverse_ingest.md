# Handoff -- 2026-10-14 -- NFL data pillar: nflverse mirror (built, tested, NOT run)

Request: "get nfl data into our systems" with the nflverse repos (nflreadpy, nflreadr, nflfastR, nflseedR,
nflverse-data) as reference. Only nflreadpy/nflreadr matter for ingest (they define URLs and schemas); nflfastR and
nflseedR are R model/simulation code.

## What shipped (own PR, merge commit; nothing from other sessions' dirty tree)
- `nfl/` package: dataset registry (25 datasets in core/detail/extra groups), thin `requests` client, ingest engine,
  pandas reader, `game_key` crosswalk to `ev/`. Dockerfile `COPY nfl/`. `deploy/setup_nfl_refresh.sh`. CONTEXT s20.
- `tests/test_nfl_ingest.py` (35 tests, offline; real parquet bytes + fake client). Mutation-checked: shrink guard,
  frozen-season skip, 404 classification and sha-unchanged tests each fail when their logic is removed. Full suite 1,216 pass.

## NOT done -- needs owner approval / next session
- **No data has been downloaded and no cloud resource created.** Downloads need the owner's go-ahead. Suggested order:
  1. `python3 -m nfl.data.ingest --mode backfill --probe` (HEAD only) to size it. pbp is the bulk of it.
  2. `deploy/deploy_service.sh` (image must contain `nfl/`), then `deploy/setup_nfl_refresh.sh`.
  3. One-time backfill execution (args override in the script header), then check `NFL/last_refresh.json`.
- **URLs are taken from nflreadpy's source, not verified live.** The first real run is the verification: a 404 on any
  frozen season / single-file dataset is reported as a failure by design. Expect to fix a registry entry if upstream moved.
- `required` columns are only those confirmed in nflreadr dictionaries; teams/officials/pfr_*/contracts/trades/
  depth_charts get structural validation only (no dictionary, or format changed in 2025).
- nflverse update cadence is unverified; the 13:30 UTC schedule is a guess.
- `gameday` vs the ET date at the date boundary (late/international games) is unverified against Kalshi.
- Follow-ups: player-name -> `gsis_id` resolver for the props sweep; second grader for settled NFL props from
  `player_stats` (open item in docs/designs/multisport_ev_sweep.md s7.4); freshness check in `monitor_ops`.
