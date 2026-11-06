import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const res = await fetch(`${API_URL}/api/public/stats/summary`, {
    headers: { 'X-API-Key': API_KEY },
    next: { revalidate: 300 },
  })
  const data = await res.json()
  // Anonymous season aggregates (no viewer is read here): let the CDN share the response for the same
  // 300s window the upstream fetch already uses, with SWR covering the refresh.
  return NextResponse.json(data, { headers: { 'Cache-Control': 'public, s-maxage=300, stale-while-revalidate=600' } })
}
