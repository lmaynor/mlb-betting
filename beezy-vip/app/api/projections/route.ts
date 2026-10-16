import { NextRequest, NextResponse } from 'next/server'
import { limitRows } from '@/lib/access'
import { getViewer } from '@/lib/auth'
import { isSportSlug } from '@/lib/sports'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

// Client-callable proxy for the daily NFL / NHL / NBA projection board (keeps BETTING_API_KEY server-side).
// The board is the paid surface (lib/access.ts): this public proxy cuts it the same way the hub page does.
export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const qs = req.nextUrl.searchParams.toString()
  try {
    const res = await fetch(`${API_URL}/api/public/projections${qs ? `?${qs}` : ''}`, {
      headers: { 'X-API-Key': API_KEY },
      cache: 'no-store',
    })
    const data = await res.json()
    const sport = req.nextUrl.searchParams.get('sport') ?? ''
    if (res.ok && Array.isArray(data?.rows) && isSportSlug(sport)) {
      const cut = limitRows<{ beezy_score?: number }>(data.rows, await getViewer(), 'player_board', sport, r => r.beezy_score ?? 0)
      return NextResponse.json({ ...data, rows: cut.rows, count: cut.rows.length, locked: cut.locked })
    }
    return NextResponse.json(data, { status: res.ok ? 200 : res.status })
  } catch {
    return NextResponse.json({ error: 'projections unavailable' }, { status: 502 })
  }
}
