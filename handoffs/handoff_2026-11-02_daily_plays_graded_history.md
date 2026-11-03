# Handoff -- 2026-11-02 -- daily plays, graded history, hidden game picks, Projections tab (code + tests only, NOT deployed)

Branch `claude/daily-plays-graded-history-5a4d32`. Request: connect the daily plays to the graded history, drop the cap of 5, hide the Game Picks tab, move projections into the odds terminal as
their own tab with lambda visuals, and align "what is a pick" to the markdown. Details of the contract are in CONTEXT.md s24.

## What changed
- Backend: `pick_tier` stamped on every board row (`ev/daily_projections.py pick_tier`), `build_daily_card` and `post_official` uncapped. Tests: `tests/test_model_picks.py`, `tests/test_daily_card.py`.
- Frontend: Daily Card, Graded History, top-picks/board-row gates, IA (Game Picks hidden, Projections tab), `/ev/projections`, `components/picks/lambda-bar.tsx`. Tests: `tests/daily-plays.test.tsx` + 7 updated suites.

## Decisions to reverse if wrong
- The per-player (1) and per-game (2) dedupe was KEPT; only the count cap went. A day with many official plays in one game shows two. Drop the dedupe in `buildDailyCard` / `build_daily_card` for a pure "every official pick" card.
- The old gate in the TS copy (edge 3-10%, score >= 68, confluence, 4+ books) is gone for NFL / NHL / NBA: the site now trusts `pick_tier`. The Python gate is stricter (measured-signal stats only, blend_ev >= 2.5%, sharp-book check), so FEWER plays than the old site badge showed. That is the alignment.
- `/picks/games` still answers (noindex); only the tab and links are gone.
- Free tier: with tiers enforced a free viewer sees a 5-row preview of today's card (per sport, merged); history is public. Decide whether plays beyond the preview should be paid.

## Deploy order (backend first)
1. Rebuild the feeds image; re-provision `daily-projections` (it writes `pick_tier`) and `props-model-picks` (uncapped posts). Until the board carries the stamp the site shows NO official plays (fail closed).
2. Then Vercel. Run `props-model-picks --fix-tracked-stakes` (see the 2026-11-02 remediation handoff) first, or pre-demotion `tracked` rows with stake 1 stay in `/api/public/picks`; the site filters them by notes tier, so history is safe either way.

## Verified
`pytest tests` 3159 passed; `beezy-vip`: `tsc --noEmit` clean, jest 806 passed (1 timing test, `computeMiddles` 9,000 rows < 50 ms, fails under parallel load and passes alone: unrelated).
Browser check against a fixture API (not production data): Daily Card, Graded History, `/ev/projections` (props, game lines, 375px), both redirects, hidden games page. NOT verified: real boards with `pick_tier`,
signed-in / tier-gated views, the live logger, Discord posts after the cap removal.
