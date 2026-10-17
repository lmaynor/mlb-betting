# Beezy.FYI — PRODUCT.md

## Register
product

## Product Purpose
A data product for sports bettors who want ML-backed MLB picks. Ten XGBoost models and a live +EV scanner score games daily across markets (NRFI, HR, F5, K, OUTS, GAME, BATTER_TB, BATTER_HITS, and others). Every pick is transparent: model probability, market implied probability, edge, Kelly stake, and rationale are all shown. Settlement runs nightly; wins, losses, pushes, and voids are public. Nothing is hidden.

## Target Users
- Sports bettors (recreational to semi-serious) who are frustrated with gut-feel tout services
- Analytically-minded bettors who want to understand why a pick was generated, not just what it is
- Bettors already familiar with Kelly sizing, implied probability, and edge concepts
- Primarily US-based, desktop and mobile, ambient light varies (stadium, couch, commute)

## Primary Tasks
1. Check today's card — scan picks ranked by Beezy Score (0–100 conviction index)
2. Review settled results — verify the track record is real and public
3. Understand a pick — read the model rationale, edge vs. market price, Kelly stake
4. Use tools — odds calculator, Kelly calculator, edge finder

## Brand Personality
Analytical · Credible · Sharp

## Brand Voice
- Never tout. State the model's read and the market price; let the math speak.
- Short sentences. Data labels, not prose paragraphs.
- No emoji, no exclamation points.
- Precision over warmth. "53.4% model probability" not "strong lean."

## Current Design Language
Terminal design system v2 (2026-10-14), per the v2 overhaul spec. Dark fintech:
- Canvas #090a0f, card #11131a, elevated #171a24, hairlines #1e2230 / #151822
- Brand emerald #57F287 (CTA, WIN, +EV); lean amber #facc15; loss #ef4444; info #38bdf8
- Text: #f3f4f6 body, #9ca3af secondary, #7d8596 muted (kept above AA on the canvas)
- Typography (three faces, app/layout.tsx): Space Grotesk 600/700 for h1/h2, display classes and the wordmark
  (--font-display); Plus Jakarta Sans for dense UI and tables (--font-inter, a historical variable name that ~1,600
  inline styles read); IBM Plex Mono for every odd, probability, EV%, stake, timestamp and every uppercase micro-label
  (--font-mono), with tabular-nums and slashed zeros. 10px is the minimum size for any text.
- Geometry: ONE surface spec (SURFACE_CARD in lib/tokens.ts): #11131a, 1px #1e2230, 8px radius, at most a 1px shadow.
  6px badges, pills for system badges. Shared table bases are TABLE_TH / TABLE_TD; do not re-declare them per file.
- Canonical classes are .ui-display, .ui-heading and .btn-primary; the .dell-* names are deprecated aliases.
- Palette is closed: six retired off-palette grays are banned by tests/no-slop-ui.test.tsx.
- Per-system hues in lib/tokens.ts SYSTEM_COLOR (+ NFL stat keys); book identity in BOOK_META
- Tokens live in app/globals.css :root + lib/tokens.ts; legacy variable names are aliases.
  RULE: CSS var() does NOT work inside SVG attributes (recharts/inline SVG) or Satori OG routes -- use literal hex there

## Anti-References
- **SaaS cream/beige landing page** — warm-tinted bg, hero metrics grid. The AI default. Never. (The house faces are Space Grotesk, Plus Jakarta Sans and IBM Plex Mono by owner decision 2026-10-17; the rest of this anti-pattern still applies.)
- **Sports media (ESPN, The Athletic)** — photo-heavy, team-color splashes, editorial hero images. This is a data product, not content.
- **DraftKings / FanDuel** — promo-code energy, "$100 bonus" CTAs, aggressive green. Beezy earns trust through transparency, not promotion.
- **Bloomberg Terminal clone** — raw data dump with no visual hierarchy or information design. Data must be navigable.

## Accessibility
- WCAG AA minimum on all text/bg pairs
- Touch targets ≥44px on mobile
- Keyboard navigable primary flows
- No color-only information encoding

## Status
Pre-launch paper mode. No real money transacted. Models clear a 200-bet gate before paid access opens.
