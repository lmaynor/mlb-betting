# Handoff -- 2026-10-15 -- beezy-vip UI consistency sweep

Branch `fix/ui-consistency-sweep`, one PR. Frontend only (`beezy-vip/`); no backend, job, scheduler or secret touched. Verified: `tsc --noEmit` clean, `npm test` 165 passed / 7 skipped, browser sweep of every public page at 1440px and 375px against production data (local Next dev server fed by a throwaway forwarder to beezy.fyi's own `/api/*` proxies).

## What changed
- **Nav/chrome**: bankroll pill removed (shared bankroll now lives on /ev + Kelly calculator); "The Edge" -> "Prop Research"; Daily Card dropped from the ribbon (link on /picks); nav + ticker pinned in one sticky `.site-chrome`; mobile bottom nav now reaches Picks/Research/Tools.
- **Ticker**: one tick per prop (not per book), shows the actual pick, units = profit/stake (EV $100 stakes no longer read as +10u), 12px, faster, `prefers-reduced-motion` gets a scrollable static strip, label/CTA hidden on phones.
- **Dedupe**: `lib/dedupe.ts` (`collapseBooks`, `propKey`) strips the `_book` suffix from pooled EV `bet_type` and keeps the best-edge row with `bookCount`; used on home, /picks, ticker, Results ledger (toggle, default on), hub MLB tab.
- **/picks**: `LiveEvBoard` deleted (it showed matchup-only rows with no stat when `player_name` was missing, and duplicated the table). Header is one toolbar row.
- **Results**: models and +EV summarised separately (no more blended 36,998 / 37.9% / +5,657u); full-season cumulative curve from `bankroll_curve`; null ROI shows `--` (was +0.0%); per-bet P&L in units of its own stake, aggregates in dollars; "1633/200" -> `1633`.
- **Edge**: sport tabs hidden unless the cockpit feed answers; model board excludes pooled EV rows (header said 13 picks, list said none); empty state links to /ev; tagline paragraph removed (no-slop rule 1).
- **Hubs / /ev copy**: "Sink A", "sweep", "THIN CONS", "shadow" removed; statuses are LIVE / PREVIEW / COMING SOON; NHL (not live) and NBA say "coming soon".
- **Tools**: index is a table of 6; Edge Finder redirects to Kelly (its edge line was already there); NRFI Conditions / Pitcher Matchups already redirected to the slate and are no longer listed; CLV tracker coerces the API's numeric strings (the NaN%) and clamps axes to the 1st-99th percentile; median CLV shown next to the mean.
- **Copy**: model run time is ~11:00 AM CT everywhere (was ET on 4 surfaces); "GATE CLEARED" -> "200+ bets"; OUTS/GAME/F1H descriptions corrected; Daily Card legend matches the real tiers (STRONG/LEAN/WATCH) and lists all MLB systems; pricing drops the fake MOST POPULAR badge and the "All 5 systems" claim; site title/description no longer say "five XGBoost models".
- Books: added Party Casino, SugarHouse, Underdog, Parx, PointsBet, ProphetX to `BOOK_META`.

## Not done / needs a human
- **Models page version numbers**: NRFI row still says v17 / 0.577 OOS AUC, CONTEXT says v18 is live. I did not guess the v18 metric; update `ACTIVE_MODELS` in `app/models/page.tsx` from the real model meta.
- `/picks/mlb/[system]` detail pages and `/models/[slug]` were only touched for the gate label.
- The alerts endpoint (`/api/public/alerts/today`) still returns `player_name: null` on roughly half of rows; the UI that surfaced it is deleted, `/edge`'s alert matching still reads it. Resolving names server-side is a backend follow-up.
- `/ev` still shows 1-book Kalshi rows with +30-70% EV (sorted after credible rows, labelled "Too few books to size"); consider hiding them behind a toggle.
- Hero headline numbers ($14,636 / +12.8%) are unchanged apart from "simulated bankroll" and "each book counted separately" wording.
- Vercel preview should be eyeballed once; verification here was a local dev server, not a Vercel build.
