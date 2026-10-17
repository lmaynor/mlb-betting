/**
 * Subscription tiers: the one place that knows the tier names, which Stripe price maps to which tier, and which tiers
 * unlock the whole product. Pure (no Clerk / Stripe imports) so the checkout route, the Stripe webhook, both
 * reconcilers, the pricing page and the tests all agree.
 *
 * Current plans: single_sport ($19/mo, one league), pro_monthly ($39/mo, all four), pro_annual ($299/yr, all four).
 * Legacy names (starter / pro / season) stay valid so existing Clerk metadata and Stripe prices keep working.
 */
import { SPORT_SLUGS } from '@/lib/sports'

export const TIERS = ['public', 'single_sport', 'pro_monthly', 'pro_annual', 'starter', 'pro', 'season'] as const
export type Tier = (typeof TIERS)[number]

/** The leagues a Single-Sport Pass can be bought for: every sport in lib/sports.ts, so a new sport joins the selector there. */
export const LEAGUES = SPORT_SLUGS
export type League = (typeof LEAGUES)[number]

export const ALL_ACCESS: readonly Tier[] = ['pro_monthly', 'pro_annual', 'pro', 'season']

export function isLeague(x: unknown): x is League {
  return typeof x === 'string' && (LEAGUES as readonly string[]).includes(x)
}

export function isTier(x: unknown): x is Tier {
  return typeof x === 'string' && (TIERS as readonly string[]).includes(x)
}

export function isAllAccess(tier: string | null | undefined): boolean {
  return ALL_ACCESS.includes(tier as Tier)
}

/** Stripe env var per purchasable tier. Pro monthly falls back to the legacy STRIPE_PRICE_PRO. */
export const PRICE_ENV: Record<Exclude<Tier, 'public'>, readonly string[]> = {
  single_sport: ['STRIPE_PRICE_SINGLE_SPORT'],
  pro_monthly: ['STRIPE_PRICE_PRO_MONTHLY', 'STRIPE_PRICE_PRO'],
  pro_annual: ['STRIPE_PRICE_PRO_ANNUAL'],
  starter: ['STRIPE_PRICE_STARTER'],
  pro: ['STRIPE_PRICE_PRO'],
  season: ['STRIPE_PRICE_SEASON'],
}

type Env = Record<string, string | undefined>

/** Stripe price id for a tier, or undefined when the env var is not set. */
export function priceIdFor(tier: string, env: Env = process.env): string | undefined {
  const names = PRICE_ENV[tier as Exclude<Tier, 'public'>]
  if (!names) return undefined
  for (const n of names) if (env[n]) return env[n]
  return undefined
}

/**
 * Stripe price id -> tier. When pro_monthly falls back to the legacy STRIPE_PRICE_PRO the two share one price id and it
 * resolves to the legacy name `pro` (both are all-access, so nothing is gated differently).
 */
export function priceToTierMap(env: Env = process.env): Record<string, Tier> {
  const out: Record<string, Tier> = {}
  for (const tier of ['starter', 'season', 'single_sport', 'pro_annual', 'pro_monthly', 'pro'] as const) {
    const id = priceIdFor(tier, env)
    if (id) out[id] = tier
  }
  return out
}

/** Raw Clerk metadata value -> a known tier ('public' for anything unknown). */
export function normalizeTier(raw: unknown): Tier {
  return isTier(raw) ? raw : 'public'
}

export function tierLabel(tier: string): string {
  switch (tier) {
    case 'single_sport': return 'Single-Sport Pass - $19/mo'
    case 'pro_monthly': return 'All-Access Pro - $39/mo'
    case 'pro_annual': return 'All-Access Pro - $299/yr'
    case 'pro': return 'Pro'
    case 'season': return 'Season'
    case 'starter': return 'Starter'
    default: return 'Free'
  }
}
