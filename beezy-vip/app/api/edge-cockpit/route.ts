import { NextRequest, NextResponse } from 'next/server'
import { limitCockpit } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import { withHeadshotIds } from '@/lib/cockpit-headshots'
import { loadCockpit } from '@/lib/cockpit-loader'

const API_URL = process.env.BETTING_API_URL ?? ''

// Client-callable proxy for the /edge multi-sport prop cockpit (keeps BETTING_API_KEY server-side).
export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const sp = req.nextUrl.searchParams
  try {
    const feed = await loadCockpit({
      sport: sp.get('sport') ?? undefined,
      limit: sp.get('limit') ? Number(sp.get('limit')) : undefined,
      minEv: sp.get('min_ev') ? Number(sp.get('min_ev')) : undefined,
    })
    if (!feed) return NextResponse.json({ error: 'cockpit unavailable' }, { status: 502 })
    // Prop-research rows are paid per league (lib/access.ts); cut here so the proxy matches the page.
    const cut = limitCockpit(feed.rows, await getViewer())
    return NextResponse.json({ ...feed, rows: withHeadshotIds(cut.rows), locked: cut.locked })
  } catch {
    return NextResponse.json({ error: 'cockpit unavailable' }, { status: 502 })
  }
}
