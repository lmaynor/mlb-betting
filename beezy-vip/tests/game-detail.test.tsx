import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { GameDetail } from '@/app/ev/games/game-detail'
import { buildGameBoard, sortBoard } from '@/lib/game-board'
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

const BASE = { refs: [] as string[], method: 'proportional' as const, bettable: null }
/** The deep-dive of one game of a feed (the earliest by default), the way the card's Analysis tab renders it. */
function detail(f: { odds: Record<string, GameOddsFeed | null>; proj: Record<string, GameProjectionsFeed | null> }, key?: string) {
  const entries = sortBoard(buildGameBoard(f, BASE), 'time')
  const g = key ? entries.find(e => e.key === key)! : entries[0]
  return render(<GameDetail game={g} bettable={null} ageLabel="4 MIN old" />)
}

const feeds = () => ({ odds: { nhl: oddsFeed('nhl', rowsNhl), nba: oddsFeed('nba', rowsNba) }, proj: { nhl: projNhl, nba: null } })

describe('line movement (flow)', () => {
  const at = (min: number) => new Date(Date.UTC(2026, 10, 3, 14, 0) + min * 60_000).toISOString()
  const pt = (min: number, line: number | null, p_a: number | null) => ({ t: at(min), line, p_a, best_a: -110, best_b: -110, n: 6 })
  const withHistory = (history: Record<string, { points: ReturnType<typeof pt>[] }>) => ({ odds: { nhl: { ...oddsFeed('nhl', rowsNhl), history } }, proj: {} })
  const moving = { [`${GK}|spread`]: { points: [pt(0, -1.0, 0.50), pt(60, -1.0, 0.52), pt(120, -1.5, 0.44), pt(180, -1.5, 0.46)] }, [`${GK}|total`]: { points: [pt(0, 6.5, 0.5), pt(5, 6.5, 0.5)] } }

  it('the deep-dive draws the line and the fair price at the current line, from the side shown', () => {
    detail(withHistory(moving))
    const mv = screen.getByTestId('movement')
    expect(within(mv).getAllByTestId('sparkline')).toHaveLength(2)
    expect(screen.getByTestId('movement-line').textContent).toMatch(/FLA -1 \u2192 -1\.5/)
    // home side: away fair 0.44 -> 0.46 at -1.5 is home 56.0% -> 54.0%
    expect(screen.getByTestId('movement-fair').textContent).toMatch(/56\.0% \u2192 54\.0% \(-2\.0 pts\)/)
    fireEvent.click(within(screen.getByRole('group', { name: 'Side' })).getByRole('button', { name: 'DET' }))
    expect(screen.getByTestId('movement-fair').textContent).toMatch(/44\.0% \u2192 46\.0% \(\+2\.0 pts\)/)
  })
  it('says tracking is new when the market has too few snapshots, and says nothing when the backend has no history', () => {
    const first = detail(withHistory({ [`${GK}|spread`]: { points: [pt(0, -1.5, 0.4)] } }))
    expect(screen.getByTestId('movement-empty').textContent).toMatch(/not enough snapshots/)
    first.unmount()
    detail(feeds())
    expect(screen.queryByTestId('movement')).toBeNull()
    expect(screen.queryByTestId('movement-empty')).toBeNull()
    expect(screen.queryByTestId('card-move')).toBeNull()
  })
})

describe('GameDetail: summary, drivers and the model', () => {
  it('shows the projected score, the ML win probability and only the drivers that exist', () => {
    detail(feeds())
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
    detail(feeds(), 'nba:2026-11-02:BOS@MIA')
    const strip = screen.getByTestId('detail-strip').textContent!
    expect(strip).not.toMatch(/--/)
    expect(strip).not.toMatch(/Projected score|Model margin|Model total/)
    expect(strip).toMatch(/Market main/)
    expect(screen.queryByTestId('model-drivers')).toBeNull()
    expect(screen.queryByRole('columnheader', { name: 'Model' })).toBeNull()
    expect(screen.queryByRole('columnheader', { name: 'Model EV' })).toBeNull()
  })
  it('opens with one plain sentence: the best price, against fair, and how many books beat it', () => {
    detail(feeds())
    const v = screen.getByTestId('verdict').textContent!
    expect(v).toMatch(/^Best price on FLA -1\.5: \+134 at BetMGM/)
    expect(v).toMatch(/market fair price of/)
    expect(v).toMatch(/of 7 books beat it|No book beats it/)
  })
})

describe('the ladder chart: dots at break-even, edge gaps, rung selection', () => {
  it('the best price is plotted at its break-even: +134 sits at 42.7%, never above 50%', () => {
    detail(feeds())
    const chart = within(screen.getByTestId('ladder-chart'))
    const best = chart.getAllByTestId('best-dot').map(e => Number(e.getAttribute('data-y')))
    expect(best).toContain(breakEvenProb(134))
    expect(breakEvenProb(134)).toBeCloseTo(0.4274, 4)
  })
  it('shows the lines near the main line by default, and every line on request', () => {
    const rows = Array.from({ length: 21 }, (_, i) => mk(GK, 'DET', 'FLA', T1, 'spread', -12.5 + i, SIX(-110, -110)))
    const f = { odds: { nhl: oddsFeed('nhl', rows) }, proj: {} }
    detail(f)
    expect(screen.getAllByTestId(/^rung-/).filter(e => /^rung-[-\d.]+$/.test(e.getAttribute('data-testid')!))).toHaveLength(7)
    fireEvent.click(screen.getByTestId('ladder-wide'))
    expect(screen.getAllByTestId(/^rung-/).filter(e => /^rung-[-\d.]+$/.test(e.getAttribute('data-testid')!))).toHaveLength(21)
  })
  it('the selected main rung is framed, and a better price than the market fair value draws a market gap', () => {
    detail(feeds())
    expect(screen.getByTestId('ladder-selected')).toBeInTheDocument()
    expect(screen.queryByTestId('gap-model')).toBeInTheDocument()   // 46.7% model vs +134 (42.7%)
  })
  it('clicking a rung column selects it and swaps the selected-line panel to that line', () => {
    detail(feeds())
    expect(within(screen.getByTestId('selected-line')).getByText('FLA -1.5')).toBeInTheDocument()
    expect(within(screen.getByTestId('best-vs-rest')).getAllByTestId('bvr-row')).toHaveLength(5)
    fireEvent.click(screen.getByTestId('rung--3.5'))
    expect(within(screen.getByTestId('selected-line')).getByText('FLA -3.5')).toBeInTheDocument()
    const rows = within(screen.getByTestId('best-vs-rest')).getAllByTestId('bvr-row')
    expect(rows.map(r => r.getAttribute('data-book')).sort()).toEqual(['draftkings', 'fanduel'])
    expect(screen.getByTestId('heat-row--3.5')).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('the heat strip: every line against every book', () => {
  it('has a row per shown line and a cell per book, with the books that do not quote a line left empty', () => {
    detail(feeds())
    const strip = within(screen.getByTestId('heat-strip'))
    expect(strip.getByTestId('heat-row--1.5').textContent).toMatch(/FLA -1\.5/)
    expect(strip.getByTestId('heat-row--1.5').textContent).toMatch(/MAIN/)
    expect(strip.getByTestId('heat-cell--1.5-betmgm')).toBeInTheDocument()
    expect(strip.queryByTestId('heat-cell--3.5-betmgm')).toBeNull()      // betmgm does not quote the -3.5 alt
    expect(strip.getByTestId('heat-cell--3.5-draftkings')).toBeInTheDocument()
  })
  it('shades a cell by its EV against the market fair price and outlines the best bettable price on each line', () => {
    detail(feeds())
    const best = screen.getByTestId('heat-cell--1.5-betmgm')
    expect(best).toHaveAttribute('data-best', 'true')
    expect(Number(best.getAttribute('data-ev'))).toBeGreaterThan(Number(screen.getByTestId('heat-cell--1.5-novig').getAttribute('data-ev')))
    expect(screen.getByTestId('heat-cell--1.5-pinnacle')).not.toHaveAttribute('data-best')   // a reference, never the best price
  })
  it('a line with no fair value (under 3 books) shows prices, not an EV, and is marked THIN', () => {
    detail(feeds())
    expect(screen.getByTestId('heat-cell--3.5-draftkings').textContent).toBe('+240')
    expect(screen.getByTestId('heat-row--3.5').textContent).toMatch(/THIN/)
    expect(screen.getByTestId('heat-row-2.5').textContent).toMatch(/THIN/)
  })
  it('clicking a cell opens that line', () => {
    detail(feeds())
    fireEvent.click(screen.getByTestId('heat-cell-2.5-draftkings'))
    expect(within(screen.getByTestId('selected-line')).getByText('FLA +2.5')).toBeInTheDocument()
  })
  it('a suspiciously large gap is amber and marked with a question mark', () => {
    const rows = [mk(GK, 'DET', 'FLA', T1, 'spread', -1.5, [...['draftkings', 'fanduel', 'betmgm', 'novig', 'kalshi'].map(k => q(k, -110, -110)), q('caesars', -250, 400)])]
    detail({ odds: { nhl: oddsFeed('nhl', rows) }, proj: {} })
    const cell = screen.getByTestId('heat-cell--1.5-caesars')
    expect(cell).toHaveAttribute('data-suspect', 'true')
    expect(cell.textContent).toMatch(/\?$/)
  })
})

describe('the selected line: best vs the rest', () => {
  const bvr = () => within(screen.getByTestId('best-vs-rest'))
  it('shows the three best prices you can bet, the sharp references, and one summary row for everyone else', () => {
    detail(feeds())
    expect(bvr().getAllByTestId('bvr-row').map(r => r.getAttribute('data-book'))).toEqual(['betmgm', 'draftkings', 'fanduel', 'pinnacle', 'novig'])
    expect(bvr().getByTestId('bvr-rest').textContent).toMatch(/2 other books/)
  })
  it('puts a better price further right', () => {
    detail(feeds())
    const x = (book: string) => Number(bvr().getAllByTestId('bvr-row').find(r => r.getAttribute('data-book') === book)!.getAttribute('data-x'))
    expect(x('betmgm')).toBeGreaterThan(x('draftkings'))
    expect(x('draftkings')).toBeGreaterThan(x('fanduel'))
    expect(x('fanduel')).toBeGreaterThan(x('pinnacle'))
    expect(x('pinnacle')).toBeGreaterThan(x('novig'))
  })
  it('every row shares one grid and the fair and model lines are drawn once down the list', () => {
    detail(feeds())
    const rows = bvr().getAllByTestId('bvr-row')
    expect(new Set(rows.map(r => r.className)).size).toBe(1)
    expect(rows[0].className).toMatch(/grid-cols-\[var\(--ml\)_minmax\(0,1fr\)_var\(--mr\)\]/)
    expect(bvr().getAllByTestId('matrix-fair-line')).toHaveLength(1)
    expect(bvr().getAllByTestId('matrix-model-line')).toHaveLength(1)
  })
  it('a book that beats fair has a green bar right of the line; one that does not has a grey bar to its left', () => {
    const rows = [mk(GK, 'DET', 'FLA', T1, 'spread', -1.5, [...['draftkings', 'fanduel', 'betmgm', 'novig', 'kalshi'].map(k => q(k, -110, -110)), q('caesars', -125, 105)])]
    detail({ odds: { nhl: oddsFeed('nhl', rows) }, proj: {} })
    const row = (book: string) => bvr().getAllByTestId('bvr-row').find(r => r.getAttribute('data-book') === book)!
    const bar = (book: string) => within(row(book)).getByTestId('bvr-bar') as HTMLElement
    expect(Number(row('caesars').getAttribute('data-x'))).toBeGreaterThan(0.5)
    expect(bar('caesars').style.background).toBe('rgb(87, 242, 135)')
    expect(Number(row('draftkings').getAttribute('data-x'))).toBeLessThanOrEqual(0.5)
    expect(bar('draftkings').style.background).not.toBe('rgb(87, 242, 135)')
  })
  it('labels the market and model fair prices and shows both sides of every quote', () => {
    detail(feeds())
    const legend = bvr().getByTestId('matrix-legend').textContent!
    expect(legend).toMatch(/Market fair/)
    expect(legend).toMatch(/Model fair/)
    expect(legend).toMatch(/unvalidated/)
    const bm = bvr().getAllByTestId('bvr-row').find(r => r.getAttribute('data-book') === 'betmgm')!
    expect(bm.textContent).toMatch(/\+134 \/ -160/)
    expect(bm.textContent).toMatch(/BEST/)
  })
  it('Pinnacle is a SHARP reference row but can never be the best price', () => {
    detail(feeds())
    const pin = bvr().getAllByTestId('bvr-row').find(r => r.getAttribute('data-book') === 'pinnacle')!
    expect(pin.textContent).toMatch(/SHARP/)
    expect(pin.textContent).not.toMatch(/BEST/)
  })
  it('"Every book" opens the full list, with the long tail still folded', () => {
    detail(feeds())
    expect(screen.queryByTestId('book-matrix')).toBeNull()
    fireEvent.click(screen.getByTestId('bvr-full'))
    expect(screen.getByTestId('book-matrix')).toBeInTheDocument()
    expect(within(screen.getByTestId('book-matrix')).getAllByTestId('matrix-row').length).toBeGreaterThanOrEqual(6)
  })
})

describe('markets and sides', () => {
  it('the side toggle flips the ladder to the other team and reverses the rung order', () => {
    detail(feeds())
    const order = () => screen.getAllByTestId('ladder-chart')[0].querySelectorAll('[data-testid^="rung-"]')
    const lines = () => Array.from(order()).map(e => e.getAttribute('data-testid'))
    expect(lines()).toEqual(['rung--3.5', 'rung--1.5', 'rung-2.5'])
    fireEvent.click(within(screen.getByRole('group', { name: 'Side' })).getByRole('button', { name: 'DET' }))
    expect(lines()).toEqual(['rung-2.5', 'rung--1.5', 'rung--3.5'])
    const best = within(screen.getByTestId('ladder-chart')).getAllByTestId('best-dot').map(e => Number(e.getAttribute('data-y')))
    expect(best).toContain(breakEvenProb(-145))   // DET's best price is Caesars -145
  })
  it('Total offers Over / Under and the Moneyline is a single line with no ladder chart', () => {
    detail(feeds())
    fireEvent.click(within(screen.getByRole('group', { name: 'Market' })).getByRole('button', { name: 'Total' }))
    expect(within(screen.getByRole('group', { name: 'Side' })).getAllByRole('button').map(b => b.textContent)).toEqual(['Over', 'Under'])
    fireEvent.click(within(screen.getByRole('group', { name: 'Market' })).getByRole('button', { name: 'Moneyline' }))
    expect(screen.queryByTestId('ladder-chart')).toBeNull()
    expect(screen.getByTestId('best-vs-rest')).toBeInTheDocument()
    expect(screen.getAllByTestId(/^heat-row-/)).toHaveLength(1)
  })
  it('the heat strip shows the lines near the main one, and every line on request', () => {
    const rows = Array.from({ length: 21 }, (_, i) => mk(GK, 'DET', 'FLA', T1, 'spread', -12.5 + i, SIX(-110, -110)))
    detail({ odds: { nhl: oddsFeed('nhl', rows) }, proj: {} })
    expect(screen.getAllByTestId(/^heat-row-/)).toHaveLength(7)
    fireEvent.click(screen.getByTestId('ladder-wide'))
    expect(screen.getAllByTestId(/^heat-row-/)).toHaveLength(21)
  })
})
