import { NextRequest, NextResponse } from 'next/server'
import { getUserId } from '@/lib/auth'
import { AUTH_DISABLED } from '@/lib/auth-mode'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

export async function GET(_req: NextRequest) {
  // Same login gate as the /tools/slate page: this route is that page's data plane, not a public surface.
  const userId = await getUserId()
  if (!userId && !AUTH_DISABLED) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })
  const res = await fetch(`${API_URL}/api/public/slate/today`, {
    headers: { 'X-API-Key': API_KEY },
    // The slate payload is the same for every signed-in viewer, so the upstream fetch can be shared for
    // 300s (the window lib/betting-api.ts uses server-side). The login gate above still runs on every
    // request and the response itself is never CDN-shared.
    next: { revalidate: 300 },
  })
  const data = await res.json()
  return NextResponse.json(data)
}
