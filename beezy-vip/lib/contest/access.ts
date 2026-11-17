/**
 * Who is a contest participant, and how a Clerk user gets that role.
 *
 * AUTHORITY: the contest database. A player is a participant once `contest_entry` has a row for them, and that row only exists after
 * they redeemed an invite code (contest/store.py join_contest). Clerk holds a MIRROR of that fact so the site can answer "is this user
 * in contest X?" without a database call (a nav link, a page shell, an admin check). The mirror is written by the server when a join
 * succeeds and can be rebuilt from the database at any time; if the two ever disagree, the database wins.
 *
 * WHERE IT LIVES: Clerk `privateMetadata.contest = { roles: { <slug>: 'participant' | 'admin' } }`. Private metadata is writable only
 * through the Backend API and is never sent to the browser or put in the session token, the same place the Stripe webhook keeps `tier`
 * (lib/auth.ts). Clerk Organizations (a role per org membership) were considered and not used: they need the Organizations feature
 * switched on in the dashboard, give every member an org switcher, and still would not replace the invite-code redemption the contest
 * needs for its own rules (docs/designs/bowl-bankroll-contest-scope.md, "Clerk roles"). A session-token claim
 * (`{{user.public_metadata.contest}}`) would let middleware read the role with no extra call, but it is a dashboard setting we do not
 * control from code, so nothing here depends on it.
 *
 * Pure parsing and a thin writer: the Clerk client is injected so the tests never touch Clerk.
 */
export type ContestRole = 'participant' | 'admin'

const ROLES: readonly ContestRole[] = ['participant', 'admin']
export const isContestRole = (x: unknown): x is ContestRole => typeof x === 'string' && (ROLES as readonly string[]).includes(x)

type Roles = Record<string, ContestRole>

/** The contest roles stored in a Clerk user's private metadata. Anything malformed is ignored (never throws). */
export function contestRoles(privateMetadata: unknown): Roles {
  const raw = (privateMetadata as { contest?: { roles?: unknown } } | null | undefined)?.contest?.roles
  if (!raw || typeof raw !== 'object') return {}
  return Object.fromEntries(Object.entries(raw as Record<string, unknown>).filter(([, v]) => isContestRole(v))) as Roles
}

/** The role this user holds in contest `slug`, or null. An admin of a contest is also a participant of it. */
export const roleIn = (privateMetadata: unknown, slug: string): ContestRole | null => contestRoles(privateMetadata)[slug] ?? null
export const isParticipant = (privateMetadata: unknown, slug: string): boolean => roleIn(privateMetadata, slug) !== null
export const isAdmin = (privateMetadata: unknown, slug: string): boolean => roleIn(privateMetadata, slug) === 'admin'

/** The subset of Clerk's backend client this module needs. */
export interface ClerkUsers {
  users: {
    getUser(id: string): Promise<{ privateMetadata?: unknown }>
    updateUserMetadata(id: string, params: { privateMetadata: Record<string, unknown> }): Promise<unknown>
  }
}

/**
 * Record `role` for `userId` in contest `slug`. Read-modify-write so the user's other contests, the Stripe `tier` and every other
 * private-metadata key are preserved. An admin is never downgraded to participant by a repeat join.
 */
export async function grantContestRole(clerk: ClerkUsers, userId: string, slug: string, role: ContestRole = 'participant'): Promise<Roles> {
  const user = await clerk.users.getUser(userId)
  const roles = contestRoles(user.privateMetadata)
  if (roles[slug] === 'admin' && role === 'participant') return roles
  const next: Roles = { ...roles, [slug]: role }
  const existing = (user.privateMetadata as { contest?: Record<string, unknown> } | undefined)?.contest ?? {}
  await clerk.users.updateUserMetadata(userId, { privateMetadata: { contest: { ...existing, roles: next } } })
  return next
}

/** Remove the user's role in `slug` (an organiser removing a player, or a rebuild after the database disagrees). */
export async function revokeContestRole(clerk: ClerkUsers, userId: string, slug: string): Promise<Roles> {
  const user = await clerk.users.getUser(userId)
  const rest = Object.fromEntries(Object.entries(contestRoles(user.privateMetadata)).filter(([k]) => k !== slug)) as Roles
  const existing = (user.privateMetadata as { contest?: Record<string, unknown> } | undefined)?.contest ?? {}
  await clerk.users.updateUserMetadata(userId, { privateMetadata: { contest: { ...existing, roles: rest } } })
  return rest
}
