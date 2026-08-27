# Handoff -- 2026-08-27 -- Frontend: wire SB and EV into the shared system taxonomy

Picked up from `handoffs/handoff_2026-08-26_ops_alert_kalshi_iam_and_refresh_oom.md`
on the user's request to "update the front end to accommodate new systems,
merge and deploy when complete." No specific system was named -- identified
the actual gaps by diffing beezy-vip's per-system hardcoded lists against
`mlb_core/registry.py` and the two systems added since the frontend was
last fully updated (SB and EV, both 2026-08-20).

## TL;DR

Two systems were only partially wired into beezy-vip:

1. **EV** (pooled +EV alert tracking, `system="EV"` in `bets`, deployed
   2026-08-25 -- see `handoffs/handoff_2026-08-20_ev_alert_tracking_and_pitcher_er_discord_bug.md`)
   had **zero** frontend support. Its `bet_type` is the underlying
   market's own convention suffixed with `_{book}`
   (`fast_alert_loop._ev_bet_type`), and `kelly_triggered` is always
   `True` for these rows, so they flow through the public API today --
   roughly 1500 real settled alerts are live in production right now.
   Unhandled, any row that hit `pickLabel()` (Results page, main picks
   table) rendered as the raw internal string, e.g.
   `"K_OVER_7.5_draftkings"`, with a fallback neutral-gray pill.
2. **SB** (stolen bases, LOG_ONLY, deployed 2026-08-22) got a full
   frontend build during its own session, but a handful of secondary
   surfaces were missed -- notably the public **Models methodology
   page**, where a live, deployed model was invisible on the site's own
   "which models exist" listing.

Both fixed, merged to `main` locally (`a3f3f1a` + merge commit
`a5c7961`), pushed. Vercel auto-deploys `beezy-vip` on push to `main` --
no manual deploy step for the frontend (unlike the Cloud Run backend).

## What was actually done

**`beezy-vip/lib/tokens.ts`** (the anchor fix -- everything else builds on
this):
- New `EV` hue (orchid, `#d97ee0`) in `SYSTEM_COLOR`/`SYSTEM_PILL`/
  `SYSTEM_LABEL`.
- `pickLabel()`'s per-system formatting logic extracted into a new
  `formatPick(sys, bt, player, team, away, home)` (pure refactor, zero
  behavior change for existing systems -- confirmed via the pre-existing
  threshold-submarket tests staying green unchanged).
- New `resolveEvUnderlying(bet)` (exported) that mirrors
  `settle_bets._settle_ev`'s prefix classification and
  `_strip_ev_book_suffix` exactly -- strips the real `_{book}` suffix
  using the row's own `book` column, then classifies the remainder back
  to its underlying system (HR/K/OUTS/PITCHER_ER/BATTER_TB/BATTER_HITS/
  BATTER_K/SB/NRFI/GAME/F5). `pickLabel()` now routes EV rows through
  `formatPick` with the RECOVERED system/bet_type, so an EV row reads
  identically to a native pick on the same market instead of leaking
  the raw suffixed string. Falls back to a book-suffix-stripped (but
  otherwise raw) string for a market it doesn't recognize -- matching
  what `_settle_ev` itself would also fail to settle, by design.
- Added `--sys-ev` to `app/globals.css` per this file's own "must match
  `--sys-*` vars in globals.css" contract (not actually CSS-referenced
  anywhere currently -- confirmed via grep, same as every other
  `--sys-*` var -- but kept in sync as documented).

**Consumers of the fix:**
- `components/picks/picks-table.tsx`: new `effectiveSystem(bet)` helper
  (resolves EV rows to their real market) used for the prop-vs-game-line
  row layout decision, so a prop-market EV alert (e.g. an HR alert) gets
  the player-first layout instead of always falling back to game-line.
- `app/results/results-client.tsx`: new "Live Alerts" filter group
  (`['EV']`) so it's selectable/chartable from a chip instead of only
  reachable via "ALL" (its P&L was already silently included in the ALL
  aggregate line either way -- summing `profit` over every settled bet
  doesn't care about system). Also added `SB` to the existing "Batter
  Props" group (parity gap, same class as the Models-page one below).
- `app/tools/clv-tracker/clv-client.tsx`: added `SB` and `EV` to
  `ALL_SYSTEMS` -- this one matters more than a cosmetic filter list,
  since with zero chips toggled this array is the DEFAULT allowlist for
  which systems' points render on the scatter chart at all.
- `app/cheat-sheet/{page,cheat-sheet-client}.tsx`: added `SB` to
  `PLAYER_SYSTEMS`; explicitly **excludes** `system === 'EV'` from both
  today's and yesterday's picks before they ever reach the client or
  `beezyscore()` -- the Daily Card showcases model picks specifically,
  and EV is a cross-market soft-line/Kalshi scanner, not a model
  prediction (deliberate product judgment call, not a bug fix -- see
  "Scoping decisions" below).
- `app/api/og/picks-card/route.tsx` (the auto-tweeted OG image): added
  an `SB` color entry; unified the K/OUTS/BATTER_TB/BATTER_HITS/
  PITCHER_ER/BATTER_K/SB pick-text formatting into one regex+noun-map
  that now ALSO handles the 2+/3+ threshold sub-market shape (e.g.
  `K_2PLUS_2.0` -> `"2+ Ks"`) -- this card had never handled that
  convention for ANY system since it shipped 2026-08-19, not just SB;
  fixed generally while already touching this exact line. Also excludes
  `system === 'EV'` from the top-5-picks selection, same reasoning as
  the cheat sheet.
- `app/models/page.tsx`: added `SB` to `PIPELINE_MODELS` -- this was the
  most visible real bug: a live, deployed, real model with zero mention
  on the site's own public methodology/transparency page.
- `app/tools/slate/slate-client.tsx`: added `SB` to
  `ALL_FILTER_SYSTEMS`. **Deliberately did NOT add `EV`** here -- see
  "Scoping decisions."

**`CONTEXT.md`**: fixed the "Dynamic system route" note (stale since
2026-05-29 -- missing SB entirely) and documented the EV
exclusion-from-browsable-route decision so a future session doesn't
"fix" it back in without the same context.

## Scoping decisions (read before undoing any of these)

- **EV is not a `/picks/mlb/[system]` page.** It isn't in
  `registry.CANONICAL_ORDER` on the backend either -- it's not a model
  with training methodology, walk-forward CV, or its own feature
  contract, it's a tracking pool over alerts the systems above already
  priced. Kept out of `pick-systems.ts`/`filter-bar.tsx`/the Models
  methodology page for the same reason.
- **EV is excluded from the two curated public share surfaces**
  (cheat-sheet Daily Card, OG picks-card image) but INCLUDED on the
  Results page, main picks table, and CLV tracker. The distinction:
  share/marketing surfaces showcase model picks; Results/CLV are the
  transparency/track-record surfaces, and EV's real ROI (+9.2% over
  ~1500 alerts per `handoffs/handoff_2026-08-20_ev_alert_tracking_and_pitcher_er_discord_bug.md`)
  is real track record that shouldn't be hidden there.
- **`slate-client.tsx`'s `ALL_FILTER_SYSTEMS` deliberately excludes
  `EV`**, unlike results-client and clv-tracker. Root cause:
  `get_today_slate()`'s SQL (`mlb/runners/public_api.py`) never selects
  `book`, so `SlatePick` has no book field, so `PickDetail`'s synthetic
  Bet-shaped object hardcodes `book: null` when calling `pickLabel()` --
  `resolveEvUnderlying` can't strip a suffix it can't see. Shipping the
  filter chip anyway would have surfaced `"7.5_draftkings"`-style leaks
  or failed bare-string matches (`"HOME_draftkings" !== "HOME"`) for
  exactly the rows a user clicks that chip to see. Left a NOTE in the
  file; needs a backend change (`book` added to that SELECT + the
  `SlatePick` type) before this can be added for real.
- **Not fixed here, spawned separately** (`task_df3e04aa`):
  `slate-client.tsx`'s `ALL_FILTER_SYSTEMS` and `clv-client.tsx`'s
  `ALL_SYSTEMS` were ALREADY missing `PITCHER_ER` and `BATTER_K` before
  this session touched them -- pre-existing staleness unrelated to
  SB/EV, left alone to keep this diff focused.

## Verification

- `npx tsc --noEmit` -- clean, zero errors, before and after the merge
  commit.
- `npx jest --no-coverage` -- 40 passed / 7 skipped (was 33/7 on `main`
  before this session). Added 7 new tests to `tests/tokens.test.ts`: 6
  for `resolveEvUnderlying`/EV `pickLabel` resolution (K, a threshold
  sub-market, bare HR, bare F5 game-line, an unrecognized market's
  graceful fallback, and a direct `resolveEvUnderlying` assertion) + 1
  SB threshold regression test that had never been added despite SB
  already sharing the exact `plusLabel` code path K/OUTS/BATTER_TB/
  BATTER_HITS are tested against.
- Targeted `eslint` on every touched file: clean except 3
  pre-existing `no-explicit-any` errors in `picks-card/route.tsx`,
  confirmed pre-existing via `git stash` + re-running eslint against the
  unmodified file at the same commit -- not introduced by this session,
  not touched.
- **No live browser verification.** beezy-vip has no local Clerk keys
  (`.env.local` does not exist in this checkout) so `next dev`/
  `next build` can't boot in this environment -- same standing
  limitation noted in `handoffs/handoff_2026-06-16_beezy_platform.md`.
  Also could not browse the live `beezy.fyi` production site from this
  session's environment (blocked by browsing policy) to confirm the
  Vercel auto-deploy landed. **Worth a manual look at `beezy.fyi/results`
  and `beezy.fyi/models`** once Vercel finishes building (typically a
  few minutes after the push) to confirm the EV "Live Alerts" chip and
  the SB pipeline-model card render as expected against real production
  data.

## Addendum -- same day: PITCHER_ER/BATTER_K follow-up resolved

The `task_df3e04aa` follow-up flagged above was started by the user in
this same session shortly after the initial merge. Added `'PITCHER_ER'`
and `'BATTER_K'` to both `slate-client.tsx`'s `ALL_FILTER_SYSTEMS` and
`clv-client.tsx`'s `ALL_SYSTEMS` (the latter is the more consequential
one -- it's the default allowlist for the CLV scatter's per-system
series with no chip toggled, so those two systems' real CLV data was
invisible by default, not just unreachable via a chip). Verified
(`tsc --noEmit` clean, `jest` 40 passed/7 skipped unchanged, `eslint`
clean on both files), merged to `main` locally (commit `e481c7b`, merge
`555831d`), pushed. Same standing caveat applies: not live-verified
(no local Clerk keys, browsing policy blocked the live site).

## Where things stand

`main` has the merge commit (`a5c7961`, merging `a3f3f1a`), pushed
(`2541736..a5c7961`). `gh` still not authenticated in this environment
(same as every prior session's note) -- merged locally, not via PR, same
as the established pattern. Local feature branch
`feat/frontend-sb-ev-system-support-2026-08-27` deleted post-merge.
Vercel should auto-deploy `beezy-vip` from this push with no manual
step; nothing on the Python/Cloud Run backend was touched, so no
service redeploy is needed for this change.
