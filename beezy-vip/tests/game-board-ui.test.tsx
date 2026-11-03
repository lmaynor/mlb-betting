import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { GameBoard } from '@/app/ev/games/game-board'
import type { GameProjectionsFeed } from '@/lib/game-lines'
import type { GameOddsFeed, GameOddsRow } from '@/lib/game-odds'
import { breakEvenProb } from '@/lib/game-ladder'

jest.mock('@/lib/betting-api', () => ({ apiGetGameOddsBoard: jest.fn(), apiGetGameProjections: jest.fn() }))

const q = (book: string, a: number | null, b: number | null) => ({ book, a, b })
const mk = (gk: string, away: string, home: string, commence: string, market: GameOddsRow['market'], line: number | null, books: GameOddsRow['books']): GameOddsRow =>
  ({ key: `${gk}|${market}|${line ?? ''}`, sport: gk.split(':')[0], game_key: gk, away, home, commence, market, line, books })

const GK = 'nhl:2026-11-02:DET@FLA'
const T1 = '2026-11-03T00:10:00Z'
// FLA -1.5: the home (b) side pays +123..+134, DET (a) the -150s. Pinnacle is a reference book.
const MAIN = [q('novig', -150, 123), q('kalshi', -150, 127), q('draftkings', -158, 132), q('fanduel', -157, 131), q('betmgm', -160, 134), q('pinnacle', -151, 130), q('caesars', -145, 118)]
const SIX = (a: number, b: number) => ['draftkings', 'fanduel', 'betmgm', 'caesars', 'novig', 'kalshi'].map(k => q(k, a, b))
const rowsNhl: GameOddsRow[] = [
  mk(GK, 'DET', 'FLA', T1, 'spread', -3.5, [q('draftkings', -300, 240), q('fanduel', -280, 235)]),
  mk(GK, 'DET', 'FLA', T1, 'spread', -1.5, MAIN),
  mk(GK, 'DET', 'FLA', T1, 'spread', 2.5, [q('draftkings', 350, -480), q('fanduel', 340, -450)]),
  mk(GK, 'DET', 'FLA', T1, 'total', 6.5, SIX(-110, -110)),
  mk(GK, 'DET', 'FLA', T1, 'ml', null, SIX(120, -140)),
]
const rowsNba: GameOddsRow[] = [
  mk('nba:2026-11-02:BOS@MIA', 'BOS', 'MIA', '2026-11-03T00:30:00Z', 'spread', -3.5, SIX(-110, -110)),
  mk('nba:2026-11-02:BOS@MIA', 'BOS', 'MIA', '2026-11-03T00:30:00Z', 'total', 224.5, SIX(-110, -110)),
]
const oddsFeed = (sport: string, rows: GameOddsRow[]): GameOddsFeed => ({
  sport, status: 'live', as_of: '2026-11-02T20:00:00Z', age_minutes: 4, stale: false, validated: false, lines_source: 'parlayapi', count: rows.length,
  books: [], offshore_books: ['pinnacle'], games: [], rows,
})
const projNhl: GameProjectionsFeed = {
  sport: 'nhl', status: 'live', engine: 'dixon_coles_eng_matrix_v1', as_of: null, age_minutes: 4, stale: false, count: 0, rows: [], markets: [],
  games: [{ game_key: GK, away: 'DET', home: 'FLA', start: T1, home_goalie: 'Bobrovsky', away_goalie: 'Lyon', is_b2b_road: true }],
  priced_games: [{
    game_key: GK, home_team: 'FLA', away_team: 'DET', game_date: '2026-11-02', lambda_h: 3.1, lambda_a: 2.4, exp_total: 5.5, markets: [],
    ladder: {
      ml: { p_home: 0.614, p_away: 0.386 },
      spread: [{ line: -3.5, p_home: 0.2, p_away: 0.8, push: 0 }, { line: -1.5, p_home: 0.467, p_away: 0.533, push: 0 }, { line: 2.5, p_home: 0.85, p_away: 0.15, push: 0 }],
      total: [{ line: 6.5, p_over: 0.4, p_under: 0.6, push: 0 }],
    },
  }],
}

const feeds = () => ({ odds: { nhl: oddsFeed('nhl', rowsNhl), nba: oddsFeed('nba', rowsNba) }, proj: { nhl: projNhl, nba: null } })

describe('GameBoard: cards across the top, one deep-dive below', () => {
  it('shows a card per game, earliest first, with the two main lines', () => {
    render(<GameBoard feeds={feeds()} />)
    const cards = screen.getAllByTestId('game-card')
    expect(cards.map(c => c.getAttribute('data-game'))).toEqual([GK, 'nba:2026-11-02:BOS@MIA'])
    expect(cards[0].textContent).toMatch(/FLA -1\.5/)
    expect(cards[0].textContent).toMatch(/6\.5/)
  })
  it('opens the first game by default; clicking another card swaps the deep-dive', () => {
    render(<GameBoard feeds={feeds()} />)
    expect(screen.getByRole('heading', { name: 'DET @ FLA' })).toBeInTheDocument()
    fireEvent.click(screen.getAllByTestId('game-card')[1])
    expect(screen.getByRole('heading', { name: 'BOS @ MIA' })).toBeInTheDocument()
  })
  it('only offers the sports that have games, and the pill narrows the cards', () => {
    render(<GameBoard feeds={feeds()} />)
    const pills = within(screen.getByRole('group', { name: 'Sport' }))
    expect(pills.getAllByRole('button').map(b => b.textContent)).toEqual(['All sports', 'NHL', 'NBA'])
    fireEvent.click(pills.getByRole('button', { name: 'NBA' }))
    expect(screen.getAllByTestId('game-card')).toHaveLength(1)
  })
  it('says the labels are unvalidated and offers no offshore book as a place to bet', () => {
    render(<GameBoard feeds={feeds()} />)
    expect(screen.getByTestId('game-board-strip').textContent).toMatch(/UNVALIDATED/)
    expect(screen.getByTestId('game-detail').textContent).toMatch(/UNVALIDATED MODEL ESTIMATE/)
  })
  it('sort reorders the cards and starts at start time', () => {
    render(<GameBoard feeds={feeds()} />)
    const sort = screen.getByLabelText('SORT') as HTMLSelectElement
    expect(sort.value).toBe('time')
    expect(Array.from(sort.options).map(o => o.textContent)).toEqual(['Start time', 'Best EV', 'Arbitrage', 'Model gap'])
    fireEvent.change(sort, { target: { value: 'gap' } })
    expect(screen.getAllByTestId('game-card')[0].getAttribute('data-game')).toBe(GK)   // only the NHL game has a model
  })
  it('says so when nothing is published, and when a feed is empty', () => {
    const first = render(<GameBoard feeds={{ odds: { nhl: null, nba: null }, proj: {} }} />)
    expect(screen.getByRole('status').textContent).toMatch(/not published yet/)
    first.unmount()
    render(<GameBoard feeds={{ odds: { nhl: oddsFeed('nhl', []), nba: null }, proj: {} }} />)
    expect(screen.getByRole('status').textContent).toMatch(/No book has posted/)
  })
})

describe('a sport with a feed but no games', () => {
  it('says why it is missing instead of silently vanishing', () => {
    const waiting = { ...oddsFeed('nba', []), status: 'awaiting_lines' as const }
    render(<GameBoard feeds={{ odds: { nhl: oddsFeed('nhl', rowsNhl), nba: waiting }, proj: {} }} />)
    expect(within(screen.getByRole('group', { name: 'Sport' })).getAllByRole('button').map(b => b.textContent)).toEqual(['All sports', 'NHL'])
    expect(screen.getByTestId('quiet-sports').textContent).toMatch(/NBA: the next slate's lines are not posted yet/)
  })
})

describe('GameDetail: summary, drivers and the model', () => {
  it('shows the projected score, the ML win probability and only the drivers that exist', () => {
    render(<GameBoard feeds={feeds()} />)
    const strip = screen.getByTestId('detail-strip').textContent!
    expect(strip).toMatch(/DET 2\.4 – FLA 3\.1/)
    expect(strip).toMatch(/FLA by 0\.7/)
    expect(strip).toMatch(/61\.4% ML/)
    expect(strip).toMatch(/FLA -1\.5 · O\/U 6\.5/)
    const drivers = screen.getByTestId('model-drivers').textContent!
    expect(drivers).toMatch(/Expected goals/)
    expect(drivers).toMatch(/Bobrovsky/)
    expect(drivers).toMatch(/back-to-back/)
    expect(drivers).not.toMatch(/GSAx/)
  })
  it('a game with no model shows no empty cells, no model columns and no drivers line', () => {
    render(<GameBoard feeds={feeds()} initialGame="nba:2026-11-02:BOS@MIA" />)
    const strip = screen.getByTestId('detail-strip').textContent!
    expect(strip).not.toMatch(/--/)
    expect(strip).not.toMatch(/Projected score|Model margin|Model total/)
    expect(strip).toMatch(/Market main/)
    expect(screen.queryByTestId('model-drivers')).toBeNull()
    expect(screen.queryByRole('columnheader', { name: 'Model' })).toBeNull()
    expect(screen.queryByRole('columnheader', { name: 'Model EV' })).toBeNull()
  })
  it('opens with one plain sentence: the best price, against fair, and how many books beat it', () => {
    render(<GameBoard feeds={feeds()} />)
    const v = screen.getByTestId('verdict').textContent!
    expect(v).toMatch(/^Best price on FLA -1\.5: \+134 at BetMGM/)
    expect(v).toMatch(/market fair price of/)
    expect(v).toMatch(/of 7 books beat it|No book beats it/)
  })
})

describe('the ladder chart: dots at break-even, edge gaps, rung selection', () => {
  it('the best price is plotted at its break-even: +134 sits at 42.7%, never above 50%', () => {
    render(<GameBoard feeds={feeds()} />)
    const chart = within(screen.getByTestId('ladder-chart'))
    const best = chart.getAllByTestId('best-dot').map(e => Number(e.getAttribute('data-y')))
    expect(best).toContain(breakEvenProb(134))
    expect(breakEvenProb(134)).toBeCloseTo(0.4274, 4)
  })
  it('each line with a market fair value gets one bar from its worst book to its best, against fair', () => {
    render(<GameBoard feeds={feeds()} />)
    const chart = within(screen.getByTestId('ladder-chart'))
    expect(chart.queryAllByTestId('book-dot')).toHaveLength(0)
    // only the main rung has the 3+ reference books a fair value needs; the two alt rungs have 2 books each
    const bars = chart.getAllByTestId('book-range')
    expect(bars).toHaveLength(1)
    const lo = Number(bars[0].getAttribute('data-ev-lo'))
    const hi = Number(bars[0].getAttribute('data-ev-hi'))
    expect(hi).toBeGreaterThan(lo)
    expect(screen.getByTestId('edge-panel').textContent).toMatch(/Books against the market fair price/)
    expect(screen.getByTestId('edge-panel').textContent).toMatch(/fair/)
  })
  it('shows the lines near the main line by default, and every line on request', () => {
    const rows = Array.from({ length: 21 }, (_, i) => mk(GK, 'DET', 'FLA', T1, 'spread', -12.5 + i, SIX(-110, -110)))
    const f = { odds: { nhl: oddsFeed('nhl', rows) }, proj: {} }
    render(<GameBoard feeds={f} />)
    expect(screen.getAllByTestId(/^rung-/).filter(e => /^rung-[-\d.]+$/.test(e.getAttribute('data-testid')!))).toHaveLength(7)
    fireEvent.click(screen.getByTestId('ladder-wide'))
    expect(screen.getAllByTestId(/^rung-/).filter(e => /^rung-[-\d.]+$/.test(e.getAttribute('data-testid')!))).toHaveLength(21)
  })
  it('the selected main rung is framed, and a better price than the market fair value draws a market gap', () => {
    render(<GameBoard feeds={feeds()} />)
    expect(screen.getByTestId('ladder-selected')).toBeInTheDocument()
    expect(screen.queryByTestId('gap-model')).toBeInTheDocument()   // 46.7% model vs +134 (42.7%)
  })
  it('clicking a rung column selects it and swaps the book matrix to that line', () => {
    render(<GameBoard feeds={feeds()} />)
    expect(within(screen.getByTestId('book-matrix')).getAllByTestId('matrix-row')).toHaveLength(6)
    fireEvent.click(screen.getByTestId('rung--3.5'))
    const rows = within(screen.getByTestId('book-matrix')).getAllByTestId('matrix-row')
    expect(rows).toHaveLength(2)
    expect(rows.map(r => r.getAttribute('data-book')).sort()).toEqual(['draftkings', 'fanduel'])
    const sel = screen.getAllByTestId('rung-row').find(r => r.getAttribute('aria-selected') === 'true')!
    expect(sel.getAttribute('data-line')).toBe('-3.5')
  })
  it('a thin rung (under 3 books) is flagged in the table', () => {
    render(<GameBoard feeds={feeds()} />)
    fireEvent.click(screen.getByRole('button', { name: 'With alt lines' }))
    const thin = screen.getAllByTestId('rung-row').filter(r => /THIN/.test(r.textContent!))
    expect(thin.map(r => r.getAttribute('data-line')).sort()).toEqual(['-3.5', '2.5'])
  })
})

describe('the book matrix: right = better price', () => {
  it('shows the top books by payout (and any sharp reference) and folds the rest behind one button', () => {
    render(<GameBoard feeds={feeds()} />)
    const rows = () => within(screen.getByTestId('book-matrix')).getAllByTestId('matrix-row')
    expect(rows().map(r => r.getAttribute('data-book'))).toEqual(['betmgm', 'draftkings', 'fanduel', 'pinnacle', 'kalshi', 'novig'])   // novig is a sharp reference, so it stays
    expect(screen.getByTestId('matrix-more').textContent).toMatch(/Show 1 more book/)
    fireEvent.click(screen.getByTestId('matrix-more'))
    expect(rows()).toHaveLength(7)
    expect(rows()[6].getAttribute('data-book')).toBe('caesars')
  })
  it('puts +134 furthest right and +123 furthest left', () => {
    render(<GameBoard feeds={feeds()} />)
    fireEvent.click(screen.getByTestId('matrix-more'))
    const rows = within(screen.getByTestId('book-matrix')).getAllByTestId('matrix-row')
    const x = (book: string) => Number(rows.find(r => r.getAttribute('data-book') === book)!.getAttribute('data-x'))
    expect(x('betmgm')).toBeGreaterThan(x('draftkings'))
    expect(x('draftkings')).toBeGreaterThan(x('fanduel'))
    expect(x('fanduel')).toBeGreaterThan(x('pinnacle'))
    expect(x('pinnacle')).toBeGreaterThan(x('kalshi'))
    expect(x('kalshi')).toBeGreaterThan(x('novig'))
  })
  it('labels the market and model fair markers and shows both sides of every quote', () => {
    render(<GameBoard feeds={feeds()} />)
    const m = screen.getByTestId('book-matrix')
    expect(m.textContent).toMatch(/Market fair/)
    expect(m.textContent).toMatch(/Model fair/)
    expect(m.textContent).toMatch(/worse price/)
    expect(m.textContent).toMatch(/better price/)
    const bm = within(m).getAllByTestId('matrix-row').find(r => r.getAttribute('data-book') === 'betmgm')!
    expect(bm.textContent).toMatch(/\+134 \/ -160/)
    expect(bm.textContent).toMatch(/BEST/)
  })
  it('Pinnacle is a SHARP reference row but can never be the best price', () => {
    render(<GameBoard feeds={feeds()} />)
    const pin = within(screen.getByTestId('book-matrix')).getAllByTestId('matrix-row').find(r => r.getAttribute('data-book') === 'pinnacle')!
    expect(pin.textContent).toMatch(/SHARP/)
    expect(pin.textContent).not.toMatch(/BEST/)
  })
})

describe('markets and sides', () => {
  it('the side toggle flips the ladder to the other team and reverses the rung order', () => {
    render(<GameBoard feeds={feeds()} />)
    const order = () => screen.getAllByTestId('ladder-chart')[0].querySelectorAll('[data-testid^="rung-"]')
    const lines = () => Array.from(order()).map(e => e.getAttribute('data-testid'))
    expect(lines()).toEqual(['rung--3.5', 'rung--1.5', 'rung-2.5'])
    fireEvent.click(within(screen.getByRole('group', { name: 'Side' })).getByRole('button', { name: 'DET' }))
    expect(lines()).toEqual(['rung-2.5', 'rung--1.5', 'rung--3.5'])
    const best = within(screen.getByTestId('ladder-chart')).getAllByTestId('best-dot').map(e => Number(e.getAttribute('data-y')))
    expect(best).toContain(breakEvenProb(-145))   // DET's best price is Caesars -145
  })
  it('Total offers Over / Under and the Moneyline is a single rung with no ladder chart', () => {
    render(<GameBoard feeds={feeds()} />)
    fireEvent.click(within(screen.getByRole('group', { name: 'Market' })).getByRole('button', { name: 'Total' }))
    expect(within(screen.getByRole('group', { name: 'Side' })).getAllByRole('button').map(b => b.textContent)).toEqual(['Over', 'Under'])
    fireEvent.click(within(screen.getByRole('group', { name: 'Market' })).getByRole('button', { name: 'Moneyline' }))
    expect(screen.queryByTestId('ladder-chart')).toBeNull()
    expect(screen.getByTestId('book-matrix')).toBeInTheDocument()
    expect(screen.getAllByTestId('rung-row')).toHaveLength(1)
  })
  it('alt lines stay out of the table until asked for', () => {
    render(<GameBoard feeds={feeds()} />)
    expect(screen.getAllByTestId('rung-row')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'With alt lines' }))
    expect(screen.getAllByTestId('rung-row')).toHaveLength(3)
  })
})
