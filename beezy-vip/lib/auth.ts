import { auth, currentUser } from '@clerk/nextjs/server'
import { AUTH_DISABLED } from './auth-mode'
import { ALL_ACCESS, normalizeTier, type Tier } from './tiers'

export type { Tier }

/** Signed-in user id, or null (always null while AUTH_DISABLED). Never throws. */
export async function getUserId(): Promise<string | null> {
  if (AUTH_DISABLED) return null
  try {
    return (await auth()).userId ?? null
  } catch {
    return null
  }
}

/** Clerk user, or null (always null while AUTH_DISABLED). Never throws. */
export async function getCurrentUser() {
  if (AUTH_DISABLED) return null
  return currentUser().catch(() => null)
}

/**
 * Get the current user's access tier from Clerk metadata.
 * Metadata is set by the Stripe webhook after successful subscription.
 */
export async function getUserTier(): Promise<Tier> {
  if (AUTH_DISABLED) return 'pro'      // local review: show tier-gated UI (see lib/auth-mode.ts)
  try {
    const { userId } = await auth()
    if (!userId) return 'public'

    const user = await currentUser()
    if (!user) return 'public'

    const tier = normalizeTier(user.privateMetadata?.tier)
    // legacy pro / season stay collapsed to 'pro' (existing behaviour); the new plan names are returned as they are
    if (tier === 'season') return 'pro'
    return tier
  } catch {
    return 'public'
  }
}

export function canAccessFeature(
  tier: Tier,
  feature: 'allPicks' | 'stakeSizing' | 'modelProb' | 'fullResults' | 'dashboard' | 'csvExport'
): boolean {
  const ALL = [...ALL_ACCESS]
  const matrix: Record<typeof feature, Tier[]> = {
    allPicks:    ALL,
    stakeSizing: ALL,
    modelProb:   ALL,
    fullResults: ['starter', 'single_sport', ...ALL],
    dashboard:   ['single_sport', ...ALL],
    csvExport:   ALL,
  }
  return matrix[feature].includes(tier)
}
