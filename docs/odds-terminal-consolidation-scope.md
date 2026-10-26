# Odds Terminal (consolidated) - scope

Status: built on PR #119 (option a: backend MLB board, terminal removed). Canonical URL is `/ev`; `/ev/odds` redirects. Folds the +EV Terminal (`/ev`, `beezy-vip/app/ev/ev-client.tsx`, 669 lines) into the Odds Screen
(`/ev/odds`, `odds-client.tsx`, ~830 lines). The screen is the better surface: every book side by side, fair value from the
reference books the user picks, three modes (Standard, Arbitrage, Alt & Longshots), Kelly sizing.

## What each one is today

| | +EV Terminal `/ev` | Odds Screen `/ev/odds` |
|---|---|---|
| Sports | MLB + NFL + NHL + NBA | NFL + NHL + NBA only (`SPORTS` in `page.tsx`; `ProjectionSport` has no MLB) |
| Unit | one flagged quote (a book beating consensus) | one prop line, all books in columns |
| Fair value | server-side consensus, fixed | client-side, user picks reference books and devig method |
| Settled history | yes (win/loss rows, public ledger strip) | none, live only |
| Extras | Beezy score, headshots, Compare-books drawer, copy-bet, My books filter, venue filter | arbitrage, alt-line longshots, curve fair value, book logos, best-price tint |
| Feed | `/api/public/ev/feed`, polled 60s | `/api/public/odds/board`, polled 3 min |

## The one real blocker: MLB

The Odds Screen has no MLB board. The terminal's MLB rows are the only ones that grade, and they feed the public ledger
(hero bankroll curve, `/results`, `/analytics` system `EV`). Killing `/ev` must not touch alert generation or grading, only the
page. MLB in the screen needs a board endpoint for MLB props (backend) before the terminal can retire for MLB users.
Until then MLB stays reachable (see Phase 2 options).

## Keep (carry into the Odds Terminal)

- Everything the screen already has: modes, reference-book picker, devig method, min EV, odds floor, My books, bankroll.
- Settled record, but as a page-level metric strip (graded, flat ROI, Kelly bankroll), not a separate tab. Data already comes from `apiGetAnalyticsDeepDive().ev_headline`.
- Player headshots in the PROP cell (the terminal uses `PlayerHeadshot`; the screen has none). Book logos are already shared via `components/ui/book-logo.tsx`.
- Pro gating unchanged (`limitOddsBoard`); the terminal's `limitEvFeed` goes away with the page.

## Leave behind

Beezy score column and legend, Compare-books drawer (the screen's columns are that), copy-bet action, venue filter (reference
and My books cover it), thin-reference toggle, shadow banners, 8-column signal table, separate sport pill group (the screen
has its own), 60s polling.

## Phases

1. **Redirect and link audit.** `/ev` -> 308 to `/ev/odds`; rename the page title to "Odds Terminal". Update `lib/ia.ts`, `lib/sitemap.ts`, nav, bottom nav, footer, hero CTA, `command-center-client.tsx`, `components/picks/market-prices.tsx`, `filter-bar.tsx`, `tools/slate`, `edge-client.tsx`, `tests/ia.test.ts`, `tests/aeo.test.ts`. Hero CTAs collapse to one.
2. **MLB.** Either (a) backend adds an MLB odds board and the screen gains an MLB tab, or (b) the redirect carries `?sport=mlb` to a trimmed terminal view kept only for MLB until (a). (a) is the end state.
3. **Ledger strip + headshots** on the screen.
4. **Delete** `ev-client.tsx`, `limitEvFeed` if unused, terminal-only tests (`ev-terminal-shadow.test.tsx` is partly an ASCII guard over other files; keep that part).

## Decisions needed

1. Final URL: `/ev` (short, existing inbound links) with the screen moved there, or keep `/ev/odds`? Recommend `/ev` as canonical, `/ev/odds` redirecting.
2. MLB: wait for backend board (a), or ship Phase 1 with the terminal kept for MLB (b)?
3. Is the Alt & Longshots / Arbitrage pair still Pro-only?
