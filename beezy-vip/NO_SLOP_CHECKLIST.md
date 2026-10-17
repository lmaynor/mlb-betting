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
