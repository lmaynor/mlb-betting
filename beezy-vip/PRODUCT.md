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
Dell 1996 catalog aesthetic applied over a dark base:
- Page frame: 8px solid black border around the viewport
- Typography: Arial Black (display), Arial Bold (headings), JetBrains Mono (data), Times New Roman (body prose)
- Color: Dell catalog tints (sage, salmon, sky, periwinkle, peach, olive, lime, steel) mapped to betting systems
- Hard 1px black borders; no rounded corners; no soft shadows; no gradients
- Dell red (#e91d2a) for primary CTA only; Dell yellow (#fcc20f) for sticker-style accent buttons
- Classic Netscape link blue (#0000ee) for text links

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
