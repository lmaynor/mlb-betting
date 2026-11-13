/** @jest-environment node */
/**
 * Fluid Active CPU guardrails: the browser-facing proxy routes must share their upstream Cloud Run fetch
 * through the Next Data Cache (never `cache: 'no-store'`), and the anonymous, parameter-only routes must
 * let the CDN share the response itself. The windows mirror lib/betting-api.ts's server-side fetchers for
 * the same endpoints (60s live boards, 300s aggregates). If a route regresses to no-store, this fails.
 */
import { NextRequest } from 'next/server'
import type { Viewer } from '../lib/access'

const getViewerMock = jest.fn()
const getUserIdMock = jest.fn()
jest.mock('@/lib/auth', () => ({
  getViewer: () => getViewerMock(),
  getUserId: () => getUserIdMock(),
}))

const free: Viewer = { tier: 'public', sport: null }

let fetchMock: jest.Mock
beforeEach(() => {
  jest.resetModules()
  process.env.BETTING_API_URL = 'https://api.example.test'
  process.env.BETTING_API_KEY = 'server-secret'
  delete process.env.ENFORCE_TIERS
  getViewerMock.mockResolvedValue(free)
  getUserIdMock.mockResolvedValue('user_1')
  fetchMock = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) })
  global.fetch = fetchMock as unknown as typeof fetch
})

async function call(routePath: string, url: string, payload: unknown = {}) {
  fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => payload })
  const mod = await import(routePath)
  return mod.GET(new NextRequest(url))
}

function upstreamInit() {
  expect(fetchMock).toHaveBeenCalledTimes(1)
  return fetchMock.mock.calls[0][1] as { cache?: string; next?: { revalidate?: number } }
}

describe('live-board proxies share the upstream fetch for 60s (viewer cut still runs per request)', () => {
  it('/api/picks', async () => {
    const res = await call('@/app/api/picks/route', 'http://localhost/api/picks?sport=all', { picks: [] })
    expect(res.status).toBe(200)
    const init = upstreamInit()
    expect(init.cache).toBeUndefined()
    expect(init.next?.revalidate).toBe(60)
  })

  it('/api/ev', async () => {
    const res = await call('@/app/api/ev/route', 'http://localhost/api/ev?sport=all', {})
    expect(res.status).toBe(200)
    const init = upstreamInit()
    expect(init.cache).toBeUndefined()
    expect(init.next?.revalidate).toBe(60)
  })

  it('/api/projections', async () => {
    const res = await call('@/app/api/projections/route', 'http://localhost/api/projections?sport=nba', { rows: [] })
    expect(res.status).toBe(200)
    const init = upstreamInit()
    expect(init.cache).toBeUndefined()
    expect(init.next?.revalidate).toBe(60)
  })
})

describe('aggregate proxies share the upstream fetch for 300s', () => {
  it('/api/trends (access gate still runs first)', async () => {
    const res = await call('@/app/api/trends/route', 'http://localhost/api/trends?player=x&stat=points', { ok: 1 })
    expect(res.status).toBe(200)
    expect(upstreamInit().next?.revalidate).toBe(300)
  })

  it('/api/shot-chart (access gate still runs first)', async () => {
    const res = await call('@/app/api/shot-chart/route', 'http://localhost/api/shot-chart?player=x', { ok: 1 })
    expect(res.status).toBe(200)
    expect(upstreamInit().next?.revalidate).toBe(300)
  })

  it('/api/edge-enrich', async () => {
    const res = await call('@/app/api/edge-enrich/route', 'http://localhost/api/edge-enrich?date=2026-11-02', { date: '2026-11-02', players: {} })
    expect(res.status).toBe(200)
    expect(upstreamInit().next?.revalidate).toBe(300)
  })

  it('/api/stats/clv (login gate still runs first; response is not CDN-shared)', async () => {
    const res = await call('@/app/api/stats/clv/route', 'http://localhost/api/stats/clv?days=90', { data: [] })
    expect(res.status).toBe(200)
    expect(upstreamInit().next?.revalidate).toBe(300)
    expect(res.headers.get('cache-control') ?? '').not.toContain('s-maxage')
  })

  it('/api/slate/today (login gate still runs first; response is not CDN-shared)', async () => {
    const res = await call('@/app/api/slate/today/route', 'http://localhost/api/slate/today', { games: [] })
    expect(res.status).toBe(200)
    expect(upstreamInit().next?.revalidate).toBe(300)
    expect(res.headers.get('cache-control') ?? '').not.toContain('s-maxage')
  })
})

describe('anonymous parameter-only routes are CDN-shared (s-maxage + SWR)', () => {
  it('/api/sparkline', async () => {
    const res = await call('@/app/api/sparkline/route', 'http://localhost/api/sparkline?days=30', { sparkline: [] })
    expect(res.status).toBe(200)
    expect(upstreamInit().next?.revalidate).toBe(300)
    expect(res.headers.get('cache-control')).toContain('s-maxage=300')
    expect(res.headers.get('cache-control')).toContain('stale-while-revalidate=600')
  })

  it('/api/stats', async () => {
    const res = await call('@/app/api/stats/route', 'http://localhost/api/stats', { overall: {} })
    expect(res.status).toBe(200)
    expect(res.headers.get('cache-control')).toContain('s-maxage=300')
  })

  it('/api/stats/summary', async () => {
    const res = await call('@/app/api/stats/summary/route', 'http://localhost/api/stats/summary', { overall: {}, bySystem: [] })
    expect(res.status).toBe(200)
    expect(res.headers.get('cache-control')).toContain('s-maxage=300')
  })

  it('/api/analytics', async () => {
    const res = await call('@/app/api/analytics/route', 'http://localhost/api/analytics', { deep: true })
    expect(res.status).toBe(200)
    expect(upstreamInit().next?.revalidate).toBe(300)
    expect(res.headers.get('cache-control')).toContain('s-maxage=300')
  })
})
