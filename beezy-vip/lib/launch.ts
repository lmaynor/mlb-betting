/**
 * Pre-launch switch for paid access. Do NOT flip without the gate criteria in CONTEXT.md s13 (Stripe section).
 * Read by the pricing component, the signup page and the nav CTA so they can never disagree.
 */
export const PRE_LAUNCH = true

/**
 * AEO-02, decided by Luc Nov 23, 2026 — 'A' is the live-product framing; 'B' is the waitlist framing. One-line flip.
 */
export const HOMEPAGE_FRAMING: 'A' | 'B' = 'A'
