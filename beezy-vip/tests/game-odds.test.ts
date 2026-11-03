import {
  ageText, buildGamePriceRows, freshnessBin, effectiveBettable, filterGamePriceRows, gameArbHedgeText, gameBookColumns, gameOddsQueryString, lineLabel, parseGameOddsQuery,
  sideLabels, signedLine, sortGamePriceRows, toBoardRow, type GameOddsRow,
} from '../lib/game-odds'
import { computeRowArb, kalshiEffectiveDecimal } from '../lib/odds-screen'

const row = (over: Partial<GameOddsRow> & { books: GameOddsRow['books'] }): GameOddsRow => ({
  key: 'nba:2026-11-02:BOS@MIA|ml|', sport: 'nba', game_key: 'nba:2026-11-02:BOS@MIA', away: 'BOS', home: 'MIA', commence: '2026-11-03T00:30:00Z', market: 'ml', line: null, ...over,
})
const q = (book: string, a: number | null, b: number | null) => ({ book, a, b })
const base = { refs: [] as string[], method: 'proportional' as const, bettable: null }

// four books: three flat -110/-110 and one that pays +105 on the away side
const ML = row({ books: [q('draftkings', -110, -110), q('fanduel', -110, -110), q('betmgm', -110, -110), q('caesars', 105, -125)] })

describe('adapter: a = away / over, b = home / under', () => {
  it('maps a to over and b to under on the terminal BoardRow', () => {
    const b = toBoardRow(ML)
    expect(b.books[3]).toEqual({ book: 'caesars', over: 105, under: -125 })
    expect(toBoardRow(ML)).toBe(b)   // cached per row object
  })
  it('the best A price is the best away price and the best B price the best home price', () => {
    const [r] = buildGamePriceRows([ML], base)
    expect(r.a).toMatchObject({ book: 'caesars', american: 105, label: 'BOS ML' })
    expect(r.b).toMatchObject({ american: -110, label: 'MIA ML' })
    expect(r.b.ties).toEqual(['draftkings', 'fanduel', 'betmgm'])
  })
})

describe('labels and the spread line sign', () => {
  it('home line -3.5 gives the away side +3.5', () => {
    const s = row({ market: 'spread', line: -3.5, books: [] })
    expect(sideLabels(s)).toEqual({ a: 'BOS +3.5', b: 'MIA -3.5' })
    expect(lineLabel(s)).toBe('MIA -3.5')
    expect(sideLabels(row({ market: 'spread', line: 2, books: [] }))).toEqual({ a: 'BOS -2', b: 'MIA +2' })
  })
  it('a pick-em is PK and a total is O / U with the points', () => {
    expect(signedLine(0)).toBe('PK')
    expect(sideLabels(row({ market: 'spread', line: 0, books: [] }))).toEqual({ a: 'BOS PK', b: 'MIA PK' })
    const t = row({ market: 'total', line: 224.5, books: [] })
    expect(sideLabels(t)).toEqual({ a: 'O 224.5', b: 'U 224.5' })
    expect(lineLabel(t)).toBe('O/U 224.5')
    expect(lineLabel(row({ books: [] }))).toBe('ML')
  })
  it('formats the age', () => {
    expect(ageText(12)).toBe('12 min')
    expect(ageText(150)).toBe('2.5 h')
    expect(ageText(null)).toBe('--')
  })
})

describe('fair value and EV', () => {
  it('de-vigs two books to a fair probability: -110 / -110 is exactly 50%', () => {
    // four flat books: leaving the best-price book out still leaves the three reference books a fair value needs
    const only = row({ books: [q('draftkings', -110, -110), q('fanduel', -110, -110), q('betmgm', -110, -110), q('caesars', -110, -110)] })
    const [r] = buildGamePriceRows([only], base)
    expect(r.a.pFair).toBeCloseTo(0.5, 10)
    expect(r.b.pFair).toBeCloseTo(0.5, 10)
    expect(r.a.nRef).toBe(3)
  })
  it('ML fair probability from a +105 / -125 book: 100/205 vs 125/225 de-vigged', () => {
    const [r] = buildGamePriceRows([row({ books: [q('draftkings', -110, -110), q('fanduel', -110, -110), q('betmgm', -110, -110), q('caesars', 105, -125)] })], base)
    // side B is best at draftkings (first of the ties); the reference books left are fanduel, betmgm (0.5 each) and caesars
    const caesarsOver = (100 / 205) / (100 / 205 + 125 / 225)
    expect(r.b.pFair).toBeCloseTo(1 - (0.5 + 0.5 + caesarsOver) / 3, 10)
    expect(r.b.ev).toBeCloseTo((1 - (1 + caesarsOver) / 3) * (100 / 110 + 1) - 1, 6)
  })
  it('leave-self-out: the book holding the best price never anchors its own fair value', () => {
    const [r] = buildGamePriceRows([ML], base)
    // best A is caesars; without caesars the other three are 50 / 50 -> fair 0.5 and EV = 0.5 * 2.05 - 1
    expect(r.a.nRef).toBe(3)
    expect(r.a.pFair).toBeCloseTo(0.5, 10)
    expect(r.a.ev).toBeCloseTo(0.025, 10)
    expect(r.bestEv).toBeGreaterThan(0.024)
  })
  it('fewer than 3 reference books: the row is priced but has no fair value or EV', () => {
    const [r] = buildGamePriceRows([ML], { ...base, refs: ['draftkings', 'fanduel'] })
    expect(r.a.american).toBe(105)
    expect(r.a.pFair).toBeNull()
    expect(r.a.ev).toBeNull()
    expect(r.b.pFair).toBeNull()
    expect(r.bestEv).toBeNull()
    expect(r.a.stake).toBe(0)
    // exactly the chosen reference books count: three of them are enough
    const [three] = buildGamePriceRows([ML], { ...base, refs: ['draftkings', 'fanduel', 'betmgm'] })
    expect(three.a.pFair).toBeCloseTo(0.5, 10)
  })
  it('the power method gives a different fair value on a vigged market than proportional', () => {
    const lop = row({ books: [q('a', -200, 170), q('b', -200, 170), q('c', -200, 170), q('d', -190, 160)] })
    const [prop] = buildGamePriceRows([lop], base)
    const [pow] = buildGamePriceRows([lop], { ...base, method: 'power' })
    expect(prop.b.pFair).not.toBeNull()
    expect(pow.b.pFair).not.toBeCloseTo(prop.b.pFair as number, 4)
  })
  it('sizes a 1/4 Kelly stake only when the side has an edge', () => {
    const [r] = buildGamePriceRows([ML], { ...base, bankroll: 1000 })
    expect(r.a.stake).toBeGreaterThan(0)
    expect(r.b.stake).toBe(0)
  })
})

describe('arbitrage', () => {
  const ARB = row({ key: 'arb', books: [q('x', 120, -150), q('y', -150, 120), q('z', -110, -110)] })
  it('best A at one book and best B at a different book, net profit on $100', () => {
    const [r] = buildGamePriceRows([ARB], base)
    expect(r.arbLive).toBe(true)
    expect(r.arb).toMatchObject({ bestOverBook: 'x', bestOverAm: 120, bestUnderBook: 'y', bestUnderAm: 120, feeLeg: false })
    // 1/2.2 + 1/2.2 = 0.909: $50 on each leg returns $110, so $10 locked on $100
    expect(r.arb!.guaranteedProfitPer100).toBeCloseTo(10, 6)
    expect(r.arb!.netArbPct).toBeCloseTo(10, 6)
    expect(r.arb!.stakeOverPer100).toBeCloseTo(50, 6)
  })
  it('one book never supplies both legs', () => {
    const one = row({ books: [q('x', 130, 130)] })
    const [r] = buildGamePriceRows([one], base)
    expect(r.arb).toBeNull()
    expect(r.arbLive).toBe(false)
  })
  it('an offshore book is shown but never the best price or an arbitrage leg', () => {
    const withOff = row({ books: [q('draftkings', -110, -110), q('fanduel', -110, -110), q('betmgm', -110, -110), q('pinnacle', 400, 400)] })
    const [r] = buildGamePriceRows([withOff], { ...base, offshore: ['pinnacle'] })
    expect(r.a.american).toBe(-110)
    expect(r.a.ties).not.toContain('pinnacle')
    expect(r.arbLive).toBe(false)
    // an older feed that forgets to list it still cannot make it best: lib/offshore.ts is applied on top
    const [r2] = buildGamePriceRows([withOff], base)
    expect(r2.a.book).not.toBe('pinnacle')
    expect(effectiveBettable([withOff], { bettable: null, offshore: [] }).has('pinnacle')).toBe(false)
    // ... and a custom offshore book from the feed is honoured
    const custom = row({ books: [q('draftkings', -110, -110), q('sharpbk', 400, -110)] })
    const [r3] = buildGamePriceRows([custom], { ...base, offshore: new Set(['sharpbk']) })
    expect(r3.a.book).toBe('draftkings')
  })
  it('bettable limits the best price to books the viewer can use', () => {
    const [r] = buildGamePriceRows([ML], { ...base, bettable: new Set(['draftkings', 'fanduel']) })
    expect(r.a.book).toBe('draftkings')
    expect(r.a.american).toBe(-110)
  })
  it('Kalshi: its taker fee comes off its leg, so the net margin is below the gross', () => {
    const k = row({ books: [q('kalshi', 120, -150), q('y', -150, 120)] })
    const [r] = buildGamePriceRows([k], base)
    expect(r.arb!.feeLeg).toBe(true)
    const eff = kalshiEffectiveDecimal(2.2)
    expect(r.arb!.netArbPct).toBeCloseTo((1 / (1 / eff + 1 / 2.2) - 1) * 100, 6)
    expect(r.arb!.netArbPct).toBeLessThan(r.arb!.arbPct)
    expect(r.arb!.netArbPct).toBeCloseTo(7.95, 1)
    expect(r.arb).toEqual(computeRowArb(toBoardRow(k), new Set(['kalshi', 'y']), true))
    // exchanges off: Kalshi leaves the search, and with it the arbitrage
    const [off] = buildGamePriceRows([k], { ...base, includeExchanges: false })
    expect(off.arb).toBeNull()
  })
  it('builds a pasteable hedge line', () => {
    const [r] = buildGamePriceRows([ARB], base)
    const text = gameArbHedgeText(r, 200)!
    expect(text).toContain('BOS @ MIA Moneyline ML')
    expect(text).toContain('BOS ML +120')
    expect(text).toContain('MIA ML +120')
    expect(text).toContain('$20.00 locked on $200.00')
    expect(gameArbHedgeText({ ...r, arb: null }, 100)).toBeNull()
  })
})

describe('rows, filters and sorting', () => {
  const spreadA = row({ key: 'sp-a', market: 'spread', line: -3.5, books: [q('draftkings', -105, -115), q('fanduel', -110, -110), q('betmgm', -110, -110), q('caesars', -108, -112)] })
  const spreadB = row({ key: 'sp-b', market: 'spread', line: -4.5, books: [q('draftkings', 100, -120)] })
  const other = row({ key: 'tor', game_key: 'nhl:TOR@MTL', sport: 'nhl', away: 'TOR', home: 'MTL', commence: '2026-11-02T23:00:00Z', market: 'total', line: 6, books: [q('draftkings', -110, -110)] })
  const rows = buildGamePriceRows([ML, spreadA, spreadB, other], base)
  const filters = { market: 'all' as const, game: 'all', q: '', minEv: -1, arbOnly: false }

  it('different lines of one game are separate rows', () => {
    expect(rows.filter(r => r.row.game_key === ML.game_key && r.row.market === 'spread')).toHaveLength(2)
    expect(rows).toHaveLength(4)
  })
  it('filters by market, game, team search and EV floor', () => {
    expect(filterGamePriceRows(rows, { ...filters, market: 'spread' })).toHaveLength(2)
    expect(filterGamePriceRows(rows, { ...filters, game: 'nhl:TOR@MTL' }).map(r => r.key)).toEqual(['tor'])
    expect(filterGamePriceRows(rows, { ...filters, q: 'mtl' }).map(r => r.key)).toEqual(['tor'])
    // rows with no fair value only pass the 'All rows' floor
    expect(filterGamePriceRows(rows, { ...filters, minEv: 0 }).every(r => r.bestEv != null)).toBe(true)
    expect(filterGamePriceRows(rows, { ...filters, minEv: 0.9 })).toHaveLength(0)
  })
  it('sorts by EV (rows without a fair value last), by start time and by game', () => {
    const byEv = sortGamePriceRows(rows, 'ev')
    expect(byEv[0].key).toBe(ML.key)
    expect(byEv[byEv.length - 1].bestEv).toBeNull()
    expect(sortGamePriceRows(rows, 'time')[0].key).toBe('tor')
    expect(sortGamePriceRows(rows, 'game').map(r => r.matchup)).toEqual(['BOS @ MIA', 'BOS @ MIA', 'BOS @ MIA', 'TOR @ MTL'])
  })
  it('a live arbitrage clears the EV floor and ranks on its locked margin', () => {
    const live = buildGamePriceRows([row({ key: 'arb', books: [q('x', 120, -150), q('y', -150, 120)] }), ML], base)
    expect(filterGamePriceRows(live, { ...filters, minEv: 0.05 }).map(r => r.key)).toEqual(['arb'])
    expect(filterGamePriceRows(live, { ...filters, arbOnly: true }).map(r => r.key)).toEqual(['arb'])
    expect(sortGamePriceRows(live, 'ev')[0].key).toBe('arb')
  })
  it('lists book columns sharp-first with offshore books flagged', () => {
    const cols = gameBookColumns([ML, row({ key: 'p', books: [q('pinnacle', -110, -110), q('draftkings', -110, -110)] })], ['pinnacle'])
    expect(cols[0]).toMatchObject({ key: 'pinnacle', offshore: true })
    expect(cols.map(c => c.key).slice(1, 3)).toEqual(['draftkings', 'fanduel'])
  })
})

describe('query validation', () => {
  const p = (s: string) => parseGameOddsQuery(new URLSearchParams(s))
  it('accepts only nba / nhl, a real day, a known market and a bounded limit', () => {
    expect(p('sport=nba&day=2026-11-02&market=spread&limit=100')).toEqual({ ok: true, query: { sport: 'nba', day: '2026-11-02', market: 'spread', limit: 100 } })
    for (const bad of ['', 'sport=mlb', 'sport=nfl', 'sport=nba&day=2026-02-31', 'sport=nba&market=prop', 'sport=nba&limit=0', 'sport=nba&limit=5001', 'sport=nba&limit=1e3']) {
      expect(p(bad).ok).toBe(false)
    }
  })
  it('serialises only the validated fields', () => {
    expect(gameOddsQueryString({ sport: 'nhl', market: 'ml', limit: 5 })).toBe('sport=nhl&market=ml&limit=5')
  })
})

describe('freshnessBin', () => {
  it('bins the age into green up to 15 min, amber up to an hour, red beyond', () => {
    expect(freshnessBin(0)).toBe('fresh')
    expect(freshnessBin(15)).toBe('fresh')
    expect(freshnessBin(15.1)).toBe('aging')
    expect(freshnessBin(60)).toBe('aging')
    expect(freshnessBin(61)).toBe('old')
  })
  it('is red when the feed says stale, and unknown with no age', () => {
    expect(freshnessBin(2, true)).toBe('old')
    expect(freshnessBin(null)).toBeNull()
    expect(freshnessBin(NaN)).toBeNull()
  })
})
