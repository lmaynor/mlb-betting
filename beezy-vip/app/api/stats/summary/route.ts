import { NextResponse } from 'next/server'
import { apiGetStats } from '@/lib/betting-api'

export const revalidate = 300

export async function GET() {
  try {
    const stats = await apiGetStats()
    // Anonymous season aggregates (no viewer is read here): let the CDN share the response for the same
    // 300s window the upstream fetch already uses, with SWR covering the refresh (as on /api/stats).
    return NextResponse.json(stats, { headers: { 'Cache-Control': 'public, s-maxage=300, stale-while-revalidate=600' } })
  } catch (err) {
    console.error('/api/stats/summary error:', err)
    return NextResponse.json({ error: 'Failed to fetch stats' }, { status: 500 })
  }
}
