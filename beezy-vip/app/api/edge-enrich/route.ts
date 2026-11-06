import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

// Client-callable proxy for The Edge enrichment. Supports ?date= and an
// optional ?player= for lazy, per-player fetches when a pick is selected.
export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const date = req.nextUrl.searchParams.get('date') ?? ''
  const player = req.nextUrl.searchParams.get('player') ?? ''
  const qs = new URLSearchParams({ date })
  if (player) qs.set('player', player)
  try {
    const res = await fetch(`${API_URL}/api/public/edge-enrich?${qs.toString()}`, {
      headers: { 'X-API-Key': API_KEY },
      // Enrichment (season stats, recent form, weather) is slow-moving: share one upstream fetch for 300s
      // (the window lib/betting-api.ts uses server-side for this endpoint).
      next: { revalidate: 300 },
    })
    if (!res.ok) return NextResponse.json({ date, players: {} })
    return NextResponse.json(await res.json())
  } catch {
    return NextResponse.json({ date, players: {} })
  }
}
