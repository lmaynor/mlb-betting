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

async function call<T>(path: string, userId: string, init: { method: 'GET' | 'POST'; body?: unknown }): Promise<BackendResult<T>> {
  const base = API_URL()
  if (!base) return { status: 503, body: { error: { code: 'NO_BACKEND', message: 'the contest service is not configured' } } as T }
  try {
    const res = await fetch(`${base}${path}`, {
      method: init.method, cache: 'no-store', signal: AbortSignal.timeout(8000),
      headers: { 'X-API-Key': API_KEY(), 'X-Contest-User': userId, ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
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
