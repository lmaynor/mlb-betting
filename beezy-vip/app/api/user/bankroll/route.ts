import { NextRequest, NextResponse } from 'next/server'
import { clerkClient }               from '@clerk/nextjs/server'
import { getUserId }                 from '@/lib/auth'

export async function POST(req: NextRequest) {
  const userId = await getUserId()
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const { bankroll } = await req.json().catch(() => ({})) as { bankroll?: number }

  if (!bankroll || typeof bankroll !== 'number' || bankroll <= 0) {
    return NextResponse.json({ error: 'Invalid bankroll value' }, { status: 400 })
  }

  const clerk = await clerkClient()
  await clerk.users.updateUserMetadata(userId, {
    publicMetadata: { bankroll },
  })

  return NextResponse.json({ bankroll })
}
