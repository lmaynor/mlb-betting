/**
 * Organiser proxy: GET .../admin/state returns the console's data, POST .../admin/<op> runs an organiser action (rename, adjust,
 * void-game, settle-game, invite, status). Only a signed-in user whose Clerk private metadata marks them an organiser of THIS contest
 * gets through; the Cloud Run service then trusts the header this route sets. OFF (404) unless CONTEST_ENABLED is on.
 */
import { NextResponse } from 'next/server'
import { fetchAdmin, postAdmin } from '@/lib/contest/backend'
import { contestEnabled } from '@/lib/contest/flag'
import { contestUser, isContestAdmin } from '@/lib/contest/identity'

export const dynamic = 'force-dynamic'
type Ctx = { params: Promise<{ slug: string; op: string }> }
const err = (status: number, code: string, message: string) => NextResponse.json({ error: { code, message } }, { status })
const NO_STORE = { 'Cache-Control': 'private, no-store' }

async function organiser(slug: string) {
  if (!contestEnabled()) return { res: err(404, 'OFF', 'the contest is not enabled') }
  const user = await contestUser()
  if (!user) return { res: err(401, 'NO_USER', 'sign in first') }
  if (!(await isContestAdmin(slug))) return { res: err(403, 'FORBIDDEN', 'organisers only') }
  return { user }
}

export async function GET(_req: Request, { params }: Ctx) {
  const { slug, op } = await params
  if (op !== 'state') return err(404, 'NO_ROUTE', 'not found')
  const a = await organiser(slug)
  if (a.res) return a.res
  const r = await fetchAdmin(slug, a.user!.id)
  return NextResponse.json(r.body, { status: r.status, headers: NO_STORE })
}

export async function POST(req: Request, { params }: Ctx) {
  const { slug, op } = await params
  const a = await organiser(slug)
  if (a.res) return a.res
  const body = await req.json().catch(() => null)
  if (!body || typeof body !== 'object') return err(400, 'BAD_REQUEST', 'a JSON object body is required')
  const r = await postAdmin(slug, op, a.user!.id, body)
  return NextResponse.json(r.body, { status: r.status, headers: NO_STORE })
}
