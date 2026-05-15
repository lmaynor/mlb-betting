import { NextResponse } from 'next/server'
import { apiGetStats } from '@/lib/betting-api'

export const revalidate = 300

export async function GET() {
  try {
    const stats = await apiGetStats()
    return NextResponse.json(stats)
  } catch (err) {
    console.error('/api/stats/summary error:', err)
    return NextResponse.json({ error: 'Failed to fetch stats' }, { status: 500 })
  }
}
