/**
 * Server-side calls to the contest API on the Cloud Run service (contest/api.py). The browser never talks to that service: it calls this
 * site's own /api/contest routes, which authenticate the player with Clerk, then call here with the site API key and the verified user id.
 */
import type { ContestFeed } from './types'

const trim = (v: string) => v.trim().replace(/\/+$/, '')
const API_URL = () => { const v = trim(process.env.BETTING_API_URL ?? ''); return v && !/^https?:\/\//i.test(v) ? `https://${v}` : v }
const API_KEY = () => process.env.BETTING_API_KEY ?? ''

export const ACTIONS = ['place', 'join', 'rename', 'cashout'] as const
export type ContestAction = (typeof ACTIONS)[number]
export const isAction = (x: string): x is ContestAction => (ACTIONS as readonly string[]).includes(x)

export interface BackendResult<T = unknown> { status: number; body: T }

async function call<T>(path: string, userId: string, init: { method: 'GET' | 'POST'; body?: unknown; admin?: boolean }): Promise<BackendResult<T>> {
  const base = API_URL()
  if (!base) return { status: 503, body: { error: { code: 'NO_BACKEND', message: 'the contest service is not configured' } } as T }
  try {
    const res = await fetch(`${base}${path}`, {
      method: init.method, cache: 'no-store', signal: AbortSignal.timeout(8000),
      headers: { 'X-API-Key': API_KEY(), 'X-Contest-User': userId, ...(init.admin ? { 'X-Contest-Admin': '1' } : {}), ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    })
    return { status: res.status, body: (await res.json().catch(() => ({}))) as T }
  } catch {
    return { status: 503, body: { error: { code: 'UNREACHABLE', message: 'the contest service did not answer' } } as T }
  }
}

export const fetchFeed = (slug: string, userId: string, suggestedName = '') =>
  call<ContestFeed>(`/api/contest/${encodeURIComponent(slug)}/feed${suggestedName ? `?name=${encodeURIComponent(suggestedName)}` : ''}`, userId, { method: 'GET' })

export const postAction = (slug: string, action: ContestAction, userId: string, body: unknown) =>
  call<ContestFeed>(`/api/contest/${encodeURIComponent(slug)}/${action}`, userId, { method: 'POST', body })

/** Organiser calls. The caller must already have checked the Clerk organiser role: this sets the header the service trusts. */
export const fetchAdmin = (slug: string, userId: string) => call<AdminState>(`/api/contest/${encodeURIComponent(slug)}/admin`, userId, { method: 'GET', admin: true })
export const postAdmin = (slug: string, op: string, userId: string, body: unknown) =>
  call<AdminState>(`/api/contest/${encodeURIComponent(slug)}/admin/${encodeURIComponent(op)}`, userId, { method: 'POST', body, admin: true })

export interface AdminState {
  name: string
  status: 'open' | 'closed'
  /** On / off switches in the contest's rules (contest/store.py RULE_SWITCHES). */
  switches: Record<string, boolean>
  entries: { entry_id: number; user_id: string; display_name: string; balance_cents: number; status: string; name_changes: number; joined_at: string }[]
  invites: { code: string; max_uses: number; uses: number }[]
  games: { id: number; away: string; home: string; commence: string; status: string; homeScore: number | null; awayScore: number | null }[]
  ledgerMismatches: unknown[]
  log: { ts: string; actor: string; action: string; detail: Record<string, unknown> }[]
}
