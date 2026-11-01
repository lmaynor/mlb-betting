/** @jest-environment node */
import { NextRequest } from 'next/server'

const getViewerMock = jest.fn()
jest.mock('@/lib/auth', () => ({ getViewer: () => getViewerMock() }))

const upstream = {
  sport: 'nba', status: 'live', engine: 'e', as_of: null, age_minutes: 1, stale: false, count: 8, markets: [], games: [],
  priced_games: [],
  rows: Array.from({ length: 8 }, (_, i) => ({ sport: 'nba', system: 'NBA_SPREAD', side: 'home', line: -1, game_key: `g${i}`, n_books: i })),
}

async function call(qs: string) {
  jest.resetModules()
  process.env.BETTING_API_URL = 'https://api.example.test'
  process.env.BETTING_API_KEY = 'server-secret'
  process.env.ENFORCE_TIERS = '1'
  const { GET } = await import('@/app/api/games/projections/route')
  return GET(new NextRequest(`http://localhost/api/games/projections?${qs}`))
}

describe('GET /api/games/projections', () => {
  let fetchMock: jest.Mock
  beforeEach(() => {
    fetchMock = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => upstream })
    global.fetch = fetchMock as unknown as typeof fetch
  })

  it('rejects an invalid query before anything is fetched', async () => {
    for (const qs of ['', 'sport=mlb', 'sport=nba&day=nope', 'sport=nba&limit=5000', 'sport=nba&limit=0']) {
      const res = await call(qs)
      expect(res.status).toBe(400)
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('forwards only validated params, with the server-side key, and never echoes the key', async () => {
    getViewerMock.mockResolvedValue({ tier: 'pro_monthly', sport: null })
    const res = await call('sport=nba&day=2026-10-31&limit=100&api_key=steal&x=1')
    expect(res.status).toBe(200)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('https://api.example.test/api/public/games/projections?sport=nba&day=2026-10-31&limit=100')
    expect(init.headers['X-API-Key']).toBe('server-secret')
    expect(JSON.stringify(await res.json())).not.toContain('server-secret')
  })

  it('cuts the feed for a free viewer and leaves Pro whole', async () => {
    getViewerMock.mockResolvedValue({ tier: 'public', sport: null })
    const free = await (await call('sport=nba')).json()
    expect(free.rows).toHaveLength(5)
    expect(free.locked).toBe(3)
    getViewerMock.mockResolvedValue({ tier: 'pro_monthly', sport: null })
    const pro = await (await call('sport=nba')).json()
    expect(pro.rows).toHaveLength(8)
    expect(pro.locked).toBe(0)
  })
})
