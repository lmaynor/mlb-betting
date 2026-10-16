# Handoff -- 2026-10-16 -- Picks redesign: player vs game picks, tier gating (flag off)

Branch `worktree-picks-redesign` (worktree `.claude/worktrees/picks-redesign`), frontend only (`beezy-vip/`). NOT committed, NOT deployed.
Verified: `tsc --noEmit` clean, jest 238 passed / 7 skipped, browser check against a fixture API (no production credentials locally).

## What changed
- **Sport ribbon**: ALL SPORTS -> /picks (was home), MLB -> /picks/mlb, NFL/NHL/NBA -> /sports/[sport]. `lib/ia.ts` `sportHref`/`sportActive`.
- **/picks = Top 10 player picks across sports** (`lib/top-picks.ts`): tracked MLB player picks + NFL/NHL/NBA projection rows on one Beezy
  Score scale; non-MLB rows carry UNVALIDATED. Free, ungated, by design.
- **/picks/games** (new): MLB game picks (NRFI, 1I, F3, F5, F1H, F7, GAME) separate from player picks. `components/picks/picks-card.tsx`
  renders both MLB cards; `/picks/mlb` is now player picks only.
- **/picks/history** (new): last 7/14/30 days, each day's top 10 player picks, flat 1u grading, day rows expand.
- **NFL/NHL/NBA hubs** share one template (PROJECTIONS | MARKET PRICES tabs), siloed per sport (`lib/hub-data.ts`). Removed: the NFL
  Kalshi table, the GAMES tab, the MODELS tab and the dead MLB branches of the hub.
- **Systems are a footer on each sport page** (second pass, same PR): MLB player/game systems footers with each system's record
  (`systems-footer.tsx`), the per-market model table as the hub footer. The MLB Systems tab and `/picks/mlb/systems` are gone (308 to
  `/picks/mlb#systems`). The per-system detail page was rebuilt to the no-slop layout.
- **Daily Card revamped**: the shareable Top 10 player picks (shared loader `lib/top-picks-data.ts`), Players | Games toggle (games are
  paid), per-sport chips, UNVALIDATED tag on projections, tweet text from the same list. The public OG image is player picks only.
- **Tier gating, behind a flag** (`lib/access.ts`, `lib/access-bets.ts`, `lib/auth.ts` `getViewer`): see CONTEXT.md s13. Off while
  `PRE_LAUNCH` is true; `ENFORCE_TIERS=1` turns it on for testing, `AUTH_DISABLED_TIER=public` previews a free viewer locally.

## Decisions taken (from the user)
Free = Top 10 + history; paid = full player boards per league, all game picks. Build now, enforcement off until launch.

## Not done / needs a decision
- **Not gated**: /ev (the +EV terminal the Pro plan advertises), /edge model board, /dashboard picks, home page + ticker + slate strip + landing
  teasers, `/api/edge-*`, `/api/ev`. Turning the flag on without gating these leaves today's picks readable elsewhere.
- Legacy `starter` tier (existing Clerk metadata) gets no board access under the current matrix; confirm before launch.
- The pricing copy says Single-Sport includes the `/edge` cockpit for one league; `/edge` is not per-league gated.
- Preview size is 5 rows (`FREE_PREVIEW_ROWS`); game picks have 0 free rows. Tune after launch data.
- Past days are public everywhere (record stays checkable). Gating keys on `game_date >= today` (CT).
- Verified only against fixtures. Look at the Vercel preview once with real data, signed in and signed out.
- MLB has no projection board (see the fit-gap discussion): lambdas are computed but only K/OUTS persist them, and the API does not return them.
