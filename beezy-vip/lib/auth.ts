import { auth, currentUser } from '@clerk/nextjs/server'

export type Tier = 'public' | 'starter' | 'pro' | 'season'

/**
 * Get the current user's access tier from Clerk metadata.
 * Metadata is set by the Stripe webhook after successful subscription.
 */
export async function getUserTier(): Promise<Tier> {
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
