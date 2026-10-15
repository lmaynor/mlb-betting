---
name: no-slop-ui
description: Use when building or changing any beezy-vip page, component or OG card. Enforces the six hard bans (eyebrow + mood headline blocks, KPI-card grids, neon glows and decorative gradients, static nav badges, card-per-row stacks, hover transforms) so work surfaces stay dense Linear/GitHub-grade tools, not AI-default landing-page slop.
---

# no-slop-ui

Read `beezy-vip/NO_SLOP_CHECKLIST.md` before touching a page in `beezy-vip/`; it is the contract. Summary:

- Page header = one toolbar row: plain `<h1>` left, controls right. No eyebrow label, no mood sentence, no intro paragraph.
- Headline numbers = one inline metric strip, not a grid of KPI cards.
- Surfaces = solid `#11131a`, `1px solid #1e2230`, 8px radius, shadow <= `0 1px 2px rgba(0,0,0,0.45)`. No glows, no gradients.
- Nav badges only for live counts.
- Comparable records = a real `<table>` with one sticky `<thead>`; detail opens as a `colSpan` sub-row.
- Hover = background/border colour, 120ms. No transforms.

Before finishing: run `npm test` (includes `tests/no-slop-ui.test.tsx`) and `npx tsc --noEmit` in `beezy-vip/`, then add a row to the
review log in the checklist.
