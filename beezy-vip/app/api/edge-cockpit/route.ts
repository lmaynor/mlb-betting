import { NextRequest, NextResponse } from 'next/server'
import { limitCockpit } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import { withHeadshotIds } from '@/lib/cockpit-headshots'
import type { CockpitRow } from '@/lib/types'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

// Client-callable proxy for the /edge multi-sport prop cockpit (keeps BETTING_API_KEY server-side).
export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const qs = req.nextUrl.searchParams.toString()
  try {
    const res = await fetch(`${API_URL}/api/public/edge/cockpit${qs ? `?${qs}` : ''}`, {
      headers: { 'X-API-Key': API_KEY },
      cache: 'no-store',
    })
    const data = await res.json()
    // Prop-research rows are paid per league (lib/access.ts); cut here so the proxy matches the page.
    if (res.ok && Array.isArray(data?.rows)) {
      const cut = limitCockpit(data.rows as CockpitRow[], await getViewer())
      return NextResponse.json({ ...data, rows: withHeadshotIds(cut.rows), locked: cut.locked })
    }
    return NextResponse.json(data, { status: res.ok ? 200 : res.status })
  } catch {
    return NextResponse.json({ error: 'cockpit unavailable' }, { status: 502 })
  }
}
