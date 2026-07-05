# Beezy.FYI — PRODUCT.md

## Register
product

## Product Purpose
A data product for sports bettors who want ML-backed MLB picks. Ten XGBoost models score games daily across markets (NRFI, HR, F5, K, OUTS, GAME, BATTER_TB, BATTER_HITS, and others). Every pick is transparent: model probability, market implied probability, edge, Kelly stake, and rationale are all shown. Settlement runs nightly; wins, losses, pushes, and voids are public. Nothing is hidden.

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
Dark terminal aesthetic (replaced the retro "Dell 1996" look 2026-06-28):
- Near-black neutral surface ladder: carbon #04040b (canvas) -> graphite #121113
  (cards/nav) -> obsidian #1a191b -> slate #232225 (inputs); hairline borders
  basalt #2b292d / iron #323035
- Text ladder: fog #8a8893 (muted) -> silver #b5b2bc -> ash #eeeef0 (body) ->
  chalk #f3f2f5 (headings)
- Brand: Signal Green #71d083 (primary CTA / WIN / live indicators); loss red
  #ec6a6a; warn amber #e3b261 (line alerts); link blue #70b8ff
- Per-system color taxonomy (Discord-style) in lib/tokens.ts SYSTEM_COLOR;
  system colors drive pills, chart lines, and detail accents
- Typography: Red Hat trio (font-text sans, font-mono for all data/numbers,
  times for prose captions); tabular-nums everywhere numbers align
- Tokens live in app/globals.css :root + lib/tokens.ts. RULE: CSS var() does
  NOT work inside SVG attributes (recharts/inline SVG) or Satori OG routes --
  use literal hex there

## Anti-References
- **SaaS cream/beige landing page** — warm-tinted bg, rounded cards, Inter everywhere, hero metrics grid. The AI default. Never.
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
