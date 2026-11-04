/** @jest-environment node */
import { NextRequest } from 'next/server'
import { limitGameOdds } from '../lib/access-feeds'
import { FREE_PREVIEW_ROWS, type Viewer } from '../lib/access'
import type { GameOddsFeed, GameOddsRow } from '../lib/game-odds'

const getViewerMock = jest.fn()
jest.mock('@/lib/auth', () => ({ getViewer: () => getViewerMock() }))

const ON = { ENFORCE_TIERS: '1' }
const free: Viewer = { tier: 'public', sport: null }
const nbaPass: Viewer = { tier: 'single_sport', sport: 'nba' }
const pro: Viewer = { tier: 'pro_monthly', sport: null }

// 8 rows over 4 games; row i is quoted by i books, so the best covered rows are the highest i
const rows: GameOddsRow[] = Array.from({ length: 8 }, (_, i) => ({
  key: `g${i % 4}|ml|${i}`, sport: 'nba', game_key: `g${i % 4}`, away: 'BOS', home: 'MIA', commence: '2026-11-03T00:30:00Z', market: 'ml' as const, line: null,
  books: Array.from({ length: i }, (_, j) => ({ book: `b${j}`, a: -110, b: -110 })),
}))
const feed = (): GameOddsFeed => ({
  sport: 'nba', status: 'live', as_of: '2026-11-02T20:00:00Z', age_minutes: 4, stale: false, validated: false, lines_source: 'parlayapi', count: rows.length,
  books: ['b0'], offshore_books: [], rows,
  games: [0, 1, 2, 3].map(i => ({ game_key: `g${i}`, away: 'BOS', home: 'MIA', start: '2026-11-03T00:30:00Z', n_rows: 2 })),
})

describe('limitGameOdds', () => {
  it('Pro sees the whole feed', () => {
    const f = feed()
    const out = limitGameOdds(f, pro, ON)
    expect(out.feed).toBe(f)
    expect(out.locked).toBe(0)
  })
  it('everyone else gets the best-covered rows only (a single-sport pass is cut like a free viewer), and `games` is cut with them', () => {
    for (const v of [free, nbaPass]) {
      const out = limitGameOdds(feed(), v, ON)
      expect(out.feed.rows).toHaveLength(FREE_PREVIEW_ROWS.ev_terminal)
      expect(out.feed.count).toBe(FREE_PREVIEW_ROWS.ev_terminal)
      expect(out.locked).toBe(rows.length - FREE_PREVIEW_ROWS.ev_terminal)
      // the five most-covered rows are i = 3..7, in games g3, g0, g1, g2, g3
      expect(out.feed.rows.map(r => r.books.length).sort()).toEqual([3, 4, 5, 6, 7])
      const kept = new Set(out.feed.rows.map(r => r.game_key))
      expect(out.feed.games.every(g => kept.has(g.game_key))).toBe(true)
      expect(out.feed.games.reduce((n, g) => n + g.n_rows, 0)).toBe(FREE_PREVIEW_ROWS.ev_terminal)
    }
  })
  it('movement of a game whose prices were withheld is cut with it', () => {
    const f = feed()
    const pts = [{ t: '2026-11-03T14:00:00Z', line: null, p_a: 0.5, best_a: -110, best_b: -110, n: 3 }, { t: '2026-11-03T15:00:00Z', line: null, p_a: 0.52, best_a: -110, best_b: -110, n: 3 }]
    f.history = Object.fromEntries([0, 1, 2, 3].map(i => [`g${i}|ml`, { points: pts }]))
    const out = limitGameOdds(f, free, ON)
    const kept = new Set(out.feed.rows.map(r => r.game_key))
    expect(Object.keys(out.feed.history ?? {}).sort()).toEqual([...kept].map(k => `${k}|ml`).sort())
    expect(limitGameOdds(f, pro, ON).feed.history).toBe(f.history)
  })
  it('drops a game whose rows were all withheld', () => {
    const f = feed()
    f.rows = f.rows.filter(r => r.game_key !== 'g0')   // g0 stays in `games` but has no rows left to show
    const out = limitGameOdds(f, free, ON)
    expect(out.feed.games.find(g => g.game_key === 'g0')).toBeUndefined()
  })
})

async function call(qs: string) {
  jest.resetModules()
  process.env.BETTING_API_URL = 'https://api.example.test'
  process.env.BETTING_API_KEY = 'server-secret'
  process.env.ENFORCE_TIERS = '1'
  const { GET } = await import('@/app/api/games/odds/route')
  return GET(new NextRequest(`http://localhost/api/games/odds?${qs}`))
}

describe('GET /api/games/odds', () => {
  let fetchMock: jest.Mock
  beforeEach(() => {
    fetchMock = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => feed() })
    global.fetch = fetchMock as unknown as typeof fetch
  })

  it('rejects an invalid query before anything is fetched', async () => {
    for (const qs of ['', 'sport=mlb', 'sport=nfl', 'sport=nba&day=nope', 'sport=nba&market=prop', 'sport=nba&limit=0', 'sport=nba&limit=9999']) {
      const res = await call(qs)
      expect(res.status).toBe(400)
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('forwards only validated params, with the server-side key, and never echoes the key', async () => {
    getViewerMock.mockResolvedValue(pro)
    const res = await call('sport=nba&day=2026-11-02&market=ml&limit=100&api_key=steal&x=1')
    expect(res.status).toBe(200)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('https://api.example.test/api/public/games/odds?sport=nba&day=2026-11-02&market=ml&limit=100')
    expect(init.headers['X-API-Key']).toBe('server-secret')
    expect(JSON.stringify(await res.json())).not.toContain('server-secret')
  })

  it('cuts the feed for a free viewer and leaves Pro whole', async () => {
    getViewerMock.mockResolvedValue(free)
    const cut = await (await call('sport=nba')).json()
    expect(cut.rows).toHaveLength(FREE_PREVIEW_ROWS.ev_terminal)
    expect(cut.locked).toBe(3)
    expect(cut.games.length).toBeLessThanOrEqual(4)
    getViewerMock.mockResolvedValue(pro)
    const whole = await (await call('sport=nba')).json()
    expect(whole.rows).toHaveLength(8)
    expect(whole.locked).toBe(0)
  })

  it('never passes a malformed 200 body through uncut, and reports an upstream failure', async () => {
    getViewerMock.mockResolvedValue(free)
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ secret: 'all rows', rows: 'nope' }) })
    expect((await call('sport=nba')).status).toBe(502)
    fetchMock.mockRejectedValueOnce(new Error('boom'))
    expect((await call('sport=nba')).status).toBe(502)
    fetchMock.mockResolvedValueOnce({ ok: false, status: 404, json: async () => ({ error: 'nope' }) })
    expect((await call('sport=nba')).status).toBe(404)
  })
})
