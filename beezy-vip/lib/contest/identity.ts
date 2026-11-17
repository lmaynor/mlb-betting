/**
 * Who is calling the contest. The signed-in Clerk user id; while the site runs with AUTH_DISABLED (local review only, ignored on a
 * Vercel production deployment) a local developer may name a stand-in with CONTEST_DEV_USER so the page can be driven against a
 * real backend without Clerk. Returns null for an anonymous visitor.
 */
import { getCurrentUser, getUserId } from '@/lib/auth'
import { isAdmin } from '@/lib/contest/access'
import { AUTH_DISABLED } from '@/lib/auth-mode'

export interface ContestUser { id: string; suggestedName: string; /** The account's primary email is verified (Clerk). Joining requires it, so one inbox is one person's account. */ emailVerified: boolean }

/** Is the signed-in user an organiser of contest `slug`? (Clerk private metadata; while AUTH_DISABLED, CONTEST_DEV_ADMIN=1 stands in.) */
export async function isContestAdmin(slug: string): Promise<boolean> {
  if (AUTH_DISABLED) return process.env.CONTEST_DEV_ADMIN === '1' && !!(process.env.CONTEST_DEV_USER ?? '').trim()
  const u = await getCurrentUser()
  return !!u && isAdmin(u.privateMetadata, slug)
}

export async function contestUser(): Promise<ContestUser | null> {
  if (AUTH_DISABLED) {
    const dev = (process.env.CONTEST_DEV_USER ?? '').trim()
    return dev ? { id: dev, suggestedName: dev, emailVerified: true } : null
  }
  const id = await getUserId()
  if (!id) return null
  const u = await getCurrentUser()
  const first = u?.firstName?.trim() ?? ''
  const suggested = (u?.username || first || '').slice(0, 20)
  return { id, suggestedName: suggested, emailVerified: u?.primaryEmailAddress?.verification?.status === 'verified' }
}
