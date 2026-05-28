import { NextResponse } from 'next/server'
import { apiGetTodayPicks } from '@/lib/betting-api'
import { siteDateKey } from '@/lib/dates'

export const revalidate = 60

export async function GET() {
  try {
    const picks = await apiGetTodayPicks()
    return NextResponse.json({ picks, count: picks.length, date: siteDateKey() })
  } catch (err) {
    console.error('/api/picks/today error:', err)
    return NextResponse.json({ error: 'Failed to fetch picks' }, { status: 500 })
  }
}
