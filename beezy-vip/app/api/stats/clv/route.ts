import { NextRequest, NextResponse } from 'next/server'
import { getUserId } from '@/lib/auth'
import { AUTH_DISABLED } from '@/lib/auth-mode'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

export async function GET(req: NextRequest) {
  // Same login gate as the /tools/clv-tracker page: this route is that page's data plane, not a public surface.
  const userId = await getUserId()
  if (!userId && !AUTH_DISABLED) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const qs  = req.nextUrl.searchParams.toString()
  const url = `${API_URL}/api/public/stats/clv${qs ? `?${qs}` : ''}`
  const res = await fetch(url, {
    headers: { 'X-API-Key': API_KEY },
    cache: 'no-store',
  })
  const data = await res.json()
  return NextResponse.json(data)
}
