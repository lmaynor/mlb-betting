import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { GamePricesClient } from '@/app/ev/games/game-prices-client'
import { GamesNav, gamesHref } from '@/app/ev/games/games-nav'
import type { GameOddsFeed, GameOddsRow } from '@/lib/game-odds'

let mobile = false
jest.mock('@/components/ui/mobile-sheet', () => {
  const actual = jest.requireActual('@/components/ui/mobile-sheet')
  return { ...actual, useIsMobile: () => mobile }
})

const q = (book: string, a: number | null, b: number | null) => ({ book, a, b })
const mk = (over: Partial<GameOddsRow> & { books: GameOddsRow['books'] }): GameOddsRow => ({
  key: 'k', sport: 'nba', game_key: 'nba:2026-11-02:BOS@MIA', away: 'BOS', home: 'MIA', commence: '2026-11-03T00:30:00Z', market: 'ml', line: null, ...over,
})
const BOOKS = ['draftkings', 'fanduel', 'betmgm', 'caesars']
const flat = [q('draftkings', -110, -110), q('fanduel', -110, -110), q('betmgm', -110, -110), q('caesars', 105, -125)]
const feed = (sport: 'nba' | 'nhl', rows: GameOddsRow[], over: Partial<GameOddsFeed> = {}): GameOddsFeed => ({
  sport, status: 'live', as_of: '2026-11-02T20:00:00Z', age_minutes: 12, stale: false, validated: false, lines_source: 'parlayapi', count: rows.length,
  books: BOOKS, offshore_books: ['pinnacle'], rows,
  games: [...new Map(rows.map(r => [r.game_key, { game_key: r.game_key, away: r.away, home: r.home, start: r.commence, n_rows: 1 }])).values()], ...over,
})
const nba = feed('nba', [
  mk({ key: 'ml', books: [...flat, q('pinnacle', 300, 300)] }),
  mk({ key: 'sp', market: 'spread', line: -3.5, books: flat }),
  mk({ key: 'arb', market: 'total', line: 224.5, books: [q('draftkings', 120, -150), q('fanduel', -150, 120)] }),
])
const nhl = feed('nhl', [mk({ key: 'nhl-ml', sport: 'nhl', game_key: 'nhl:2026-11-02:TOR@MTL', away: 'TOR', home: 'MTL', books: flat })])

beforeEach(() => { mobile = false })

describe('GamePricesClient (the /ev/games PRICES view)', () => {
  it('renders one row per game x market x line with a column per book and the UNVALIDATED label', () => {
    render(<GamePricesClient feeds={{ nba, nhl }} sport="all" />)
    const strip = screen.getByTestId('game-prices-strip')
    expect(strip.textContent).toMatch(/NBA \+ NHL/)
    expect(strip.textContent).toMatch(/4 ROWS/)
    expect(strip.textContent).toMatch(/LINES 12 MIN OLD/)
    expect(strip.textContent).toMatch(/UNVALIDATED market prices, not a tested edge/)
    expect(screen.queryByText('STALE')).toBeNull()
    const table = screen.getByRole('table')
    const heads = within(table).getAllByRole('columnheader').map(h => h.textContent)
    expect(heads).toEqual(expect.arrayContaining(['GAME', 'BEST AWAY / OVER', 'BEST HOME / UNDER', 'FAIR %', 'EV', 'DRAFTKIN', 'FANDUEL', 'BETMGM', 'CAESARS']))
    expect(within(table).getAllByText('BOS @ MIA').length).toBe(3)
    expect(within(table).getByText('TOR @ MTL')).toBeInTheDocument()
    expect(table.textContent).toMatch(/MIA -3\.5/)
    expect(table.textContent).toMatch(/O\/U 224\.5/)
  })

  it('shows an offshore book with its price but never as the best price', () => {
    render(<GamePricesClient feeds={{ nba, nhl: null }} sport="nba" />)
    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('columnheader').some(h => h.textContent === 'PINNACLE*')).toBe(true)
    const mlRow = within(table).getAllByRole('row').find(r => r.textContent?.includes('NBA') && r.textContent?.includes('ML'))!
    expect(mlRow.textContent).toMatch(/\+300/)               // pinnacle's price is on screen ...
    expect(within(mlRow).getAllByText('+105').length).toBeGreaterThan(0)
    // ... but the best away price (the first price cell after the game) is caesars' +105, not +300
    expect(mlRow.querySelectorAll('td')[1].textContent).toMatch(/\+105/)
    expect(mlRow.querySelectorAll('td')[1].textContent).not.toMatch(/\+300/)
    expect(within(mlRow).queryAllByText('+300').every(el => el.style.color !== 'rgb(87, 242, 135)')).toBe(true)
  })

  it('highlights an arbitrage with the locked profit and a Copy hedge button', () => {
    render(<GamePricesClient feeds={{ nba, nhl: null }} sport="nba" />)
    const arbRow = screen.getAllByRole('row').find(r => r.getAttribute('data-row') === 'game-arb')!
    expect(arbRow.textContent).toMatch(/ARB \+10\.00%/)
    expect(arbRow.textContent).toMatch(/\+\$10\.00 locked on \$100/)
    expect(within(arbRow).getByRole('button', { name: 'Copy hedge' })).toBeInTheDocument()
    // it ranks first under the default EV sort
    expect(screen.getAllByRole('row').filter(r => r.getAttribute('data-row') === 'game-prices')[0].getAttribute('data-arb')).toBe('true')
  })

  it('narrows by market and by arbs only, and says so when nothing matches', () => {
    render(<GamePricesClient feeds={{ nba, nhl }} sport="all" />)
    fireEvent.click(screen.getByRole('button', { name: 'Spread' }))
    expect(screen.getByTestId('game-prices-strip').textContent).toMatch(/1 ROWS/)
    fireEvent.click(screen.getByRole('button', { name: 'All' }))
    fireEvent.click(screen.getByLabelText('Arbs only'))
    expect(screen.getByTestId('game-prices-strip').textContent).toMatch(/1 ROWS/)
    fireEvent.change(screen.getByLabelText('Search teams'), { target: { value: 'zzz' } })
    expect(screen.getByRole('status').textContent).toMatch(/No lines match/)
  })

  it('searches by team', () => {
    render(<GamePricesClient feeds={{ nba, nhl }} sport="all" />)
    fireEvent.change(screen.getByLabelText('Search teams'), { target: { value: 'mtl' } })
    expect(screen.getByTestId('game-prices-strip').textContent).toMatch(/1 ROWS/)
    expect(within(screen.getByRole('table')).queryByText('BOS @ MIA')).toBeNull()
  })

  it('flags a stale feed', () => {
    render(<GamePricesClient feeds={{ nba: { ...nba, stale: true, age_minutes: 200 }, nhl: null }} sport="nba" />)
    expect(screen.getByText('STALE')).toBeInTheDocument()
    expect(screen.getByTestId('game-prices-strip').textContent).toMatch(/LINES 3\.3 H OLD/)
  })

  it('says prices are not published when the feeds are missing or unavailable', () => {
    const { rerender } = render(<GamePricesClient feeds={{ nba: null, nhl: null }} sport="all" />)
    expect(screen.getByRole('status').textContent).toMatch(/Game-line prices are not published yet/)
    expect(screen.getByTestId('game-prices-strip').textContent).toMatch(/UNVALIDATED/)
    rerender(<GamePricesClient feeds={{ nba: feed('nba', [], { status: 'unavailable' }), nhl: null }} sport="nba" />)
    expect(screen.getByRole('status').textContent).toMatch(/not published yet/)
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('says no lines are posted when the feed is up but empty', () => {
    render(<GamePricesClient feeds={{ nba: feed('nba', [], { status: 'awaiting_lines' }), nhl: null }} sport="nba" />)
    expect(screen.getByRole('status').textContent).toMatch(/No book has posted game lines/)
  })

  it('tells a free viewer how many rows the preview withheld', () => {
    render(<GamePricesClient feeds={{ nba: { ...nba, locked: 37 }, nhl: null }} sport="nba" />)
    const note = screen.getByRole('note')
    expect(note.textContent).toMatch(/37/)
    expect(note.textContent).toMatch(/All-Access Pro/)
  })

  it('on a phone each game x market is a card, with every book behind a disclosure', () => {
    mobile = true
    render(<GamePricesClient feeds={{ nba, nhl: null }} sport="nba" />)
    expect(document.querySelector('.odds-table')).toBeNull()
    const cards = document.querySelectorAll('[data-card="game-prices"]')
    expect(cards).toHaveLength(3)
    expect(screen.getAllByText('All books').length).toBe(3)
    expect(screen.getAllByRole('button', { name: 'Copy hedge' })).toHaveLength(1)
  })
})

describe('the PRICES | MODEL switch', () => {
  it('builds shareable hrefs: prices and all are the defaults and stay out of the URL', () => {
    expect(gamesHref('prices', 'all')).toBe('/ev/games')
    expect(gamesHref('model', 'all')).toBe('/ev/games?view=model')
    expect(gamesHref('prices', 'nba')).toBe('/ev/games?sport=nba')
    expect(gamesHref('model', 'nhl')).toBe('/ev/games?view=model&sport=nhl')
  })
  it('the view links keep the sport and the sport links keep the view', () => {
    render(<GamesNav view="model" sport="nba" />)
    const view = within(screen.getByRole('navigation', { name: 'Game lines view' }))
    expect(view.getByRole('link', { name: 'PRICES' })).toHaveAttribute('href', '/ev/games?sport=nba')
    expect(view.getByRole('link', { name: 'MODEL' })).toHaveAttribute('href', '/ev/games?view=model&sport=nba')
    expect(view.getByRole('link', { name: 'MODEL' })).toHaveAttribute('aria-current', 'page')
    const sports = within(screen.getByRole('navigation', { name: 'Game lines sport' }))
    expect(sports.getByRole('link', { name: 'NBA + NHL' })).toHaveAttribute('href', '/ev/games?view=model')
    expect(sports.getByRole('link', { name: 'NHL' })).toHaveAttribute('href', '/ev/games?view=model&sport=nhl')
    expect(sports.getByRole('link', { name: 'NBA' })).toHaveAttribute('aria-current', 'page')
  })
})
