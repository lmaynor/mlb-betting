/** @jest-environment node */
/**
 * OPS-05 (Vercel Fast Origin Transfer): the live-board proxy routes answer browser polls with an
 * ETag and `Cache-Control: private, no-cache`, so a poll whose body is unchanged since the caller's
 * last fetch comes back 304 with no payload instead of the full board (the NFL odds board is
 * ~900 KB raw and the cockpit polls every 60s). The responses stay per-viewer (the access cut still
 * runs on every request, including revalidations); only the bytes change.
 */
import { NextRequest } from 'next/server'
import type { Viewer } from '../lib/access'

const getViewerMock = jest.fn()
jest.mock('@/lib/auth', () => ({ getViewer: () => getViewerMock() }))
jest.mock('@/lib/cockpit-loader', () => ({
  loadCockpit: async () => ({ rows: [], as_of: '2026-11-14T00:00:00Z' }),
}))

const free: Viewer = { tier: 'public', sport: null }

let fetchMock: jest.Mock
beforeEach(() => {
  jest.resetModules()
  process.env.BETTING_API_URL = 'https://api.example.test'
  process.env.BETTING_API_KEY = 'server-secret'
  delete process.env.ENFORCE_TIERS
  getViewerMock.mockResolvedValue(free)
  fetchMock = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) })
  global.fetch = fetchMock as unknown as typeof fetch
})

async function call(routePath: string, url: string, payload: unknown, ifNoneMatch?: string) {
  fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => payload })
  const mod = await import(routePath)
  const headers = ifNoneMatch ? { 'if-none-match': ifNoneMatch } : undefined
  return mod.GET(new NextRequest(url, { headers }))
}

const boardPayload = { sport: 'nfl', as_of: '2026-11-14T00:00:00Z', rows: [], games: [], books: [], offshore_books: [] }

const cases: [string, string, string, unknown][] = [
  ['/api/odds-board', '@/app/api/odds-board/route', 'http://localhost/api/odds-board?sport=nfl&limit=3000', boardPayload],
  ['/api/games/odds', '@/app/api/games/odds/route', 'http://localhost/api/games/odds?sport=nba&limit=3000', boardPayload],
  ['/api/games/projections', '@/app/api/games/projections/route', 'http://localhost/api/games/projections?sport=nba', boardPayload],
  ['/api/projections', '@/app/api/projections/route', 'http://localhost/api/projections?sport=nba', { rows: [] }],
  ['/api/ev', '@/app/api/ev/route', 'http://localhost/api/ev?sport=all', { mlb: { rows: [] } }],
  ['/api/edge-cockpit', '@/app/api/edge-cockpit/route', 'http://localhost/api/edge-cockpit?limit=60', {}],
]

describe('live-board proxies revalidate by ETag (OPS-05)', () => {
  for (const [name, routePath, url, payload] of cases) {
    it(`${name}: 200 with ETag + private/no-cache, then 304 on a matching If-None-Match`, async () => {
      const first = await call(routePath, url, payload)
      expect(first.status).toBe(200)
      const etag = first.headers.get('etag')
      expect(etag).toBeTruthy()
      expect(first.headers.get('cache-control')).toContain('private')
      expect(first.headers.get('cache-control')).toContain('no-cache')
      expect((await first.text()).length).toBeGreaterThan(0)

      const second = await call(routePath, url, payload, etag!)
      expect(second.status).toBe(304)
      expect(second.headers.get('etag')).toBe(etag)
      expect(await second.text()).toBe('')

      const stale = await call(routePath, url, payload, '"outdated-etag"')
      expect(stale.status).toBe(200)
    })
  }
})
