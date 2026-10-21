# Handoff -- 2026-10-21 -- alert copy + slip threads, stale recap fix, L7/L30

Worktree `/Users/lmaynor/mlb-betting-alerts`, branch `fix/alerts-threads-recap-stats` (off origin/main 72c195e). NOT committed, NOT deployed.
Full suite: 2432 passed; discord-bot `npm test`: 148 passed.

## What changed
1. **NFL/NHL/NBA alert copy** (`mlb_core/alerts/sport_alerts.py`, `ev/sweep_nfl_props.py`, `ev/sweep_props_alerts.py`): plain-English stat names
   (`STAT_NAMES`), "price needs X% / other books say Y%", matchup + kickoff, one embed per game in the daily thread. Dropped "Sink A", "spr", "OI".
2. **Threads + tagging everywhere**: top-level highlights are now one bot message per alert with a GamblyBot/Playbook thread (max 10/pass);
   free plays got the same; the daily props-signals thread tags inline (`daily_thread.mention_slip_bots`, bot change in `httpServer.js`).
   Content is always copy-clean plain slip lines. Kalshi/Novig rows get copy lines but no tag (not a sportsbook slip).
3. **Stale recap**: `tweet_drafter.slate_is_fresh` (<= 1 day before today, Central) guards `build_recap_prompt`, `build_settlement`, `main`.
4. **L7/L30**: `GET /api/public/stats/window` (`public_api.get_window_stats`) + header lines in every tweet-queue mode.

## Deploy order (nothing is live yet)
1. Bot image (`discord-bot/`) FIRST: an old bot ignores `mention_slip_bots`, so the daily thread posts untagged (harmless, just no tags).
2. Service (`deploy/deploy_service.sh`): adds `/api/public/stats/window`. Until then L7/L30 lines are absent (drafter handles 404/no echo).
3. Jobs: `mlb-tweet-*` run `tweet_drafter.py` and the sweeps run `ev/` + `mlb_core/` -- rebuild the feeds/jobs images those use and confirm
   env `BEEZY_BOT_URL`/`BEEZY_BOT_SECRET` (RUNBOOKS s7a pattern). Free plays job (`mlb-free-plays`) needs the main image.

## Unknowns
- Could not query prod: why Tuesday 2026-10-20 had no fresh settled slate (no bets vs /settle late) is unverified. The guard handles both by skipping.
- Tag behaviour in the daily thread assumes Gambly/Playbook react to a mention inside a thread message; unverified until live.
