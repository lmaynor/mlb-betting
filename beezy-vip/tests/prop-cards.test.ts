import type { BoardRow } from '@/lib/board-rows'
import { cardGate, cockpitHref, hasCardLayout, resolveLayout, sortCards } from '@/lib/prop-cards'

function makeRow(overrides: Partial<BoardRow> = {}): BoardRow {
  return {
    key: 'nba:game1|player_one|pts',
    sport: 'nba',
    system: 'NBA_PTS',
    marketLabel: 'Points',
    player: 'Player One',
    headshotId: 100,
    team: 'BOS',
    matchup: 'BOS @ MIA',
    gameKey: 'nba:game1',
    away: 'BOS',
    home: 'MIA',
    start: '2026-11-17T20:00:00Z',
    side: 'over',
    line: 20.5,
    hasLine: true,
    proj: 22.4,
    avg10: 21.0,
    avgSzn: 20.5,
    pModel: 0.58,
    pFair: 0.52,
    pBlend: 0.55,
    nBooks: 4,
    l5: 0.6,
    l10: 0.7,
    odds: -110,
    book: 'draftkings',
    modelEdge: 0.06,
    marketEv: 0.04,
    confluence: true,
    score: 60,
    isPick: false,
    result: null,
    engine: 'Hierarchical',
    tier: null,
    p10: 14.0,
    p90: 30.0,
    ...overrides,
  }
}

describe('cardGate', () => {
  it('filters out unpriced rows (missing odds, book, or no line)', () => {
    const unpricedOdds = makeRow({ odds: null })
    const unpricedBook = makeRow({ book: null })
    const noLine = makeRow({ hasLine: false })
    const valid = makeRow()

    expect(cardGate([unpricedOdds, unpricedBook, noLine, valid])).toEqual([valid])
  })

  it('filters out rows with fewer than 3 reference books', () => {
    const twoBooks = makeRow({ nBooks: 2 })
    const zeroBooks = makeRow({ nBooks: 0 })
    const nullBooks = makeRow({ nBooks: null })
    const threeBooks = makeRow({ nBooks: 3 })
    const fiveBooks = makeRow({ nBooks: 5 })

    expect(cardGate([twoBooks, zeroBooks, nullBooks, threeBooks, fiveBooks])).toEqual([threeBooks, fiveBooks])
  })

  it('filters out rows with negative EV and score under 35', () => {
    const negEvLowScore = makeRow({ marketEv: -0.02, score: 34 })
    expect(cardGate([negEvLowScore])).toEqual([])
  })

  it('keeps rows with negative EV if score is 35 or higher', () => {
    const negEvScore35 = makeRow({ marketEv: -0.02, score: 35 })
    expect(cardGate([negEvScore35])).toEqual([negEvScore35])
  })

  it('keeps rows with positive EV even when score is 0', () => {
    const posEvScore0 = makeRow({ marketEv: 0.01, score: 0 })
    expect(cardGate([posEvScore0])).toEqual([posEvScore0])
  })

  it('preserves input order', () => {
    const r1 = makeRow({ key: 'k1', marketEv: 0.05, score: 50 })
    const r2 = makeRow({ key: 'k2', marketEv: -0.01, score: 40 })
    const r3 = makeRow({ key: 'k3', marketEv: 0.02, score: 10 })

    expect(cardGate([r1, r2, r3])).toEqual([r1, r2, r3])
  })
})

describe('sortCards', () => {
  it('sorts by ev desc with score tiebreak and stable input order', () => {
    const r1 = makeRow({ key: 'k1', marketEv: 0.05, score: 40 })
    const r2 = makeRow({ key: 'k2', marketEv: 0.10, score: 20 })
    const r3 = makeRow({ key: 'k3', marketEv: 0.05, score: 60 })
    const r4 = makeRow({ key: 'k4', marketEv: null, score: 70 })

    const sorted = sortCards([r1, r2, r3, r4], 'ev')
    expect(sorted.map(r => r.key)).toEqual(['k2', 'k3', 'k1', 'k4'])
  })

  it('sorts by score desc with marketEv tiebreak', () => {
    const r1 = makeRow({ key: 'k1', score: 50, marketEv: 0.02 })
    const r2 = makeRow({ key: 'k2', score: 70, marketEv: 0.01 })
    const r3 = makeRow({ key: 'k3', score: 50, marketEv: 0.05 })

    const sorted = sortCards([r1, r2, r3], 'score')
    expect(sorted.map(r => r.key)).toEqual(['k2', 'k3', 'k1'])
  })

  it('sorts by time asc with null start last and marketEv tiebreak', () => {
    const r1 = makeRow({ key: 'k1', start: '2026-11-17T22:00:00Z', marketEv: 0.05 })
    const r2 = makeRow({ key: 'k2', start: '2026-11-17T19:00:00Z', marketEv: 0.02 })
    const r3 = makeRow({ key: 'k3', start: null, marketEv: 0.10 })
    const r4 = makeRow({ key: 'k4', start: '2026-11-17T19:00:00Z', marketEv: 0.08 })

    const sorted = sortCards([r1, r2, r3, r4], 'time')
    expect(sorted.map(r => r.key)).toEqual(['k4', 'k2', 'k1', 'r3'].map(k => (k === 'r3' ? 'k3' : k)))
  })
})

describe('resolveLayout', () => {
  it('param beats stored beats viewport', () => {
    expect(resolveLayout({ param: 'cards', stored: 'table', isMobile: false })).toBe('cards')
    expect(resolveLayout({ param: 'table', stored: 'cards', isMobile: true })).toBe('table')
  })

  it('stored beats viewport when param is null', () => {
    expect(resolveLayout({ param: null, stored: 'cards', isMobile: false })).toBe('cards')
    expect(resolveLayout({ param: null, stored: 'table', isMobile: true })).toBe('table')
  })

  it('falls back to desktop default table', () => {
    expect(resolveLayout({ param: null, stored: null, isMobile: false })).toBe('table')
  })

  it('falls back to mobile default cards', () => {
    expect(resolveLayout({ param: null, stored: null, isMobile: true })).toBe('cards')
  })
})

describe('cockpitHref', () => {
  it('formats cockpit href correctly', () => {
    const row = makeRow({ sport: 'nba', key: 'nba:game1|player_one|pts' })
    expect(cockpitHref(row)).toBe(`/edge?sport=nba&prop=${encodeURIComponent('proj|nba|nba:game1|player_one|pts')}`)
  })

  it('formats an NHL cockpit href with the nhl sport', () => {
    const row = makeNhlRow()
    expect(cockpitHref(row)).toBe(`/edge?sport=nhl&prop=${encodeURIComponent('proj|nhl|nhl:2026-11-16:VAN@EDM|connor_mcdavid|sog')}`)
  })
})

function makeNhlRow(overrides: Partial<BoardRow> = {}): BoardRow {
  return {
    key: 'nhl:2026-11-16:VAN@EDM|connor_mcdavid|sog',
    sport: 'nhl',
    system: 'NHL_SOG',
    marketLabel: 'Shots on Goal',
    player: 'Connor McDavid',
    headshotId: 8478402,
    team: 'EDM',
    matchup: 'VAN @ EDM',
    gameKey: 'nhl:2026-11-16:VAN@EDM',
    away: 'VAN',
    home: 'EDM',
    start: '2026-11-17T02:00:00Z',
    side: 'over',
    line: 3.5,
    hasLine: true,
    proj: 4.1,
    avg10: 3.8,
    avgSzn: 3.9,
    pModel: 0.57,
    pFair: 0.52,
    pBlend: 0.55,
    nBooks: 7,
    l5: 0.6,
    l10: 0.6,
    odds: 110,
    book: 'novig',
    modelEdge: 0.05,
    marketEv: 0.06,
    confluence: false,
    score: 55,
    isPick: false,
    result: null,
    engine: 'Hierarchical',
    tier: null,
    p10: 1.0,
    p90: 7.0,
    ...overrides,
  }
}

describe('hasCardLayout', () => {
  it('enables the cards/table switch for NBA and NHL only', () => {
    expect(hasCardLayout('nba')).toBe(true)
    expect(hasCardLayout('nhl')).toBe(true)
    expect(hasCardLayout('nfl')).toBe(false)
    expect(hasCardLayout('mlb')).toBe(false)
    expect(hasCardLayout('soccer')).toBe(false)
  })
})

describe('cardGate on NHL-shaped rows', () => {
  it('keeps a qualifying NHL row (priced, 7 books, positive EV)', () => {
    const row = makeNhlRow()
    expect(cardGate([row])).toEqual([row])
  })

  it('drops an NHL row with no two-sided consensus (no blend, no market EV, low score)', () => {
    const noConsensus = makeNhlRow({ pBlend: null, marketEv: null, score: 20 })
    expect(cardGate([noConsensus])).toEqual([])
  })

  it('keeps a negative-EV NHL row when the score clears 35 (saves shape)', () => {
    const saves = makeNhlRow({ system: 'NHL_SAVES', marketLabel: 'Saves', marketEv: -0.01, score: 42, nBooks: 4 })
    expect(cardGate([saves])).toEqual([saves])
  })

  it('drops an NHL row priced by fewer than 3 books even with positive EV', () => {
    const thin = makeNhlRow({ nBooks: 2, marketEv: 0.09 })
    expect(cardGate([thin])).toEqual([])
  })

  it('sorts NHL cards by EV desc with the same rules as NBA', () => {
    const a = makeNhlRow({ key: 'a', marketEv: 0.03, score: 60 })
    const b = makeNhlRow({ key: 'b', marketEv: 0.08, score: 40 })
    expect(sortCards([a, b], 'ev').map(r => r.key)).toEqual(['b', 'a'])
  })
})
