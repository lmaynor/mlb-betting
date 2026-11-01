import { NextRequest, NextResponse } from 'next/server'
import { limitGameLines } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import { gameQueryString, parseGameProjectionQuery, type GameProjectionsFeed } from '@/lib/game-lines'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

// Client-callable proxy for daily game-line projections (keeps BETTING_API_KEY server-side). Only a validated query
// (sport nfl|nhl|nba, day YYYY-MM-DD, limit 1..1200, market) is forwarded, and the response is cut per viewer exactly as
// the /ev page cuts it (lib/access-feeds.ts limitGameLines).
export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const parsed = parseGameProjectionQuery(req.nextUrl.searchParams)
  if (!parsed.ok) return NextResponse.json({ error: parsed.error }, { status: 400 })
  try {
    const res = await fetch(`${API_URL}/api/public/games/projections?${gameQueryString(parsed.query)}`, {
      headers: { 'X-API-Key': API_KEY },
      // one upstream fetch is shared across viewers; the per-viewer cut below still runs
      next: { revalidate: 60 },
    })
    const data = await res.json()
    if (res.ok && data && Array.isArray(data.rows)) {
      const cut = limitGameLines(data as GameProjectionsFeed, await getViewer())
      return NextResponse.json({ ...cut.feed, locked: cut.locked })
    }
    return NextResponse.json(data, { status: res.ok ? 200 : res.status })
  } catch {
    return NextResponse.json({ error: 'game projections unavailable' }, { status: 502 })
  }
}
