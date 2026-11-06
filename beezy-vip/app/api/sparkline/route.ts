import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const qs  = req.nextUrl.searchParams.toString()
  const url = `${API_URL}/api/public/stats/sparkline${qs ? `?${qs}` : ''}`
  const res = await fetch(url, {
    headers: { 'X-API-Key': API_KEY },
    // Trailing daily P&L changes only when bets settle: share one upstream fetch for 300s (the window
    // lib/betting-api.ts uses server-side for this endpoint).
    next: { revalidate: 300 },
  })
  const data = await res.json()
  // Anonymous and a pure function of the query string (no viewer is read here), so the CDN may share the
  // response itself: s-maxage matches the 300s data window, SWR serves the last copy while one background
  // revalidation refreshes it. Function invocations collapse to ~1 per window per URL instead of 1 per hit.
  return NextResponse.json(data, { headers: { 'Cache-Control': 'public, s-maxage=300, stale-while-revalidate=600' } })
}
