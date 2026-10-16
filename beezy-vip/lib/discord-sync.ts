/**
 * Tells beezy-bot (discord-bot/) to grant or revoke a member's Discord roles when their subscription changes or they
 * link a Discord account. Best-effort by design: a Stripe webhook must never fail or retry-storm because Discord is
 * down, so every failure resolves to a result object instead of throwing.
 *
 * Env (server only): BEEZY_BOT_SYNC_URL (full URL of POST /internal/sync-role), BEEZY_BOT_SECRET (sent as X-Beezy-Secret).
 */
import { isLeague, type League } from '@/lib/tiers'

export interface RoleSyncPayload {
  discord_user_id: string
  email: string | null
  tier: string
  sport_choice: League | null
  clerk_user_id?: string
}

export type RoleSyncResult =
  | { ok: true; status: number }
  | { ok: false; skipped: true; reason: string }
  | { ok: false; skipped?: false; status?: number; reason: string }

/** Discord user ids are snowflakes: 17-20 digits. */
export const isSnowflake = (x: unknown): x is string => typeof x === 'string' && /^\d{17,20}$/.test(x)

export function buildRoleSyncPayload(input: {
  discordUserId: unknown; email?: string | null; tier: string; sport?: unknown; clerkUserId?: string
}): RoleSyncPayload | null {
  if (!isSnowflake(input.discordUserId)) return null
  return {
    discord_user_id: input.discordUserId,
    email: input.email ?? null,
    tier: input.tier,
    sport_choice: isLeague(input.sport) ? input.sport : null,
    ...(input.clerkUserId ? { clerk_user_id: input.clerkUserId } : {}),
  }
}

export async function syncDiscordRole(
  payload: RoleSyncPayload | null,
  opts: { url?: string; secret?: string; fetchImpl?: typeof fetch; timeoutMs?: number } = {},
): Promise<RoleSyncResult> {
  const url = opts.url ?? process.env.BEEZY_BOT_SYNC_URL
  const secret = opts.secret ?? process.env.BEEZY_BOT_SECRET
  if (!payload) return { ok: false, skipped: true, reason: 'no linked Discord account' }
  if (!url || !secret) return { ok: false, skipped: true, reason: 'BEEZY_BOT_SYNC_URL / BEEZY_BOT_SECRET not set' }
  const f = opts.fetchImpl ?? fetch
  try {
    const res = await f(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Beezy-Secret': secret },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(opts.timeoutMs ?? 8000),
    })
    // 404 = the member has not joined the server yet; the bot still stored the link, so this is not a failure.
    if (res.ok || res.status === 404) return { ok: true, status: res.status }
    return { ok: false, status: res.status, reason: `bot answered ${res.status}` }
  } catch (err) {
    return { ok: false, reason: err instanceof Error ? err.message : String(err) }
  }
}

/** The slice of a Clerk user this module reads (kept structural so tests need no Clerk). */
export interface SyncableUser {
  privateMetadata?: Record<string, unknown>
  emailAddresses?: { emailAddress: string }[]
}

/** Push one Clerk user's current tier to the bot. No-ops (skipped) when the user has not linked Discord. */
export async function syncUserRoles(
  user: SyncableUser | null | undefined, clerkUserId: string, tier: string, sport?: unknown,
  opts: Parameters<typeof syncDiscordRole>[1] = {},
): Promise<RoleSyncResult> {
  const meta = user?.privateMetadata ?? {}
  const payload = buildRoleSyncPayload({
    discordUserId: meta.discordUserId,
    email: user?.emailAddresses?.[0]?.emailAddress ?? null,
    tier,
    sport: sport ?? meta.sportChoice,
    clerkUserId,
  })
  return syncDiscordRole(payload, opts)
}

/** Clerk names the provider "oauth_discord" (older SDKs: "discord"). */
export function discordIdOf(accounts: { provider?: string; providerUserId?: string; externalId?: string }[] | undefined): string | null {
  for (const a of accounts ?? []) {
    if (a.provider === 'oauth_discord' || a.provider === 'discord') {
      const id = a.providerUserId ?? a.externalId
      if (isSnowflake(id)) return id
    }
  }
  return null
}
