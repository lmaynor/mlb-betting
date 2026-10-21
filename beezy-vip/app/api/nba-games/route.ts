import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

// Client-callable proxy for the NBA games board (keeps BETTING_API_KEY server-side). The board is informational and
// unvalidated, so it is not cut by the access policy.
export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const day = req.nextUrl.searchParams.get('day')
  try {
    const res = await fetch(`${API_URL}/api/public/nba-games${day ? `?day=${encodeURIComponent(day)}` : ''}`, {
      headers: { 'X-API-Key': API_KEY },
      cache: 'no-store',
    })
    return NextResponse.json(await res.json(), { status: res.ok ? 200 : res.status })
  } catch {
    return NextResponse.json({ error: 'nba games unavailable' }, { status: 502 })
  }
}
