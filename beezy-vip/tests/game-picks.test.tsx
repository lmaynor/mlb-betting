import '@testing-library/jest-dom'
import { fireEvent, render, screen } from '@testing-library/react'
import { GamePicksTable } from '@/app/picks/games/game-picks-table'
import GamePicksPage from '@/app/picks/games/page'
import type { GameProjectionsFeed } from '@/lib/game-lines'
import type { GameOddsFeed, GameOddsRow } from '@/lib/game-odds'

const getViewerMock = jest.fn()
const oddsMock = jest.fn()
const projMock = jest.fn()
const offseasonMock = jest.fn()
jest.mock('@/lib/auth', () => ({ getViewer: () => getViewerMock() }))
jest.mock('@/lib/betting-api', () => ({ apiGetGameOddsBoard: (...a: unknown[]) => oddsMock(...a), apiGetGameProjections: (...a: unknown[]) => projMock(...a) }))
jest.mock('@/lib/dates', () => ({ ...jest.requireActual('@/lib/dates'), isMlbOffseason: () => offseasonMock() }))
jest.mock('@/components/picks/sport-board-page', () => ({ SportBoardPage: () => <div data-testid="mlb-tracked-card">MLB tracked card</div> }))

const q = (book: string, a: number, b: number) => ({ book, a, b })
const GK = 'nhl:2026-11-02:DET@FLA'
const SIX = (a: number, b: number) => ['draftkings', 'fanduel', 'betmgm', 'caesars', 'novig', 'kalshi'].map(k => q(k, a, b))
const mk = (market: GameOddsRow['market'], line: number | null, books: GameOddsRow['books']): GameOddsRow =>
  ({ key: `${GK}|${market}|${line ?? ''}`, sport: 'nhl', game_key: GK, away: 'DET', home: 'FLA', commence: '2026-11-03T00:10:00Z', market, line, books })
const oddsFeed: GameOddsFeed = {
  sport: 'nhl', status: 'live', as_of: null, age_minutes: 3, stale: false, validated: false, lines_source: 'parlayapi', count: 3, books: [], offshore_books: [], games: [],
  rows: [mk('ml', null, SIX(120, -140)), mk('spread', -1.5, SIX(-150, 130)), mk('total', 6.5, SIX(-110, -110))],
}
const projFeed: GameProjectionsFeed = {
  sport: 'nhl', status: 'live', as_of: null, age_minutes: 3, stale: false, count: 0, rows: [], games: [], markets: [],
  priced_games: [{ game_key: GK, home_team: 'FLA', away_team: 'DET', game_date: 'd', lambda_h: 3.1, lambda_a: 2.4, markets: [], ladder: { ml: { p_home: 0.7, p_away: 0.3 } } }],
}

describe('GamePicksTable', () => {
  it('lists the model side per game with the model, market fair, gap and best price, and says UNVALIDATED', () => {
    render(<GamePicksTable feeds={{ odds: { nhl: oddsFeed }, proj: { nhl: projFeed } }} />)
    const rows = screen.getAllByTestId('pick-row')
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toMatch(/DET/)
    expect(rows[0].textContent).toMatch(/FLA ML/)
    expect(rows[0].textContent).toMatch(/Unvalidated/)
    expect(rows[0].textContent).toMatch(/70\.0%/)
  })
  it('a row opens the same deep-dive as the game board, and a second click closes it', () => {
    render(<GamePicksTable feeds={{ odds: { nhl: oddsFeed }, proj: { nhl: projFeed } }} />)
    expect(screen.queryByTestId('game-detail')).toBeNull()
    fireEvent.click(screen.getByTestId('pick-row'))
    expect(screen.getByTestId('game-detail')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('pick-row'))
    expect(screen.queryByTestId('game-detail')).toBeNull()
  })
  it('says so when the model has no side above the market, and when no lines are posted', () => {
    const none = render(<GamePicksTable feeds={{ odds: { nhl: oddsFeed }, proj: {} }} />)
    expect(screen.getByRole('status').textContent).toMatch(/no side above the market/i)
    none.unmount()
    render(<GamePicksTable feeds={{ odds: { nhl: null }, proj: {} }} />)
    expect(screen.getByRole('status').textContent).toMatch(/No game lines are posted/i)
  })
})

describe('/picks/games', () => {
  beforeEach(() => {
    getViewerMock.mockReset().mockResolvedValue({ tier: 'pro_monthly', sport: null })
    oddsMock.mockReset().mockResolvedValue(oddsFeed)
    projMock.mockReset().mockResolvedValue(projFeed)
  })
  it('in the winter the page is the model picks alone: no MLB card, no MLB mention', async () => {
    offseasonMock.mockReturnValue(true)
    const { container } = render(await GamePicksPage({ searchParams: Promise.resolve({}) }))
    expect(screen.queryByTestId('mlb-tracked-card')).toBeNull()
    expect(screen.getByRole('heading', { level: 1, name: 'Game Picks' })).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/MLB/)
  })
  it('in season the graded MLB card leads and the model picks follow under a second-level heading', async () => {
    offseasonMock.mockReturnValue(false)
    render(await GamePicksPage({ searchParams: Promise.resolve({}) }))
    expect(screen.getByTestId('mlb-tracked-card')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'NBA and NHL model picks' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1 })).toBeNull()
  })
})
