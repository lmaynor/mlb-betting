import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const qs = req.nextUrl.searchParams.toString()
  const res = await fetch(`${API_URL}/api/public/analytics/deep-dive${qs ? `?${qs}` : ''}`, {
    headers: { 'X-API-Key': API_KEY },
    // Share one upstream fetch for 300s (the window lib/betting-api.ts uses server-side for this endpoint).
    next: { revalidate: 300 },
  })
  const data = await res.json()
  // Anonymous and a pure function of the query string (the same aggregates the ISR /analytics page shows;
  // no viewer is read here), so a successful response may be shared by the CDN for the same 300s window.
  return NextResponse.json(data, {
    status: res.ok ? 200 : res.status,
    headers: res.ok ? { 'Cache-Control': 'public, s-maxage=300, stale-while-revalidate=600' } : undefined,
  })
}
