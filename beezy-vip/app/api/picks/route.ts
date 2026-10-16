import { NextRequest, NextResponse } from 'next/server'
import { limitBets } from '@/lib/access-bets'
import { getViewer } from '@/lib/auth'
import { siteDateKey } from '@/lib/dates'
import type { Bet } from '@/lib/types'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

export async function GET(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const qs = req.nextUrl.searchParams.toString()
  const url = `${API_URL}/api/public/picks${qs ? `?${qs}` : ''}`
  const res = await fetch(url, {
    headers: { 'X-API-Key': API_KEY },
    cache: 'no-store',
  })
  const data = await res.json()
  // Today's picks are the paid surface (lib/access.ts); this public proxy must not hand out what the pages withhold.
  if (Array.isArray(data?.picks)) {
    const { bets, locked } = limitBets(data.picks as Bet[], await getViewer(), siteDateKey())
    return NextResponse.json({ ...data, picks: bets, count: bets.length, locked })
  }
  return NextResponse.json(data)
}
