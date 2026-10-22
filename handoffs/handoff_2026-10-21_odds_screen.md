# Handoff -- 2026-10-21 -- /ev/odds Odds Screen

Branch `feat/odds-screen` (worktree `/Users/lmaynor/mlb-betting-oddsscreen`). Nothing deployed, nothing committed unless noted below.

## What it is
An OddsJam-style odds screen for the +EV Terminal: every book's price per NFL / NHL / NBA player prop line side by side, with fair value and EV from
the reference books the viewer picks. Third tab of the Scanner section (`+EV Terminal | Odds Screen | Prop Research`), Pro only like `/ev`.

Scope decisions (asked and answered 2026-10-21): v1 = player props NFL/NHL/NBA from the banked feeds (no new credits); new route `/ev/odds`;
controls = reference books + de-vig method + book columns. NOT built: game lines (no live non-MLB game-line feed; needs new ingestion + ParlayAPI
credits), MLB (offseason), saved strategy tabs with ROI (no backtest behind them).

## Pieces
- Backend: `ev/odds_board.py` (`build_board`, `write_board`, `read_board`); `ev/daily_projections.py::run_sport` writes
  `EVSweep/{sport}_props/odds_board_latest.json` next to `projections_latest.json`; `mlb/runners/public_api.py::get_odds_board`;
  route `GET /api/public/odds/board` in `main.py`. Tests: `tests/test_odds_board.py` (7). Full pytest: 2439 passed.
- Frontend: `lib/odds-screen.ts` (math), `lib/access-feeds.ts::limitOddsBoard`, `lib/betting-api.ts::apiGetOddsBoard`, proxy `app/api/odds-board/route.ts`,
  `app/ev/odds/{page,odds-client,loading}.tsx`, `.odds-table` CSS in `globals.css`, IA tab in `lib/ia.ts` (`tabActive('/ev')` is now exact so only one tab lights).
  Tests: `tests/odds-screen.test.ts`, `tests/odds-board-access.test.ts`, `tests/ia.test.ts`. `npm test` 552 passed, `tsc --noEmit` clean.

## Deploy order (backend first)
1. Deploy the Cloud Run service (adds the route + the board write inside `daily-projections`). The `daily-projections` job image must be rebuilt too, or no
   `odds_board_latest.json` is ever written. Until the job has run once the endpoint answers `status: unavailable` and the page shows its empty state.
2. Then merge/deploy Vercel. Do not flip PRE_LAUNCH.

## How it was verified
Real `build_board` on synthetic multi-book events served by a local mock of the endpoint, `next dev`, headless Chrome screenshot (the in-app browser refuses
localhost). Not verified against live ParlayAPI / BettingPros payloads: check one real NBA board after deploy (book keys, how many books clear 10% coverage).

## Known limits / next
- Fair value is the MEAN of selected reference books; the terminal's backend consensus is the MEDIAN. Numbers can differ slightly between `/ev` and `/ev/odds`.
- Main lines only (alt markets dropped); a book quoting a shifted line (24.5 vs 25.5) is a separate row, not a column of this one.
- Board rows have no player ids, so no headshots (BettingPros has no crosswalk); teams come from the event, not the player.
- Cap 3000 rows per sport, nearest game first. If NFL Sundays blow past it, add per-game paging rather than raising the cap.
- Sticky header uses `top: 131px` like `.ev-table`; below 1500px the table scrolls horizontally and the header is not sticky.

## Performance pass (same day)
Measured at 9,000 rows (a full NFL Sunday + NBA + NHL, an ESTIMATE of the worst case): rebuild was 98 ms proportional / 1,500 ms power per filter click or keystroke.
Now: each quote is de-vigged once per (row, method) (`WeakMap` in `lib/odds-screen.ts`), leave-self-out is `sum - own`, so a warm rebuild is ~38 ms either way; the
first power-method solve after a data refresh or a method switch is ~580 ms once. Search is `useDeferredValue`. Polling is every 3 min (the board changes every
30) and skips the state update when every sport's `as_of` is unchanged. The `/api/odds-board` proxy uses `next: { revalidate: 60 }` so one upstream fetch is shared
across viewers (the per-viewer tier cut still runs). Payload: ~2.4 MB raw per 3,000 rows (~100 KB gzip on synthetic data; real data compresses less -- NOT measured
on live data). Only 150 rows are in the DOM at a time.
