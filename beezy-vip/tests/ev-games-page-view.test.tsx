import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import GameLinesPage from '@/app/ev/games/page'
import type { GameOddsFeed } from '@/lib/game-odds'

const getViewerMock = jest.fn()
const oddsMock = jest.fn()
const projMock = jest.fn()
jest.mock('@/lib/auth', () => ({ getViewer: () => getViewerMock() }))
jest.mock('@/lib/betting-api', () => ({
  apiGetGameOddsBoard: (...a: unknown[]) => oddsMock(...a),
  apiGetGameProjections: (...a: unknown[]) => projMock(...a),
}))

const q = (book: string, a: number, b: number) => ({ book, a, b })
const flat = [q('draftkings', -110, -110), q('fanduel', -110, -110), q('betmgm', -110, -110), q('caesars', 105, -125)]
const oddsFeed = (sport: string): GameOddsFeed => ({
  sport, status: 'live', as_of: null, age_minutes: 3, stale: false, validated: false, lines_source: 'parlayapi', count: 8,
  books: ['draftkings', 'fanduel', 'betmgm', 'caesars'], offshore_books: [],
  games: [{ game_key: `${sport}:g`, away: 'BOS', home: 'MIA', start: '2026-11-03T00:30:00Z', n_rows: 8 }],
  rows: Array.from({ length: 8 }, (_, i) => ({ key: `${sport}-${i}`, sport, game_key: `${sport}:g`, away: 'BOS', home: 'MIA', commence: '2026-11-03T00:30:00Z', market: 'total' as const, line: 220 + i, books: flat })),
})

async function page(sp: Record<string, string>) {
  return GameLinesPage({ searchParams: Promise.resolve(sp) })
}

beforeEach(() => {
  process.env.ENFORCE_TIERS = '1'
  getViewerMock.mockReset().mockResolvedValue({ tier: 'pro_monthly', sport: null })
  oddsMock.mockReset().mockImplementation(async (sport: string) => oddsFeed(sport))
  projMock.mockReset().mockResolvedValue(null)
})

describe('/ev/games view and sport routing (server-side)', () => {
  it('defaults to the GAMES board: fetches both odds feeds and both model feeds', async () => {
    render(await page({}))
    expect(oddsMock.mock.calls.map(c => c[0]).sort()).toEqual(['nba', 'nhl'])
    expect(projMock.mock.calls.map(c => c[0]).sort()).toEqual(['nba', 'nhl'])
    expect(screen.getByTestId('game-board-strip')).toBeInTheDocument()
    expect(screen.getAllByTestId('game-card').length).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: 'GAMES' })).toHaveAttribute('aria-current', 'page')
  })
  it('the retired MODEL view lands on the games board', async () => {
    render(await page({ view: 'model', sport: 'nhl' }))
    expect(screen.getByTestId('game-board-strip')).toBeInTheDocument()
  })
  it('an unreachable feed degrades to the unavailable state', async () => {
    oddsMock.mockRejectedValue(new Error('down'))
    render(await page({}))
    expect(screen.getByRole('status').textContent).toMatch(/Game-line prices are not published yet/)
  })
  it('TABLE keeps the all-rows screen and ?sport narrows its fetch', async () => {
    render(await page({ view: 'table', sport: 'nba' }))
    expect(oddsMock.mock.calls.map(c => c[0])).toEqual(['nba'])
    expect(projMock).not.toHaveBeenCalled()
    expect(screen.getByTestId('game-prices-strip')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'TABLE' })).toHaveAttribute('aria-current', 'page')
  })
  it('cuts the price feed on the server for a free viewer before it reaches the client', async () => {
    getViewerMock.mockResolvedValue({ tier: 'public', sport: null })
    render(await page({ view: 'table', sport: 'nba' }))
    expect(screen.getByTestId('game-prices-strip').textContent).toMatch(/5 ROWS/)
    expect(screen.getByRole('note').textContent).toMatch(/3/)
  })
})
