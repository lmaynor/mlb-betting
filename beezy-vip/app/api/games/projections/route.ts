import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

// Client-callable proxy for daily game-line quantitative projections (keeps BETTING_API_KEY server-side).
export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const qs = req.nextUrl.searchParams.toString()
  try {
    const res = await fetch(`${API_URL}/api/public/games/projections${qs ? `?${qs}` : ''}`, {
      headers: { 'X-API-Key': API_KEY },
      next: { revalidate: 60 },
    })
    const data = await res.json()
    return NextResponse.json(data, { status: res.ok ? 200 : res.status })
  } catch {
    return NextResponse.json({ error: 'game projections unavailable' }, { status: 502 })
  }
}
