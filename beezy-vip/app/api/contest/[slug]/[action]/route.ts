/**
 * Proxy from the browser to the contest API. GET .../feed returns the player's feed; POST .../place | join | rename | cashout acts for
 * them. The player is whoever Clerk says is signed in: no user id, price or clock is ever taken from the request. OFF (404) unless
 * CONTEST_ENABLED is on. A successful join also records the participant role in Clerk (lib/contest/access.ts); that is a mirror, so a
 * failure to write it is logged and never fails the join.
 */
import { NextResponse } from 'next/server'
import { clerkClient } from '@clerk/nextjs/server'
import { AUTH_DISABLED } from '@/lib/auth-mode'
import { grantContestRole } from '@/lib/contest/access'
import { fetchFeed, isAction, postAction } from '@/lib/contest/backend'
import { contestEnabled } from '@/lib/contest/flag'
import { contestUser } from '@/lib/contest/identity'

export const dynamic = 'force-dynamic'
type Ctx = { params: Promise<{ slug: string; action: string }> }

const off = () => NextResponse.json({ error: { code: 'OFF', message: 'the contest is not enabled' } }, { status: 404 })
const signIn = () => NextResponse.json({ error: { code: 'NO_USER', message: 'sign in first' } }, { status: 401 })

export async function GET(req: Request, { params }: Ctx) {
  if (!contestEnabled()) return off()
  const { slug, action } = await params
  if (action !== 'feed') return NextResponse.json({ error: { code: 'NO_ROUTE', message: 'not found' } }, { status: 404 })
  const user = await contestUser()
  if (!user) return signIn()
  const r = await fetchFeed(slug, user.id, user.suggestedName)
  return NextResponse.json(r.body, { status: r.status, headers: { 'Cache-Control': 'private, no-store' } })
}

export async function POST(req: Request, { params }: Ctx) {
  if (!contestEnabled()) return off()
  const { slug, action } = await params
  if (!isAction(action)) return NextResponse.json({ error: { code: 'NO_ROUTE', message: 'not found' } }, { status: 404 })
  const user = await contestUser()
  if (!user) return signIn()
  const body = await req.json().catch(() => null)
  if (!body || typeof body !== 'object') return NextResponse.json({ error: { code: 'BAD_REQUEST', message: 'a JSON object body is required' } }, { status: 400 })
  const r = await postAction(slug, action, user.id, body)
  if (action === 'join' && r.status === 200 && !AUTH_DISABLED) {
    try { await grantContestRole(await clerkClient(), user.id, slug, 'participant') } catch (e) { console.error('contest role mirror failed', e) }
  }
  return NextResponse.json(r.body, { status: r.status, headers: { 'Cache-Control': 'private, no-store' } })
}
