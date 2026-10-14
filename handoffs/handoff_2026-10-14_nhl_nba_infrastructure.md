# Handoff -- 2026-10-14 -- NHL / NBA infrastructure (odds banking, bets.sport, alerts, assets)

Three sessions worked in parallel (this one, an NHL data-feed session, a frontend session). This handoff covers the infrastructure
session's pieces. **Nothing below is deployed.** Code is merged or in PR; no Cloud Run Job, scheduler, image or DB change was made.

## PRs
| PR | Contents | State |
|---|---|---|
| #38 (NHL session) | `nhl/` data feed: box score + play-by-play + shots ingest, roster/team crosswalk, `player_names.py` | merged |
| #42 (this session) | Odds banking: per-sport credit ledger, guarded accumulator, NHL/NBA extractor shapes, `deploy/setup_odds_banking.sh` | merged (merge commit 25eb293) |
| #41 (NHL session) | `nhl-refresh-data` / `nhl-backfill` job scripts | their PR |
| this branch (`feat/nhl-nba-infra`) | `bets.sport` + scoping, NHL/NBA alerts + grader, NBA crosswalk, team logos/headshots, docs | open as PR B |
| `feat/mlb-headshots-silo` | MLB headshots -> MLB CDN `silo` (transparent); deletes ~110 MB of rembg PNGs + `process_headshots.py`; adds `refresh_mlb_player_map.py` | open as the follow-up PR |

## What was verified (and how)
- Live: NHL/NBA ParlayAPI payload shapes, `/v1/markets` catalog, credit headers; NHL official API; ESPN rosters/scoreboard/summary;
  logo + headshot CDNs (transparency measured on the pixels, not assumed); MLB `silo` coverage for all 1,272 mapped players.
- End to end locally: guarded accumulator against the real API into a scratch dir (11 credits, ledger exact); NBA crosswalk (629 players,
  30 teams); NHL roster crosswalk (785 players, 32 teams after a retry); logos rendered on the dark background and checked by eye.
- Tests: full suite 1,273 passing on this branch from a clean worktree; jest 90 passing; `tsc --noEmit` clean.
- NOT verified: anything on Cloud Run (egress, secrets, IAM); Vercel build of the frontend changes (the PR's preview does that);
  whether alerts have positive EV (no graded history exists yet).

## Deploying PR B -- order matters
1. **Do not use `deploy/deploy_service.sh` from a shared/dirty tree** (it commits and pushes the working tree). Build from a clean
   checkout of origin/main. New feature jobs use the `mlb-betting-feeds` image, never `mlb-betting:latest` (jobs re-resolve the tag
   at every execution -- see docs/solutions/conventions/cloud-run-job-image-tag-resolution.md).
2. Deploying the service/jobs that carry B changes the live `bets` queries. The `sport` column is added automatically by
   `ensure_sport_column()` on first engine creation and by `BetTracker._init_db`; the column default backfills every existing row
   as `mlb`. Metadata-only on Postgres 11+. Watch the first `mlb-settle` and the first runner after the deploy.
3. After every old revision/job image is gone: `DROP INDEX idx_bets_dedup_v3;` (the v4 index already includes `sport`). Until then an
   identical key in another sport hits v3 and `log_bet` returns -1 with an ERROR log (game-id ranges are disjoint, so it should not
   fire).
4. Then, and only then, `deploy/setup_props_alerts.sh` (sweep, grader, NBA crosswalk jobs). It needs odds banking provisioned first.
5. Odds banking (`deploy/setup_odds_banking.sh`, `CONFIRM_IMAGE_HAS_LEDGER=1`) is independent of B and already merged.

## Decisions the user made / still open
- Made: `sport` column (not namespaced systems); alerts first, models later; hotlinked headshots; defaults of 3,000 credits/sport/month
  and a 2,500 floor for odds banking; MLB headshots move to the silo CDN after the PRs land.
- Open: the shared ParlayAPI pool is 20k/month (15,251 left at 00:23 CDT 2026-10-14) and MLB's own pace guard targets 19,500 alone --
  apply the 100k plan or trim MLB snapshots for the offseason (~50 MLB scheduler jobs are still enabled).
- Open: alerts need CLV vs the closing line (not built) before any verdict; ROI on correlated quotes is noise.
- Open: NBA prop MODEL (the only viable NBA path per handoff_nba_backtest_2026-06-15) needs banked prop history first.

## Things that will bite
- `latest.json` under `OddsAccum/{sport}/` is shared by props and game_lines; read `latest_props.json`.
- NBA ids in the crosswalk are ESPN's. NHL ids are the NHL's. SportsBlaze NBA ids are UUIDs and join to neither.
- A roster crosswalk with a missing team is PARTIAL (`failed_teams`); the NHL API 429s when two jobs share an IP.
- `scripts/build_sport_assets.py --players` refuses to write a partial headshot map; re-run it when rosters change (trades, call-ups).
- Shared worktree: another session reset the working tree mid-session and wiped my untracked frontend files; this work was rebuilt in
  its own `git worktree` off main. Do multi-session work in separate worktrees.
