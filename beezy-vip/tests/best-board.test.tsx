import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { BestBoard } from '@/app/ev/games/best-board'
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
  it('a chip opens that game\'s card, on the Alt lines pane when the edge is on an alt line', () => {
    // make the -1.5 line the main line (most books) with no edge, and put the edge on the -2.5 alt
    const wide = [mk('spread', -1.5, [...FLAT, q('espnbet', -110, -110), q('hardrock', -110, -110), q('betrivers', -110, -110)]), mk('spread', -2.5, [...FLAT, q('caesars', -140, 125)])]
    render(board(wide))
    fireEvent.click(screen.getAllByTestId('edge-chip')[0])
    expect(screen.getByTestId('alt-pane')).toBeInTheDocument()
  })
})

describe('the card (double-click)', () => {
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
  it('SHOP: both sides of each market with the best price at its book, the next best elsewhere, and an EV chip only for a real edge', () => {
    render(board(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    const pane = within(screen.getByTestId('shop-pane'))
    expect(pane.getAllByTestId('shop-row').length).toBe(6)         // moneyline, spread, total: two sides each
    const spreadHome = pane.getAllByTestId('shop-row').find(r => /FLA -1\.5/.test(r.textContent!))!
    expect(spreadHome.textContent).toMatch(/\+106/)
    expect(spreadHome.textContent).toMatch(/also/)
    expect(within(spreadHome).getByTestId('price-chip').textContent).toMatch(/\+\d+\.\d%/)
    const total = pane.getAllByTestId('shop-row').find(r => /O 6\.5/.test(r.textContent!))!
    expect(total.textContent).not.toMatch(/%/)                     // a tight total shows no EV chip
  })
  it('MOVEMENT: the line and the fair price from the history, or an honest empty state', () => {
    const history = { [`${GK}|spread`]: { points: [pt(0, -1.0, 0.50), pt(60, -1.0, 0.52), pt(120, -1.5, 0.44), pt(180, -1.5, 0.46)] } }
    const a = render(board(EDGE, { history }))
    open(screen.getAllByTestId('board-row')[0])
    fireEvent.click(screen.getByRole('button', { name: 'Movement' }))
    const sp = within(screen.getByTestId('move-spread'))
    expect(sp.getAllByTestId('sparkline')).toHaveLength(2)
    expect(sp.getByText(/FLA -1 → -1\.5/)).toBeInTheDocument()
    expect(screen.getByTestId('move-spread').textContent).toMatch(/56\.0% → 54\.0% \(-2\.0 pts, FLA\)/)
    a.unmount()
    render(board(EDGE, { history: {} }))
    open(screen.getAllByTestId('board-row')[0])
    fireEvent.click(screen.getByRole('button', { name: 'Movement' }))
    expect(screen.getByTestId('move-empty').textContent).toMatch(/not enough snapshots/)
  })
  it('MOVEMENT says it is not available when the backend sends no history at all', () => {
    render(board(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    fireEvent.click(screen.getByRole('button', { name: 'Movement' }))
    expect(screen.getByTestId('move-empty').textContent).toMatch(/not available yet/)
  })
  it('ALT LINES: every line with both sides, the main marked, thin lines flagged and no edge claimed on them', () => {
    render(board(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    fireEvent.click(screen.getByRole('button', { name: 'Alt lines' }))
    const rows = within(screen.getByTestId('alt-pane')).getAllByTestId('alt-row')
    expect(rows.map(r => r.getAttribute('data-line'))).toEqual(['-3.5', '-2.5', '-1.5'])
    expect(rows.find(r => r.getAttribute('data-line') === '-1.5')!.textContent).toMatch(/MAIN/)
    const thin = rows.find(r => r.getAttribute('data-line') === '-3.5')!
    expect(thin.textContent).toMatch(/THIN/)
    expect(within(thin).queryAllByTestId('price-chip').every(c => !/%/.test(c.textContent!))).toBe(true)
  })
  it('links to the full analysis of that game', () => {
    render(board(EDGE))
    open(screen.getAllByTestId('board-row')[0])
    expect(screen.getByRole('link', { name: /Full analysis/ })).toHaveAttribute('href', `/ev/games?view=analysis&game=${encodeURIComponent(GK)}`)
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
})
