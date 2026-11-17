/**
 * Who is calling the contest. The signed-in Clerk user id; while the site runs with AUTH_DISABLED (local review only, ignored on a
 * Vercel production deployment) a local developer may name a stand-in with CONTEST_DEV_USER so the page can be driven against a
 * real backend without Clerk. Returns null for an anonymous visitor.
 */
import { getCurrentUser, getUserId } from '@/lib/auth'
import { AUTH_DISABLED } from '@/lib/auth-mode'

export interface ContestUser { id: string; suggestedName: string }

export async function contestUser(): Promise<ContestUser | null> {
  if (AUTH_DISABLED) {
    const dev = (process.env.CONTEST_DEV_USER ?? '').trim()
    return dev ? { id: dev, suggestedName: dev } : null
  }
  const id = await getUserId()
  if (!id) return null
  const u = await getCurrentUser()
  const first = u?.firstName?.trim() ?? ''
  const suggested = (u?.username || first || '').slice(0, 20)
  return { id, suggestedName: suggested }
}
