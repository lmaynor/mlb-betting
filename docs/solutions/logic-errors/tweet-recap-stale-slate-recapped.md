---
module: tweet_drafter
tags: [tweet-queue, recap, settlement, staleness, discord]
problem_type: logic-error
---

# Recap / settlement drafted an old slate when yesterday had nothing settled

**Symptom (2026-10-21):** #tweet-queue got a recap of MONDAY's results at 5am Wednesday.

**Cause:** `build_recap_prompt` and `build_settlement` took `max(game_date)` over whatever `/api/public/picks/recent` returned. That fixed
the older "game_date == today never matches" bug (see tweet-recap-date-filter-never-matches.md) but left no age check, so when the newest
settled slate was not yesterday (no bets that day, or `/settle` not finished at 09:00 UTC) the job silently recapped the newest old one.

**Fix:** `slate_is_fresh(day)` -- the slate must be at most `RECAP_MAX_AGE_DAYS` (1) before today in America/Chicago. A stale slate prints
`skip_reason()` and posts nothing (exit 0 is correct here: no fresh slate is not a failure). The check lives in both builders AND in
`main()` so a future caller cannot bypass it. Tests inject `central_today`.

**Gotcha:** the check is an upper bound only (a game_date in the future relative to the clock still counts as fresh).
