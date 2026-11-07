import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.BETTING_API_URL ?? ''
const API_KEY = process.env.BETTING_API_KEY ?? ''

// Pragmatic shape check, repeated server-side by the backend. This first
// pass keeps junk off the wire; the submitted address is never logged here.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/

export async function POST(req: NextRequest) {
  if (!API_URL) return NextResponse.json({ error: 'API not configured' }, { status: 500 })

  let body: unknown
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Enter a valid email address' }, { status: 400 })
  }

  const rawEmail = (body as { email?: unknown } | null)?.email
  const email = typeof rawEmail === 'string' ? rawEmail.trim().toLowerCase() : ''
  if (email.length > 254 || !EMAIL_RE.test(email)) {
    return NextResponse.json({ error: 'Enter a valid email address' }, { status: 400 })
  }

  const rawSource = (body as { source?: unknown } | null)?.source
  const source = typeof rawSource === 'string' ? rawSource.slice(0, 64) : undefined

  const res = await fetch(`${API_URL}/api/public/waitlist`, {
    method: 'POST',
    headers: { 'X-API-Key': API_KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, source }),
    cache: 'no-store',
  })
  const data = await res.json().catch(() => ({}))
  return NextResponse.json(data, { status: res.ok ? 200 : res.status })
}
