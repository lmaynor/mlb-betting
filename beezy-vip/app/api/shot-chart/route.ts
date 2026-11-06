import { NextRequest, NextResponse } from 'next/server'
import { canViewFull } from '@/lib/access'
import { getViewer } from '@/lib/auth'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

// Client-callable proxy for the /edge NBA shot chart (keeps BETTING_API_KEY server-side). Shot charts are the depth of NBA
// prop research, so they follow the cockpit access policy (lib/access.ts): other viewers get { status: 'locked' }.
export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  if (!canViewFull(await getViewer(), 'cockpit', 'nba')) return NextResponse.json({ status: 'locked' })
  const qs = req.nextUrl.searchParams.toString()
  try {
    const res = await fetch(`${API_URL}/api/public/edge/shotchart${qs ? `?${qs}` : ''}`, {
      headers: { 'X-API-Key': API_KEY },
      // Shot charts are historical aggregates: share one upstream fetch for 300s (the window
      // lib/betting-api.ts uses server-side). The access gate above still runs per request.
      next: { revalidate: 300 },
    })
    return NextResponse.json(await res.json(), { status: res.ok ? 200 : res.status })
  } catch {
    return NextResponse.json({ error: 'shot chart unavailable' }, { status: 502 })
  }
}
