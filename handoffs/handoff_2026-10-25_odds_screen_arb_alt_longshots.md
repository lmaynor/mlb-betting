# Handoff -- 2026-10-25 -- /ev/odds v1.1: Arbitrage + Alt & Longshots

Branch `claude/odds-arbitrage-altline-aab6b5` (worktree `partycasino-american-book-038e44`). Not deployed.

## What changed
- `ev/odds_board.py`: alternate rungs are kept (were dropped). Each row now has `is_alt`, `modal_point`, `curve_fair_over`, `curve_n`.
  `fit_curve()` = monotone (weighted PAV) logit curve through the player's rungs that have >= 2 two-sided books; extrapolation is capped at
  the anchors' span; one well-covered yardage line falls back to the normal tail of `ev.props.shift_p_over` (<= 1 sd out); count stats with a
  single anchor get no curve. Under `MAX_ROWS` (3000) the order is: main lines, alt rungs with >= 2 two-sided books or actionable (>= 1.5% edge
  vs the curve, or cross-book margin > -2%), then other multi-book alt rungs; the payload is re-sorted nearest game first.
- `beezy-vip/lib/odds-screen.ts`: `computeRowArb`, `buildArbRows`, `kalshiEffectiveDecimal`, `computeLongshotMetrics`, `buildLongshotRows`.
- `beezy-vip/app/ev/odds/odds-client.tsx`: Standard +EV | Arbitrage | Alt & Longshots switcher, per-mode second toolbar row, per-mode summary
  columns, sticky PROP column, cell tint / arb outline, outlay input, Copy hedge. DEVIG method moved into the Reference books popover.
- Tests: `tests/test_odds_board.py` (16), `beezy-vip/tests/odds-screen.test.ts` (27). Full pytest 2459 passed.

## Things the spec asked for that do not exist or differ
- `build_player_curves` is NOT in this repo (no such function anywhere); `fit_curve` is new and its tail model is an unvalidated prior.
- Alt coverage depends on the feed: BettingPros live flags non-main lines as `*_alternate` (usually one-sided overs); the ParlayAPI NFL sweep
  does not request alternate markets and NBA only in the opt-in `full` tier. Kalshi / Novig strike ladders are NOT in the board's event feed
  (only quotes ParlayAPI returns under those book keys). Check the real counts after the first board write (`summary.alt_rows`, `summary.curve_rows`).
- Default LINES filter is All (so Standard looks as before); Main only hides rows >= 1.0 off the modal line even if they are a real book's main line.
- Arbitrage: only Kalshi's fee is modelled. A prop "arb" is usually a stale or mismatched quote; nothing here validates that.
- Fade under needs a real under price: most alt ladders are over-only, so Fade rows appear mainly where an exchange or a two-sided alt quotes the under.

## Deploy order
Backend first (service + rebuild the `daily-projections` job image, or `odds_board_latest.json` never gets `is_alt` / curve fields), then Vercel.
The frontend degrades safely against an old board (`is_alt` undefined = main line, no curve, Standard unchanged).

## Verified
pytest (full), jest (`npm test`: only `legal-and-brand` "manifest and root metadata" fails, from `app/layout.tsx` on main, untouched here), `tsc --noEmit`,
`eslint` on the touched files; the three modes rendered in headless Chrome against a local mock of the endpoint (real `build_board` +
`get_odds_board` on synthetic events). NOT checked against live ParlayAPI / BettingPros payloads.
