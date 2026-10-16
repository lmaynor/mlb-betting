export const dynamic = 'force-dynamic'

// Links the signed-in member's Discord account and pushes their roles.
//
// The Discord id is NEVER taken from the request: it is read from the Discord account the member connected through
// Clerk's OAuth (external account, provider "discord"), so nobody can claim someone else's id -- that would let a free
// account strip another member's paid roles on the next sync. Body is ignored.
//
//   POST /api/user/discord-link  ->  { linked: true, discord_user_id, tier, sync: { ok, skipped?, reason? } }
import { NextResponse } from 'next/server'
import { clerkClient } from '@clerk/nextjs/server'
import { getUserId } from '@/lib/auth'
import { discordIdOf, syncUserRoles } from '@/lib/discord-sync'
import { normalizeTier } from '@/lib/tiers'

export async function POST() {
  const userId = await getUserId()
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const clerk = await clerkClient()
  const user = await clerk.users.getUser(userId)
  const discordId = discordIdOf(user.externalAccounts as never)
  if (!discordId) {
    return NextResponse.json({ error: 'Connect your Discord account in your account settings first.' }, { status: 409 })
  }

  const tier = normalizeTier(user.privateMetadata?.tier)
  await clerk.users.updateUserMetadata(userId, { privateMetadata: { discordUserId: discordId } })
  const sync = await syncUserRoles({ ...user, privateMetadata: { ...user.privateMetadata, discordUserId: discordId } }, userId, tier)
  return NextResponse.json({ linked: true, discord_user_id: discordId, tier, sync })
}
