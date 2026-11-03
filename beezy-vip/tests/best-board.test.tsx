import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { BestBoard } from '@/app/ev/games/best-board'
import type { GameProjectionsFeed } from '@/lib/game-lines'
import type { GameOddsFeed, GameOddsRow } from '@/lib/game-odds'

jest.mock('@/lib/betting-api', () => ({ apiGetGameOddsBoard: jest.fn(), apiGetGameProjections: jest.fn() }))

const q = (book: string, a: number | null, b: number | null) => ({ book, a, b })
const GK = 'nhl:2026-11-02:DET@FLA'
const T1 = '2026-11-03T00:10:00Z'
const mk = (market: GameOddsRow['market'], line: number | null, books: GameOddsRow['books'], gk = GK, away = 'DET', home = 'FLA', sport = 'nhl', commence = T1): GameOddsRow =>
  ({ key: `${gk}|${market}|${line ?? ''}`, sport, game_key: gk, away, home, commence, market, line, books })
const FLAT = ['draftkings', 'fanduel', 'betmgm', 'novig', 'kalshi'].map(k => q(k, -110, -110))
// FLA -1.5: caesars pays +106 on the home side, a believable 2%+ edge; the total and the moneyline are tight
const EDGE = [
  mk('spread', -1.5, [...FLAT, q('caesars', -125, 106)]),
  mk('spread', -2.5, [...FLAT, q('caesars', -140, 125)]),
  mk('spread', -3.5, [q('draftkings', -300, 240), q('fanduel', -280, 235)]),
  mk('total', 6.5, FLAT),
  mk('ml', null, ['draftkings', 'fanduel', 'betmgm', 'caesars'].map(k => q(k, 120, -140))),
]
const TIGHT = [mk('spread', -1.5, FLAT), mk('total', 6.5, FLAT)]
const feed = (rows: GameOddsRow[], extra: Partial<GameOddsFeed> = {}): GameOddsFeed => ({
  sport: 'nhl', status: 'live', as_of: '2026-11-02T20:00:00Z', age_minutes: 4, stale: false, validated: false, lines_source: 'parlayapi', count: rows.length,
  books: [], offshore_books: ['pinnacle'], games: [], rows, ...extra,
})
const board = (rows: GameOddsRow[], extra: Partial<GameOddsFeed> = {}) => <BestBoard feeds={{ odds: { nhl: feed(rows, extra) }, proj: {} }} />
const PROJ: GameProjectionsFeed = {
  sport: 'nhl', status: 'live', engine: 'dixon_coles_eng_matrix_v1', as_of: null, age_minutes: 4, stale: false, count: 0, rows: [], markets: [],
  games: [{ game_key: GK, away: 'DET', home: 'FLA', start: T1, home_goalie: 'Bobrovsky', away_goalie: 'Lyon', is_b2b_road: true }],
  priced_games: [{
    game_key: GK, home_team: 'FLA', away_team: 'DET', game_date: '2026-11-02', lambda_h: 3.1, lambda_a: 2.4, exp_total: 5.5, markets: [],
    ladder: {
      ml: { p_home: 0.614, p_away: 0.386 },
      spread: [{ line: -3.5, p_home: 0.2, p_away: 0.8, push: 0 }, { line: -2.5, p_home: 0.32, p_away: 0.68, push: 0 }, { line: -1.5, p_home: 0.55, p_away: 0.45, push: 0 }],
      total: [{ line: 6.5, p_over: 0.4, p_under: 0.6, push: 0 }],
    },
  }],
}
const withModel = (rows: GameOddsRow[]) => <BestBoard feeds={{ odds: { nhl: feed(rows) }, proj: { nhl: PROJ } }} />
const pt = (min: number, line: number | null, p_a: number | null) => ({ t: new Date(Date.UTC(2026, 10, 3, 14, min)).toISOString(), line, p_a, best_a: -110, best_b: -110, n: 6 })

describe('BestBoard: one row per game', () => {
  it('shows the best price at every market with its book, and the best edge as one chip', () => {
    render(board(EDGE))
    const row = screen.getAllByTestId('board-row')[0]
    expect(row.textContent).toMatch(/DET.*FLA/)
    expect(row.textContent).toMatch(/\+106/)           // the home spread side at caesars
    expect(within(row).getByTestId('row-edge').textContent).toMatch(/FLA -2\.5|FLA -1\.5/)
    expect(within(row).getAllByRole('img', { name: /Caesars/i }).length).toBeGreaterThan(0)
  })
  it('a game with no edge shows a dash, never a manufactured chip', () => {
    render(board(TIGHT))
    expect(within(screen.getAllByTestId('board-row')[0]).queryByTestId('row-edge')).toBeNull()
  })
  it('only offers the sports with games, and says why one is missing', () => {
    render(<BestBoard feeds={{ odds: { nhl: feed(TIGHT), nba: { ...feed([], { sport: 'nba' }), status: 'awaiting_lines' } }, proj: {} }} />)
    expect(within(screen.getByRole('group', { name: 'Sport' })).getAllByRole('button').map(b => b.textContent)).toEqual(['All sports', 'NHL'])
    expect(screen.getByTestId('quiet-sports').textContent).toMatch(/NBA: the next slate's lines are not posted yet/)
  })
  it('says so when nothing is published, and when a feed is empty', () => {
    const first = render(<BestBoard feeds={{ odds: { nhl: null }, proj: {} }} />)
    expect(screen.getByRole('status').textContent).toMatch(/not published yet/)
    first.unmount()
    render(board([]))
    expect(screen.getByRole('status').textContent).toMatch(/No book has posted/)
  })
  it('sorts by start time first and offers the movement sort', () => {
    const later = TIGHT.map(r => ({ ...r, key: r.key.replace('FLA', 'MTL'), game_key: 'nhl:2026-11-02:TOR@MTL', away: 'TOR', home: 'MTL', commence: '2026-11-03T03:00:00Z' }))
    render(board([...later, ...TIGHT]))
    expect(screen.getAllByTestId('board-row').map(r => r.getAttribute('data-game'))).toEqual([GK, 'nhl:2026-11-02:TOR@MTL'])
    expect(Array.from((screen.getByLabelText('SORT') as HTMLSelectElement).options).map(o => o.textContent)).toContain('Movement')
  })
})

describe('Edges now', () => {
  it('lists the real edges on the slate, and says honestly when there are none', () => {
    const a = render(board(EDGE))
    expect(within(screen.getByTestId('edges-now')).getAllByTestId('edge-chip').length).toBeGreaterThan(0)
    a.unmount()
    render(board(TIGHT))
    expect(screen.getByTestId('edges-none').textContent).toMatch(/Nothing clears the bar/)
  })
  it('a chip opens that game\'s card on the right market, with the edge\'s line already picked', () => {
    // -1.5 is the main line (most books, no edge); the edge is on the -2.5 alt, which has six books and pays +125
    const wide = [mk('spread', -1.5, [...FLAT, q('espnbet', -110, -110), q('hardrock', -110, -110), q('betrivers', -110, -110)]), mk('spread', -2.5, [...FLAT, q('caesars', -140, 125)])]
    render(board(wide))
    fireEvent.click(screen.getAllByTestId('edge-chip')[0])
    expect(screen.getByTestId('tab-spread')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('line-chip--2.5')).toHaveAttribute('aria-pressed', 'true')
  })
  it('a huge gap is shown as an amber outlier to confirm: never hidden, never styled as a clean edge', () => {
    const rows = [mk('spread', -1.5, [...FLAT, q('espnbet', -110, -110), q('hardrock', -110, -110), q('betrivers', -110, -110)]), mk('total', 8.5, [...FLAT, q('caesars', -600, 525)])]
    render(board(rows))
    const chip = screen.getByTestId('edge-chip')
    expect(chip).toHaveAttribute('data-outlier', 'true')
    expect(chip.textContent).toMatch(/\+525/)
    expect(chip.textContent).toMatch(/CHECK/)
    expect(screen.queryByTestId('edges-none')).toBeNull()
  })
  it('the edges are small cards: the game, the line, the price at its book and the EV', () => {
    render(board(EDGE))
    const card = screen.getAllByTestId('edge-chip')[0]
    expect(card.textContent).toMatch(/DET @ FLA/)
    expect(card.textContent).toMatch(/FLA -2\.5/)        // the biggest of the game's edges (the -2.5 alt, six books, +125)
    expect(card.textContent).toMatch(/\+125/)
    expect(card.textContent).toMatch(/\+\d+\.\d%/)
    expect(within(card).getByRole('img', { name: /Caesars/i })).toBeInTheDocument()
  })
})

describe('the card (double-click)', () => {
  it('keeps one size however you switch tabs, so it never jumps', () => {
    render(board(EDGE, { history: { [`${GK}|spread`]: { points: [pt(0, -1.0, 0.50), pt(60, -1.5, 0.45)] } } }))
    fireEvent.doubleClick(screen.getAllByTestId('board-row')[0])
    const cls = screen.getByTestId('game-popup').className
    expect(cls).toMatch(/\bh-\[88vh\]/)
    expect(cls).toMatch(/sm:h-\[680px\]/)
    for (const t of ['tab-total', 'tab-ml', 'tab-move', 'tab-analysis', 'tab-spread']) {
      fireEvent.click(screen.getByTestId(t))
      expect(screen.getByTestId('game-popup').className).toBe(cls)
    }
    expect(screen.getByTestId('popup-body').className).toMatch(/overflow-y-auto/)
  })
  const open = (e: HTMLElement) => fireEvent.doubleClick(e)
  it('opens on a double-click, from Enter and from the arrow button, and a single click only selects', () => {
    render(board(EDGE))
    const row = screen.getAllByTestId('board-row')[0]
    fireEvent.click(row)
    expect(screen.queryByTestId('game-popup')).toBeNull()
    expect(row).toHaveAttribute('aria-selected', 'true')
    open(row)
    expect(screen.getByRole('dialog', { name: 'DET at FLA' })).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('game-popup')).toBeNull()
    fireEvent.keyDown(row, { key: 'Enter' })
    expect(screen.getByTestId('game-popup')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    fireEvent.click(screen.getByRole('button', { name: /Open the card for DET at FLA/ }))
    expect(screen.getByTestId('game-popup')).toBeInTheDocument()
  })
  it('the backdrop closes it, a click inside does not', () => {
    render(board(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    fireEvent.mouseDown(screen.getByTestId('game-popup'))
    expect(screen.getByTestId('game-popup')).toBeInTheDocument()
    fireEvent.mouseDown(screen.getByTestId('game-popup-backdrop'))
    expect(screen.queryByTestId('game-popup')).toBeNull()
  })
  it('opens with one honest sentence: the best edge, or that there is none', () => {
    const a = render(board(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    expect(screen.getByTestId('popup-headline').textContent).toMatch(/^Best edge: FLA -1\.5 \+106 at Caesars/)
    a.unmount()
    render(board(TIGHT))
    open(screen.getAllByTestId('board-row')[0])
    expect(screen.getByTestId('popup-headline').textContent).toBe('No price beats the market fair price by 2% or more on the main lines.')
  })
  it('makes opening obvious: a hint above the table and a labelled Open button on every row', () => {
    render(board(EDGE))
    expect(screen.getByTestId('open-hint').textContent).toMatch(/Double-click any game to open its card/)
    expect(screen.getByRole('button', { name: /Open the card for DET at FLA/ }).textContent).toMatch(/Open/)
    expect(screen.getByRole('columnheader', { name: 'Card' })).toBeInTheDocument()
  })
  it('tabs: one per market, then Movement and Model, starting on the spread', () => {
    render(board(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    expect(within(screen.getByRole('tablist')).getAllByRole('tab').map(t => t.textContent)).toEqual(['Moneyline', 'Spread', 'Total', 'Movement', 'Model'])
    expect(screen.getByTestId('tab-spread')).toHaveAttribute('aria-selected', 'true')
  })
  it('a market is a line scroller, two big best-price buttons, and every line as tiles (the Pikkit layout)', () => {
    render(board(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    const chips = within(screen.getByTestId('line-scroller')).getAllByRole('button')
    expect(chips.map(c => c.getAttribute('data-testid'))).toEqual(['line-chip--3.5', 'line-chip--2.5', 'line-chip--1.5'])
    expect(screen.getByTestId('line-chip--1.5')).toHaveAttribute('aria-pressed', 'true')       // the main line is picked
    const [away, home] = within(screen.getByTestId('side-buttons')).getAllByTestId('side-button')
    expect(away.textContent).toMatch(/DET \+1\.5/)
    expect(home.textContent).toMatch(/FLA -1\.5/)
    expect(home.textContent).toMatch(/\+106/)
    expect(home.textContent).toMatch(/Caesars/)
    expect(home.textContent).toMatch(/also/)
    expect(home).toHaveAttribute('data-actionable', 'true')
    expect(away).not.toHaveAttribute('data-actionable')
    expect(within(screen.getByTestId('line-tiles')).getAllByTestId('line-tile')).toHaveLength(6)   // three lines, two sides each
  })
  it('picking a line in the scroller, or a tile, changes the big buttons', () => {
    render(board(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    fireEvent.click(screen.getByTestId('line-chip--2.5'))
    expect(within(screen.getByTestId('side-buttons')).getAllByTestId('side-button')[1].textContent).toMatch(/FLA -2\.5/)
    fireEvent.click(within(screen.getByTestId('line-tiles')).getAllByTestId('line-tile').find(t => t.getAttribute('data-line') === '-1.5')!)
    expect(screen.getByTestId('line-chip--1.5')).toHaveAttribute('aria-pressed', 'true')
  })
  it('a thin line says no edge is claimed, and its tiles carry no EV chip', () => {
    render(board(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    fireEvent.click(screen.getByTestId('line-chip--3.5'))
    expect(screen.getByTestId('side-buttons').parentElement!.textContent).toMatch(/Fewer than 3 books quote this line, so no edge is claimed/)
    const tiles = within(screen.getByTestId('line-tiles')).getAllByTestId('line-tile').filter(t => t.getAttribute('data-line') === '-3.5')
    expect(tiles.every(t => !/%/.test(t.textContent!))).toBe(true)
  })
  it('the total has the same layout, and the moneyline has the two buttons only', () => {
    render(board(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    fireEvent.click(screen.getByTestId('tab-total'))
    expect(screen.getByTestId('market-total')).toBeInTheDocument()
    expect(within(screen.getByTestId('side-buttons')).getAllByTestId('side-button')[0].textContent).toMatch(/O 6\.5/)
    fireEvent.click(screen.getByTestId('tab-ml'))
    expect(screen.queryByTestId('line-scroller')).toBeNull()
    expect(screen.queryByTestId('line-tiles')).toBeNull()
    expect(within(screen.getByTestId('side-buttons')).getAllByTestId('side-button')).toHaveLength(2)
  })
  it('MOVEMENT: the line and the fair price from the history, or an honest empty state', () => {
    const history = { [`${GK}|spread`]: { points: [pt(0, -1.0, 0.50), pt(60, -1.0, 0.52), pt(120, -1.5, 0.44), pt(180, -1.5, 0.46)] } }
    const a = render(board(EDGE, { history }))
    open(screen.getAllByTestId('board-row')[0])
    fireEvent.click(screen.getByTestId('tab-move'))
    const sp = within(screen.getByTestId('move-spread'))
    expect(sp.getAllByTestId('sparkline')).toHaveLength(2)
    expect(sp.getByText(/FLA -1 \u2192 -1\.5/)).toBeInTheDocument()
    expect(screen.getByTestId('move-spread').textContent).toMatch(/56\.0% \u2192 54\.0% \(-2\.0 pts, FLA\)/)
    a.unmount()
    render(board(EDGE, { history: {} }))
    open(screen.getAllByTestId('board-row')[0])
    fireEvent.click(screen.getByTestId('tab-move'))
    expect(screen.getByTestId('move-empty').textContent).toMatch(/not enough snapshots/)
  })
  it('MOVEMENT says it is not available when the backend sends no history at all', () => {
    render(board(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    fireEvent.click(screen.getByTestId('tab-move'))
    expect(screen.getByTestId('move-empty').textContent).toMatch(/not available yet/)
  })
  it('MODEL: one sentence, the main lines as model against market, the drivers and one chart, with a link to open the page', () => {
    render(withModel(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    fireEvent.click(screen.getByTestId('tab-analysis'))
    const pane = within(screen.getByTestId('model-pane'))
    expect(pane.getByTestId('model-verdict').textContent).toMatch(/^Our model leans .*against the market's .* pts\)\.$/)
    expect(pane.getByTestId('model-score').textContent).toMatch(/DET 2\.4 \u2013 FLA 3\.1/)
    expect(pane.getAllByTestId(/^model-row-/).map(r => r.getAttribute('data-testid'))).toEqual(['model-row-ml', 'model-row-spread', 'model-row-total'])
    expect(within(pane.getByTestId('model-drivers')).getByText('Goalies')).toBeInTheDocument()
    expect(pane.getByTestId('model-chart')).toBeInTheDocument()
    expect(screen.queryByTestId('heat-strip')).toBeNull()        // the book grid is under the markets now, not here
    expect(screen.queryByTestId('game-detail')).toBeNull()
    expect(screen.getByRole('link', { name: /Open as a page/ })).toHaveAttribute('href', `/ev/games?view=analysis&game=${encodeURIComponent(GK)}`)
  })
  it('MODEL says so honestly when the game has no projection', () => {
    render(board(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    fireEvent.click(screen.getByTestId('tab-analysis'))
    expect(screen.getByTestId('model-empty').textContent).toMatch(/no model projection/)
  })
  it('ALL BOOKS: every book on every line sits collapsed under a market, and opens as a grid for either side', () => {
    render(board(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    expect(screen.queryByTestId('heat-strip')).toBeNull()
    fireEvent.click(screen.getByTestId('all-books-toggle'))
    expect(screen.getByTestId('heat-strip')).toBeInTheDocument()
    expect(screen.getByTestId('heat-cell--1.5-caesars').getAttribute('data-ev')).not.toBe('')
    expect(screen.getByRole('group', { name: 'Side' })).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('all-books-toggle'))
    expect(screen.queryByTestId('heat-strip')).toBeNull()
  })
})

describe('on a phone', () => {
  beforeEach(() => {
    window.matchMedia = ((query: string) => ({ matches: true, media: query, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false })) as unknown as typeof window.matchMedia
  })
  afterEach(() => { delete (window as { matchMedia?: unknown }).matchMedia })
  it('a single tap opens the card, since a double-tap zooms', () => {
    render(board(EDGE))
    fireEvent.click(screen.getAllByTestId('board-row')[0])
    expect(screen.getByTestId('game-popup')).toBeInTheDocument()
  })
  it('a row is one stacked cell, with the edge under the game and no Edge or Card column to scroll sideways', () => {
    render(board(EDGE))
    expect(screen.queryByRole('columnheader', { name: 'Edge' })).toBeNull()
    expect(screen.queryByRole('columnheader', { name: 'Card' })).toBeNull()
    const row = screen.getAllByTestId('board-row')[0]
    expect(within(row.querySelector('td')!).getByTestId('row-edge')).toBeInTheDocument()
  })
})
