import { auth, currentUser } from '@clerk/nextjs/server'
import { AUTH_DISABLED } from './auth-mode'
import { viewerOf, type Viewer } from './access'
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
 * The current user's tier and (for a Single-Sport Pass) chosen league, from Clerk metadata set by the Stripe webhook.
 * Anyone not signed in, or on any error, is a free viewer. While AUTH_DISABLED the viewer is `pro` so tier-gated UI can be
 * reviewed locally; set `AUTH_DISABLED_TIER` (e.g. `public`, `single_sport`) and `AUTH_DISABLED_SPORT` to review the gates.
 */
export async function getViewer(): Promise<Viewer> {
  if (AUTH_DISABLED) return viewerOf(normalizeTier(process.env.AUTH_DISABLED_TIER ?? 'pro'), process.env.AUTH_DISABLED_SPORT)
  try {
    const { userId } = await auth()
    if (!userId) return viewerOf('public', null)

    const user = await currentUser()
    if (!user) return viewerOf('public', null)

    const tier = normalizeTier(user.privateMetadata?.tier)
    // legacy pro / season stay collapsed to 'pro' (existing behaviour); the new plan names are returned as they are
    return viewerOf(tier === 'season' ? 'pro' : tier, user.privateMetadata?.sportChoice)
  } catch {
    return viewerOf('public', null)
  }
}

/** The current user's access tier (see getViewer). */
export async function getUserTier(): Promise<Tier> {
  return (await getViewer()).tier
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
