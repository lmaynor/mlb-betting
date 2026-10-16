import { NextResponse } from 'next/server'
import { limitBets } from '@/lib/access-bets'
import { getViewer } from '@/lib/auth'
import { apiGetTodayPicks } from '@/lib/betting-api'
import { siteDateKey } from '@/lib/dates'

export const revalidate = 60

export async function GET() {
  try {
    const today = siteDateKey()
    // Today's picks are the paid surface (lib/access.ts). Reading the viewer makes this route per-request, not cached.
    const { bets, locked } = limitBets(await apiGetTodayPicks(), await getViewer(), today)
    return NextResponse.json({ picks: bets, count: bets.length, locked, date: today })
  } catch (err) {
    console.error('/api/picks/today error:', err)
    return NextResponse.json({ error: 'Failed to fetch picks' }, { status: 500 })
  }
}
