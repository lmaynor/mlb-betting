import { auth, currentUser } from '@clerk/nextjs/server'
import { AUTH_DISABLED } from './auth-mode'

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

export type Tier = 'public' | 'starter' | 'pro' | 'season'

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

    const tier = user.privateMetadata?.tier as string | undefined
    if (tier === 'pro' || tier === 'season') return 'pro'
    if (tier === 'starter') return 'starter'

    return 'public'
  } catch {
    return 'public'
  }
}

export function canAccessFeature(
  tier: Tier,
  feature: 'allPicks' | 'stakeSizing' | 'modelProb' | 'fullResults' | 'dashboard' | 'csvExport'
): boolean {
  const matrix: Record<typeof feature, Tier[]> = {
    allPicks:    ['pro', 'season'],
    stakeSizing: ['pro', 'season'],
    modelProb:   ['pro', 'season'],
    fullResults: ['starter', 'pro', 'season'],
    dashboard:   ['pro', 'season'],
    csvExport:   ['pro', 'season'],
  }
  return matrix[feature].includes(tier)
}
