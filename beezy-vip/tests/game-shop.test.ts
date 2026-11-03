import { buildGameBoard } from '../lib/game-board'
import { ALT_MIN_BOOKS, altRows, EDGE_MIN, LONGSHOT_MAX_AMERICAN, edgesNow, gameEdges, gameHeadline, shopMarket, shopSide } from '../lib/game-shop'
import type { GameOddsFeed, GameOddsRow } from '../lib/game-odds'

const q = (book: string, a: number | null, b: number | null) => ({ book, a, b })
const GK = 'nhl:2026-11-02:DET@FLA'
const mk = (market: GameOddsRow['market'], line: number | null, books: GameOddsRow['books'], gk = GK, away = 'DET', home = 'FLA'): GameOddsRow =>
  ({ key: `${gk}|${market}|${line ?? ''}`, sport: 'nhl', game_key: gk, away, home, commence: '2026-11-03T00:10:00Z', market, line, books })
const feedOf = (rows: GameOddsRow[]): GameOddsFeed => ({ sport: 'nhl', status: 'live', as_of: null, age_minutes: 3, stale: false, validated: false, lines_source: 'x', count: rows.length, books: [], offshore_books: [], games: [], rows })
const base = { refs: [] as string[], method: 'proportional' as const, bettable: null }
const board = (rows: GameOddsRow[]) => buildGameBoard({ odds: { nhl: feedOf(rows) }, proj: {} }, base)

// five books at -110/-110 and one that pays +106 on the home (b) side: a clear but believable edge
const FLAT = ['draftkings', 'fanduel', 'betmgm', 'novig', 'kalshi'].map(k => q(k, -110, -110))
const EDGE_ROWS = [mk('spread', -1.5, [...FLAT, q('caesars', -125, 106)]), mk('total', 6.5, [...FLAT, q('caesars', -110, -110)]), mk('ml', null, ['draftkings', 'fanduel', 'betmgm', 'caesars'].map(k => q(k, 120, -140)))]

describe('shopSide', () => {
  it('names the best price, its book, the next best prices elsewhere and the EV against fair', () => {
    const [g] = board(EDGE_ROWS)
    const s = shopSide(g.main.spread!, 'b')
    expect(s.label).toBe('FLA -1.5')
    expect(s).toMatchObject({ american: 106, book: 'caesars' })
    expect(s.others.map(o => o.book)).toHaveLength(3)
    expect(s.others.every(o => o.american === -110)).toBe(true)
    expect(s.ev!).toBeGreaterThan(EDGE_MIN)
    expect(s.actionable).toBe(true)
  })
  it('never offers an offshore book as the other prices', () => {
    const rows = [mk('spread', -1.5, [...FLAT, q('pinnacle', -125, 120)])]
    const [g] = board(rows)
    expect(shopSide(g.main.spread!, 'b').others.map(o => o.book)).not.toContain('pinnacle')
  })
  it('a price that does not beat fair is not actionable, and a huge one is suspect, not actionable', () => {
    const [g] = board(EDGE_ROWS)
    expect(shopSide(g.main.spread!, 'a').actionable).toBe(false)
    const [h] = board([mk('spread', -1.5, [...FLAT, q('caesars', -125, 400)])])
    const s = shopSide(h.main.spread!, 'b')
    expect(s.suspect).toBe(true)
    expect(s.actionable).toBe(false)
  })
  it('a line with fewer than three books is never actionable', () => {
    const [g] = board([mk('spread', -3.5, [q('draftkings', -300, 240), q('fanduel', -280, 400)])])
    expect(shopSide(g.main.spread!, 'b').actionable).toBe(false)
  })
})

describe('what counts as an edge: not a longshot artifact, not a thin alt', () => {
  it('a price longer than +300 is never actionable, however good it looks', () => {
    expect(LONGSHOT_MAX_AMERICAN).toBe(300)
    // five books at -110/-110 and one paying +400 for a 20%+ looking gap: shown, not headlined
    const [g] = board([mk('spread', -1.5, [...FLAT, q('caesars', -600, 400)])])
    expect(shopSide(g.main.spread!, 'b').american).toBe(400)
    expect(shopSide(g.main.spread!, 'b').actionable).toBe(false)
  })
  it('an alt line needs five books, the main line only three', () => {
    expect(ALT_MIN_BOOKS).toBe(5)
    const four = ['draftkings', 'fanduel', 'betmgm'].map(k => q(k, -110, -110))
    const [g] = board([mk('spread', -1.5, [...FLAT, q('espnbet', -110, -110), q('hardrock', -110, -110), q('betrivers', -110, -110)]), mk('spread', -2.5, [...four, q('caesars', -140, 125)])])
    const alt = g.rows.spread.find(r => r.row.line === -2.5)!
    expect(shopSide(alt, 'b').actionable).toBe(true)                        // judged as a main line it would pass
    expect(shopSide(alt, 'b', { alt: true }).actionable).toBe(false)        // five books are needed on an alt
    expect(gameEdges(g).some(e => e.line === -2.5)).toBe(false)
  })
})

describe('edges and headline', () => {
  it('lists actionable edges biggest first and marks an alt line', () => {
    const rows = [...EDGE_ROWS, mk('spread', -2.5, [...FLAT, q('caesars', -140, 125)])]
    const [g] = board(rows)
    const e = gameEdges(g)
    expect(e.length).toBeGreaterThan(0)
    expect(e.map(x => x.shop.ev!)).toEqual(e.map(x => x.shop.ev!).slice().sort((a, b) => b - a))
    expect(e.find(x => x.line === -2.5)?.alt).toBe(true)
    expect(e.find(x => x.line === -1.5)?.alt).toBe(false)
  })
  it('edgesNow takes the best per game across the slate and honours the cap', () => {
    const other = EDGE_ROWS.map(r => ({ ...r, key: r.key.replace('FLA', 'MTL'), game_key: 'nhl:2026-11-02:TOR@MTL', away: 'TOR', home: 'MTL' }))
    const games = board([...EDGE_ROWS, ...other])
    expect(edgesNow(games, 5, 1)).toHaveLength(2)
    expect(edgesNow(games, 1)).toHaveLength(1)
  })
  it('the headline says where the edge is, or honestly that there is none', () => {
    const [g] = board(EDGE_ROWS)
    expect(gameHeadline(g)).toMatch(/^Best edge: FLA -1\.5 \+106 at Caesars/)
    const [none] = board([mk('spread', -1.5, FLAT), mk('total', 6.5, FLAT)])
    expect(gameHeadline(none)).toBe('No price beats the market fair price by 2% or more on the main lines.')
  })
  it('an edge that exists only on an alt line is named but not headlined as the game\'s edge', () => {
    const wide = [...FLAT, q('espnbet', -110, -110), q('hardrock', -110, -110), q('betrivers', -110, -110)]   // the main line has the most books
    const [g] = board([mk('spread', -1.5, wide), mk('spread', -2.5, [...FLAT, q('caesars', -140, 125)])])
    expect(gameHeadline(g)).toMatch(/On an alt line: FLA -2\.5/)
  })
})

describe('altRows and shopMarket', () => {
  it('lists every line with both sides, marks the main line and thin ones', () => {
    const [g] = board([...EDGE_ROWS, mk('spread', -3.5, [q('draftkings', -300, 240), q('fanduel', -280, 235)])])
    const rows = altRows(g, 'spread')
    expect(rows.map(r => r.line)).toEqual([-3.5, -1.5])
    expect(rows.find(r => r.line === -1.5)!.main).toBe(true)
    expect(rows.find(r => r.line === -3.5)!.thin).toBe(true)
  })
  it('a market the game does not have is null', () => {
    const [g] = board([mk('total', 6.5, FLAT)])
    expect(shopMarket(g, 'spread')).toBeNull()
    expect(shopMarket(g, 'total')!.a.label).toBe('O 6.5')
  })
})

import { primaryMove } from '../lib/game-shop'

describe('primaryMove', () => {
  const pt = (min: number, line: number | null, p_a: number | null) => ({ t: new Date(Date.UTC(2026, 10, 3, 14, min)).toISOString(), line, p_a, best_a: -110, best_b: -110, n: 6 })
  it('prefers the spread, then the total, then the moneyline, and needs real movement', () => {
    const feed = { ...feedOf(EDGE_ROWS), history: {
      [`${GK}|total`]: { points: [pt(0, 6.5, 0.5), pt(60, 6.5, 0.52)] },
      [`${GK}|ml`]: { points: [pt(0, null, 0.4), pt(60, null, 0.45)] },
    } }
    const [g] = buildGameBoard({ odds: { nhl: feed }, proj: {} }, base)
    expect(primaryMove(g)).toMatchObject({ market: 'total', side: 'a' })
    const [h] = buildGameBoard({ odds: { nhl: { ...feed, history: { ...feed.history, [`${GK}|spread`]: { points: [pt(0, -1, 0.5), pt(60, -1.5, 0.45)] } } } }, proj: {} }, base)
    expect(primaryMove(h)).toMatchObject({ market: 'spread', side: 'b' })
    const [none] = buildGameBoard({ odds: { nhl: feedOf(EDGE_ROWS) }, proj: {} }, base)
    expect(primaryMove(none)).toBeNull()
  })
})

import { medianFair } from '../lib/game-shop'

describe('one bad book cannot invent an edge', () => {
  it('the median fair price ignores a single outlier book', () => {
    const [g] = board([mk('total', 8.5, [...FLAT, q('caesars', -600, 525)])])
    const over = medianFair(g.main.total!, 'a', null)!
    expect(over).toBeCloseTo(0.5, 2)               // the mean would be dragged to about 0.56
    expect(medianFair(g.main.total!, 'a', 'draftkings')).toBeCloseTo(0.5, 2)
    expect(medianFair(board([mk('total', 8.5, FLAT.slice(0, 3))])[0].main.total!, 'a', 'draftkings')).toBeNull()   // needs three other books
  })
  it('an outlier book makes no phantom edge on the opposite side', () => {
    const [g] = board([mk('total', 8.5, [...FLAT, q('caesars', -600, 525)])])
    expect(shopSide(g.main.total!, 'a').ev!).toBeGreaterThan(EDGE_MIN)       // the mean-based EV is fooled ...
    expect(shopSide(g.main.total!, 'a').actionable).toBe(false)              // ... the median-based check is not
  })
})
