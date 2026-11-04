# no-slop-ui checklist (beezy-vip)

Hard rules for every work surface (`/ev`, `/analytics`, `/sports/[sport]`, `/edge`, and anything new). A page fails
review if any item below is true. Static guards for most of these live in `tests/no-slop-ui.test.tsx`.

1. **No eyebrow + mood headline + prose intro.** A page opens with one compact toolbar row: `<h1>` (20px / 700, plain
   name such as "+EV Terminal") on the left, controls/tabs/row count on the right. Explanatory prose goes in a footnote,
   a `title`, or a `<details>`, never above the tool.
2. **No KPI-card grid above the work surface.** Headline numbers are one inline metric strip (about 38px: `#11131a`,
   1px `#1e2230`, 8px radius), e.g. `7,546 graded · +$57,028 flat (+8.2% ROI) · $1,000 -> $14,300 1/4-Kelly`.
3. **No coloured glows, no decorative gradients.** Surfaces are solid `#11131a`, border `1px solid #1e2230`, radius 8px,
   shadow at most `0 1px 2px rgba(0,0,0,0.45)`. Tier/status is a left border or a text badge, never a neon box-shadow.
   `TIER_META.*.glow` stays `'none'`.
4. **No static decorative nav badges.** A badge appears only if it carries a live count; otherwise plain labels.
5. **No card-per-row stacks for tabular data.** Lists of comparable records are a real `<table>`: one sticky `<thead>`,
   hairline rows, tabular monospace numbers, expandable detail as a `<tr><td colSpan>` sub-row. Column labels never
   repeat inside rows.
6. **No transform animation on hover.** Hover changes background or border colour only (`120ms ease`). No
   `translate`/`scale` on `:hover`.

Also (from PRODUCT.md): no emoji or exclamation points in UI copy; SVG and Satori OG routes use literal hex, never
`var()`; source files stay ASCII (use escapes for typographic characters).

## Review log

| Date | Surface | Result | Notes |
|---|---|---|---|
| 2026-10-14 | `/ev` | PASS | toolbar header, ledger strip, filter bar, table with Compare-books sub-row |
| 2026-10-14 | `/analytics` | PASS | h1 + tabs on one row; paper-results caveat is a footnote |
| 2026-10-14 | `/sports/[sport]` | PASS | solid header bar with title, status badge and tabs inline |
| 2026-10-14 | `/edge` | PARTIAL | no glows/gradients/hover transforms/KPI grid/cards-per-row; FAILS rule 1 in part: `.edge-tagline` paragraph sits under the title row. Not changed here (file belongs to #54) |
| 2026-10-15 | `/edge` | PASS | h1 is "Prop Research" on one toolbar row; `.edge-tagline` paragraph removed; sport tabs hidden when no cockpit feed |
| 2026-10-15 | `/picks`, `/results`, `/models`, `/tools`, `/picks/mlb`, `/tools/clv-tracker` | PASS | one-row headers, inline metric strips, real tables; `/picks/mlb` and `/tools` converted from card grids to tables |
| 2026-10-15 | `/tools/*`, `/login`, `/blocked`, `/dashboard`, `/learn/[slug]` | PARTIAL | one-row headers via `ToolHeader`; still failing rules 1/2/5: `/signup`, `/pricing` (gradient + glow), `/learn` index (tagline, card grid), `/models/[slug]` and `/picks/mlb/[system]` (30px h1 + KPI grids), `/cheat-sheet` |
| 2026-10-16 | `/picks` (Top 10), `/picks/history`, `/picks/mlb`, `/sports/[sport]` | PASS | one-row headers; Top 10 and each history day are real tables (`TopPicksTable`, one sticky `<thead>`), a history day opens as a `colSpan` sub-row; hubs share one 3-tab template; no glow/gradient/hover transform |
| 2026-10-16 | `/picks/mlb/[system]`, systems footers | PASS | system detail page rebuilt: one toolbar row, inline metric strip, real tables (was 30px h1 + KPI grid); systems are a page footer, not a tab |
| 2026-10-15 | `/pricing`, `/sports/[sport]` (NFL/NHL/NBA), `/ev` My books | PASS | pricing rebuilt as one toolbar row + three solid plan surfaces + 3-column explainer (no gradient/glow, featured plan = border colour); projection board is a real table with one sticky `<thead>`; My books is a filter row, not cards |
| 2026-10-17 | `/` (home hero), buttons, `.ribbon-card-wrap` | PARTIAL | hero gradient and radius-xl removed (solid SURFACE_CARD); `.btn-primary:hover` is a flat #6bf596 shift and `--glow-signal` is deleted; one 8px surface spec app-wide. `/` still fails rules 1 and 5 (eyebrow pill, prose intro, step cards): not rebuilt here |
| 2026-10-21 | `/ev/odds` | PASS | one toolbar row (h1 + row count + refresh), one filter bar, one real `<table>` with a sticky `<thead>` and hairline rows; best price = green text on a flat 6% tint (no glow); hover is a 120ms background change |
| 2026-10-25 | `/ev/odds` (3 modes) | PASS | mode switcher is a recessed pill group on the existing filter bar (two rows inside one surface, the second row changes per mode); still one real `<table>` with a sticky `<thead>` and a sticky PROP column; tint / outline are flat colours (no glow, no gradient); hover is a 120ms background change; no text below 10px |
| 2026-10-25 | `/` (home) | PASS | hero solid surface, no PAPER MODE boxes; today's plays and recent graded plays are real tables capped at 6 rows; league coverage cards (all 4 sports) replace the MLB-only systems list; How it works is one 4-cell strip; pricing summary from lib/plans. Not browser-verified (Clerk keys) |
| 2026-11-02 | `/picks/[sport]` + `/projections` + system pages (NFL/NHL/NBA) on a phone | PASS | still one real `<table>` with one `<thead>`; below 768px it keeps SCORE, PLAYER, PICK, MODEL, BEST PRICE, EDGE (no sideways scroll at 375px) and the dropped columns open as a `colSpan` sub-row on tap; no glow/gradient/hover transform; UNVALIDATED is a flat amber text badge. Rendered in the Browser pane against fixture rows (real archive shapes), AUTH_DISABLED |
| 2026-11-02 | BoardUI port + design tokens (#159, #163), all pages | PARTIAL | statically checked: no hover transforms, no glow shadows, no new gradients, no emoji; removed the Discord CTA gradient and glow (solid surface) and non-ASCII from `styles/*.css`. DEVIATION: surface radius is 12px (BoardUI) not 8px, shadow `0 1px 1px rgba(0,0,0,0.05)` is within the cap. Rules 1/2/5 were not re-audited page by page; structure was ported, not redesigned. Not browser-verified on the Vercel preview |
| 2026-11-03 | `/ev` Odds Terminal, `/ev/projections`, `/picks/*`, `/results`, `/models`, `/tools/*`, `/dashboard/*`, `/cheat-sheet`, `/learn`, shared `components/ui` and `components/picks` | PASS | same pass as Game Lines: half-pixel sizes snapped to the 10/11/12/13/14/16/18 scale, bold to semibold (h1 stays 700), tracked uppercase labels set in sentence case (`.ui-heading` no longer uppercases or letter-spaces). Guards added in `tests/no-slop-ui.test.tsx`. NOT touched: `/edge` (owned by the Prop Research PR), OG cards (Satori, fixed canvases), the brand wordmark. Checked in the Browser pane on `/ev`, `/results`, `/models` against the mock feed |
| 2026-11-03 | `/edge` NBA inspector (shot chart) | PASS | new Shot chart section is a flat panel: inline metric strip, a three-way view switch, real SVG (hex colours, no gradient or glow), real `<table>`s for zones / shot types / opponent / home-away, no hover transform, no text under 10px, no KPI grid. Rendered in the Browser pane at 375px and desktop and in the iOS Simulator (iPhone 17 Pro) against a mock cockpit feed with REAL shots from the supplied file; AUTH_DISABLED |
| 2026-11-03 | `/edge` NBA inspector (Trends / Shots / Prices tabs) | PASS | three-tab switch on one row, flat panels, real `<table>`s for every comparison (windows, splits, teammate-out, drivers, zones, quarters), SVG charts with literal hex, filter chips (not a card grid), no glow / gradient / hover transform, no text under 10px. Browser pane at 375px and desktop and iPhone 17 Pro Simulator against a mock cockpit feed with REAL shots and box scores; AUTH_DISABLED |
| 2026-11-03 | every route except `/edge*` (46 routes), phone 375px and desktop 1280px | PASS | measured in the Browser pane with an in-page script (computed font size, text-transform, letter-spacing, all-caps text, sideways overflow, h1 count) against the mock feed: no overflow, no off-scale size except the 34px plan prices, no tracked or CSS-uppercase text. Leftovers by design: OOS AUC/MAE, LIVE, legal-page disclaimers, and `/pricing` plan prices. The mock feed is empty on most boards, so row data (team and player names, result chips) was checked by tests and source grep, not in the browser |
| 2026-11-03 | phone, iOS Simulator (iPhone 17 Pro) + a horizontal-clip script on 46 routes | PASS | seen on the simulator: Game Lines (board, card, all lines), Odds Terminal, Projections, Props (/edge), home, burger menu, Picks (Top Signals, NBA, MLB NRFI, history, systems, games), Results, Analytics, Models and model detail, Pricing, Cheat sheet, Learn and an article, Signup, Tools and four tools, Dashboard. Fixed from it: hydration error (iOS phone-number linking), Odds Terminal filter bar, Results filters behind one row, active section tab scrolled into view, model detail grids, Tools list, empty day bars, shared table-header caps, `/edge` type scale and caps. NOT seen on the simulator: About, Compare, Login, Legal pages, Blocked, Dashboard history and settings, Kelly, Boost EV and Bet tracker tools, `/edge/projections`; those passed the clip and caps scripts only. Boards had no rows (mock feed), so row layouts are covered by tests only. Known leftovers: `/learn` is a card-per-article list (rule 5), the home hero still has an eyebrow pill (rule 1), `/edge` filter panel is tall on a phone and uses square corners |
