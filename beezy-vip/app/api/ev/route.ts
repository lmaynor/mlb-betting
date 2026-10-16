import { NextRequest, NextResponse } from 'next/server'
import { limitEvFeed } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import { siteDateKey } from '@/lib/dates'
import type { EvFeed } from '@/lib/types'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const qs = req.nextUrl.searchParams.toString()
  const res = await fetch(`${API_URL}/api/public/ev/feed${qs ? `?${qs}` : ''}`, {
    headers: { 'X-API-Key': API_KEY },
    cache: 'no-store',
  })
  const data = await res.json()
  // Live rows are the Pro surface (lib/access.ts); this public proxy must not hand out what the page withholds.
  if (res.ok && data?.mlb) {
    const cut = limitEvFeed(data as EvFeed, await getViewer(), siteDateKey())
    return NextResponse.json({ ...cut.feed, locked: cut.locked })
  }
  return NextResponse.json(data, { status: res.ok ? 200 : res.status })
}
