import { NextRequest, NextResponse } from 'next/server'
import { limitOddsBoard } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import type { OddsBoard } from '@/lib/odds-screen'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

// Client-callable proxy for the /ev/odds price matrix (keeps BETTING_API_KEY server-side). The screen is the +EV Terminal's
// table view, so this public proxy cuts it exactly as the page does (lib/access-feeds.ts limitOddsBoard).
export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const qs = req.nextUrl.searchParams.toString()
  try {
    const res = await fetch(`${API_URL}/api/public/odds/board${qs ? `?${qs}` : ''}`, {
      headers: { 'X-API-Key': API_KEY },
      // the board changes every 30 minutes: share ONE upstream fetch across viewers (the per-viewer cut below still runs)
      next: { revalidate: 60 },
    })
    const data = await res.json()
    if (res.ok && Array.isArray(data?.rows)) {
      const cut = limitOddsBoard(data as OddsBoard, await getViewer())
      return NextResponse.json({ ...cut.board, locked: cut.locked })
    }
    return NextResponse.json(data, { status: res.ok ? 200 : res.status })
  } catch {
    return NextResponse.json({ error: 'odds board unavailable' }, { status: 502 })
  }
}
