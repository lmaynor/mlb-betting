import { NextRequest, NextResponse } from 'next/server'
import { limitGameOdds } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import { gameOddsQueryString, parseGameOddsQuery, type GameOddsFeed } from '@/lib/game-odds'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

// Client-callable proxy for the NBA / NHL game-line price board (keeps BETTING_API_KEY server-side). Only a validated query
// (sport nba|nhl, day YYYY-MM-DD, market ml|spread|total, limit 1..5000) is forwarded, and the response is cut per viewer
// exactly as the /ev/games page cuts it (lib/access-feeds.ts limitGameOdds): the Odds Terminal's Pro rule.
export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const parsed = parseGameOddsQuery(req.nextUrl.searchParams)
  if (!parsed.ok) return NextResponse.json({ error: parsed.error }, { status: 400 })
  try {
    const res = await fetch(`${API_URL}/api/public/games/odds?${gameOddsQueryString(parsed.query)}`, {
      headers: { 'X-API-Key': API_KEY },
      // one upstream fetch is shared across viewers; the per-viewer cut below still runs
      next: { revalidate: 60 },
    })
    const data = await res.json()
    if (res.ok && data && Array.isArray(data.rows)) {
      const cut = limitGameOdds(data as GameOddsFeed, await getViewer())
      return NextResponse.json({ ...cut.feed, locked: cut.locked })
    }
    // never pass a 200 body through uncut: a payload without rows is malformed, not publishable
    if (res.ok) return NextResponse.json({ error: 'game odds unavailable' }, { status: 502 })
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'game odds unavailable' }, { status: 502 })
  }
}
