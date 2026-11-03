import {
  bookX, breakEvenProb, buildLadderRung, decimalOf, flipProb, mainLineOf, orderRungs, selectRung, LADDER_THIN_BELOW,
} from '../lib/game-ladder'
import type { GameOddsRow } from '../lib/game-odds'

const q = (book: string, a: number | null, b: number | null) => ({ book, a, b })
const row = (over: Partial<GameOddsRow> & { books: GameOddsRow['books'] }): GameOddsRow => ({
  key: 'nhl:2026-11-02:DET@FLA|spread|-1.5', sport: 'nhl', game_key: 'nhl:2026-11-02:DET@FLA', away: 'DET', home: 'FLA',
  commence: '2026-11-03T00:10:00Z', market: 'spread', line: -1.5, ...over,
})

// FLA -1.5: the home (b) side pays +123..+134 across the books; DET (a) is the -150s
const MAIN = row({
  books: [q('novig', -150, 123), q('kalshi', -150, 127), q('draftkings', -158, 132), q('fanduel', -157, 131), q('betmgm', -160, 134), q('pinnacle', -151, 130)],
})

describe('dots are plotted at the break-even probability, never 1 - p', () => {
  it('+134 plots at 42.7%: below a 46.7% model line and never above 50%', () => {
    const y = breakEvenProb(134)
    expect(y).toBeCloseTo(0.4274, 4)
    expect(y).toBeLessThan(0.467)
    expect(y).toBeLessThan(0.5)
  })
  it('a better price has a LOWER break-even', () => {
    expect(breakEvenProb(134)).toBeLessThan(breakEvenProb(120))
  })
  it('every dot of a rung is its own price break-even', () => {
    const r = buildLadderRung(MAIN, 'b')
    for (const d of r.dots) expect(d.y).toBeCloseTo(breakEvenProb(d.american), 10)
    expect(Math.max(...r.dots.map(d => d.y))).toBeLessThan(0.5)
  })
})

describe('book bars: right = better price', () => {
  it('+134 (decimal 2.34) is strictly further right than +123 (2.23)', () => {
    const lo = decimalOf(123), hi = decimalOf(134)
    expect(bookX(decimalOf(134), lo, hi)).toBeGreaterThan(bookX(decimalOf(123), lo, hi))
  })
  it('x rises with the payout across the whole rung and stays inside the track', () => {
    const r = buildLadderRung(MAIN, 'b')
    const lo = Math.min(...r.dots.map(d => d.decimal)), hi = Math.max(...r.dots.map(d => d.decimal))
    const xs = r.dots.slice().sort((p, q2) => p.decimal - q2.decimal).map(d => bookX(d.decimal, lo, hi))
    expect(xs).toEqual(xs.slice().sort((p, q2) => p - q2))
    expect(xs[0]).toBeGreaterThanOrEqual(0.1)
    expect(xs[xs.length - 1]).toBeLessThanOrEqual(0.92)
  })
  it('a rung where every book quotes the same price centres the dot', () => {
    expect(bookX(2.2, 2.2, 2.2)).toBeCloseTo(0.51, 10)
  })
})

describe('best price and the two gaps', () => {
  it('the best bettable price is the highest payout; offshore books are shown but never best', () => {
    const r = buildLadderRung(MAIN, 'b')
    expect(r.best).toMatchObject({ book: 'betmgm', american: 134, best: true })
    expect(r.dots.find(d => d.book === 'pinnacle')).toMatchObject({ offshore: true, best: false })
    const onlyOffshoreBest = buildLadderRung(row({ books: [q('pinnacle', -120, 150), q('draftkings', -150, 125)] }), 'b')
    expect(onlyOffshoreBest.best?.book).toBe('draftkings')
  })
  it('the viewer\'s book cut limits the best price but keeps every dot', () => {
    const r = buildLadderRung(MAIN, 'b', { bettable: new Set(['draftkings', 'fanduel']) })
    expect(r.best?.book).toBe('draftkings')
    expect(r.dots).toHaveLength(6)
  })
  it('a best price below the market fair curve is a market gap with positive market EV', () => {
    const r = buildLadderRung(MAIN, 'b', { pMarket: 0.435, pModel: 0.467 })
    expect(r.marketGap).toEqual({ from: breakEvenProb(134), to: 0.435 })
    expect(r.evMarket).toBeGreaterThan(0)
    expect(r.modelGap).toEqual({ from: breakEvenProb(134), to: 0.467 })
    expect(r.evModel).toBeCloseTo(0.467 * 2.34 - 1, 6)
  })
  it('model gap without a market gap: the model likes it, the consensus does not', () => {
    const r = buildLadderRung(MAIN, 'b', { pMarket: 0.41, pModel: 0.467 })
    expect(r.marketGap).toBeNull()
    expect(r.evMarket).toBeLessThan(0)
    expect(r.modelGap).not.toBeNull()
  })
  it('a gap exists exactly when its EV is positive', () => {
    for (const p of [0.38, 0.4274, 0.43, 0.5]) {
      const r = buildLadderRung(MAIN, 'b', { pMarket: p })
      expect(r.marketGap != null).toBe((r.evMarket ?? 0) > 1e-9)
    }
  })
  it('no fair value means no gap and no EV, not zero', () => {
    const r = buildLadderRung(MAIN, 'b')
    expect(r.marketGap).toBeNull()
    expect(r.modelGap).toBeNull()
    expect(r.evMarket).toBeNull()
    expect(r.evModel).toBeNull()
  })
  it('the active side picks the book price: a = away, b = home', () => {
    expect(buildLadderRung(MAIN, 'a').best).toMatchObject({ book: 'novig', american: -150 })
    expect(buildLadderRung(MAIN, 'b').best?.american).toBe(134)
  })
  it('a rung with fewer than three books is thin', () => {
    expect(LADDER_THIN_BELOW).toBe(3)
    expect(buildLadderRung(row({ books: [q('draftkings', -150, 125), q('fanduel', -150, 124)] }), 'b').thin).toBe(true)
    expect(buildLadderRung(MAIN, 'b').thin).toBe(false)
  })
  it('a book with no price on the side is skipped, and no bettable book means no best', () => {
    const r = buildLadderRung(row({ books: [q('draftkings', -150, null), q('pinnacle', -150, 130)] }), 'b')
    expect(r.nBooks).toBe(1)
    expect(r.best).toBeNull()
  })
})

describe('rung order: hardest cover first for the active side', () => {
  const rungs = [{ line: -0.5 }, { line: 1.5 }, { line: -2.5 }, { line: -1.5 }]
  it('spread: the home side rises left to right, the away side falls', () => {
    expect(orderRungs(rungs, 'spread', 'b').map(r => r.line)).toEqual([-2.5, -1.5, -0.5, 1.5])
    expect(orderRungs(rungs, 'spread', 'a').map(r => r.line)).toEqual([1.5, -0.5, -1.5, -2.5])
  })
  it('total: the over gets easier as the line falls, the under as it rises', () => {
    const t = [{ line: 6 }, { line: 7.5 }, { line: 5.5 }, { line: 6.5 }]
    expect(orderRungs(t, 'total', 'a').map(r => r.line)).toEqual([7.5, 6.5, 6, 5.5])
    expect(orderRungs(t, 'total', 'b').map(r => r.line)).toEqual([5.5, 6, 6.5, 7.5])
  })
  it('a moneyline keeps its order and the input is never mutated', () => {
    const ml = [{ line: null }, { line: null }]
    expect(orderRungs(ml, 'ml', 'a')).toEqual(ml)
    const before = rungs.map(r => r.line)
    orderRungs(rungs, 'spread', 'b')
    expect(rungs.map(r => r.line)).toEqual(before)
  })
})

describe('flip, main line and selection', () => {
  it('the other side of a line is 1 - p - push', () => {
    expect(flipProb(0.467)).toBeCloseTo(0.533, 10)
    expect(flipProb(0.4, 0.1)).toBeCloseTo(0.5, 10)
    expect(flipProb(0.95, 0.2)).toBe(0)
  })
  it('the main line is the one most books quote; ties go to the line nearest pick\'em', () => {
    const rows = [
      row({ line: -2.5, books: [q('a', -1, 1), q('b', -1, 1)] }),
      row({ line: -1.5, books: [q('a', -1, 1), q('b', -1, 1), q('c', -1, 1), q('d', -1, 1)] }),
      row({ line: -0.5, books: [q('a', -1, 1)] }),
    ]
    expect(mainLineOf(rows)).toBe(-1.5)
    const tie = [row({ line: -2.5, books: [q('a', -1, 1), q('b', -1, 1)] }), row({ line: -1.5, books: [q('a', -1, 1), q('b', -1, 1)] })]
    expect(mainLineOf(tie)).toBe(-1.5)
    const tot = [row({ market: 'total', line: 7, books: [q('a', -1, 1)] }), row({ market: 'total', line: 6.5, books: [q('a', -1, 1)] })]
    expect(mainLineOf(tot)).toBe(6.5)
    expect(mainLineOf([])).toBeNull()
  })
  it('clicking a rung selects it; a refresh that drops it falls back to the main line', () => {
    const lines = [-2.5, -1.5, -0.5]
    expect(selectRung(lines, -2.5, -1.5)).toBe(-2.5)
    expect(selectRung(lines, 3.5, -1.5)).toBe(-1.5)
    expect(selectRung(lines, null, -1.5)).toBe(-1.5)
  })
})

describe('suspect: a huge edge is usually a stale quote', () => {
  it('flags a best price more than 15% better than the market fair value, and nothing smaller', () => {
    const r = buildLadderRung(MAIN, 'b', { pMarket: 0.6 })   // +134 against a 60% fair value is +40% EV
    expect(r.evMarket!).toBeGreaterThan(0.15)
    expect(r.suspect).toBe(true)
    expect(buildLadderRung(MAIN, 'b', { pMarket: 0.435 }).suspect).toBe(false)
    expect(buildLadderRung(MAIN, 'b').suspect).toBe(false)
  })
})

import { LADDER_WINDOW, rungSpread, splitBooks, windowRungs } from '../lib/game-ladder'

describe('what the chart shows by default', () => {
  const mkRung = (line: number, thin = false) => ({ line, rung: { thin } as never })
  const ladder = Array.from({ length: 21 }, (_, i) => mkRung(-12.5 + i))   // -12.5 .. +7.5
  it('windows the ladder around the main line, clamped at the ends, and never more than the window', () => {
    const w = windowRungs(ladder, -4.5)
    expect(w).toHaveLength(LADDER_WINDOW)
    expect(w.map(x => x.line)).toEqual([-7.5, -6.5, -5.5, -4.5, -3.5, -2.5, -1.5])
    expect(windowRungs(ladder, -12.5).map(x => x.line)[0]).toBe(-12.5)
    expect(windowRungs(ladder, 7.5).map(x => x.line).slice(-1)[0]).toBe(7.5)
  })
  it('drops thin rungs unless that would leave fewer than three', () => {
    const l = [mkRung(-3.5, true), mkRung(-2.5), mkRung(-1.5), mkRung(-0.5, true), mkRung(0.5), mkRung(1.5)]
    expect(windowRungs(l, -1.5).map(x => x.line)).toEqual([-2.5, -1.5, 0.5, 1.5])
    const sparse = [mkRung(-1.5), mkRung(0.5, true), mkRung(1.5, true)]
    expect(windowRungs(sparse, -1.5)).toHaveLength(3)
  })
  it('summarises a rung by the spread of its books and how many beat the market fair value', () => {
    const r = buildLadderRung(MAIN, 'b', { pMarket: 0.435 })
    const s = rungSpread(r)!
    expect(s.lo).toBeCloseTo(breakEvenProb(134), 10)
    expect(s.hi).toBeCloseTo(breakEvenProb(123), 10)
    expect(s.lo).toBeLessThan(s.median)
    expect(s.median).toBeLessThan(s.hi)
    expect(s.beating).toBe(r.dots.filter(d => d.y < 0.435).length)
    expect(rungSpread({ dots: [], pMarket: null } as never)).toBeNull()
  })
  it('shows the top books plus any sharp reference and folds the rest', () => {
    const dots = ['a', 'b', 'c', 'd', 'e', 'f', 'pinnacle', 'g'].map(book => ({ book }))
    const { shown, rest } = splitBooks(dots, new Set(['pinnacle']), 5)
    expect(shown.map(d => d.book)).toEqual(['a', 'b', 'c', 'd', 'e', 'pinnacle'])
    expect(rest.map(d => d.book)).toEqual(['f', 'g'])
  })
})

import { rungVerdict } from '../lib/game-ladder'

describe('rungVerdict: one plain sentence', () => {
  const name = (b: string) => ({ betmgm: 'BetMGM', draftkings: 'DraftKings' } as Record<string, string>)[b] ?? b
  it('says where the best price is, against fair, and how many books beat it', () => {
    const r = buildLadderRung(MAIN, 'b', { pMarket: 0.435 })
    const v = rungVerdict(r, 'FLA -1.5', name)
    expect(v).toMatch(/^Best price on FLA -1\.5: \+134 at BetMGM\./)
    expect(v).toMatch(/EV against the market fair price of \+130/)
    expect(v).toMatch(/\d of 6 books beat it\./)
  })
  it('never claims an edge on a thin rung, and says when there is no fair value', () => {
    const thin = buildLadderRung(row({ books: [q('draftkings', -150, 125), q('fanduel', -150, 124)] }), 'b', { pMarket: 0.3 })
    expect(rungVerdict(thin, 'FLA +2.5', name)).toMatch(/Only 2 books quote this line, so no edge is claimed/)
    expect(rungVerdict(buildLadderRung(MAIN, 'b'), 'FLA -1.5', name)).toMatch(/no market fair price/)
  })
  it('flags a suspiciously large gap and handles a rung nobody can bet', () => {
    expect(rungVerdict(buildLadderRung(MAIN, 'b', { pMarket: 0.6 }), 'FLA -1.5', name)).toMatch(/unusually large/)
    expect(rungVerdict(buildLadderRung(row({ books: [q('pinnacle', -150, 125)] }), 'b'), 'FLA -1.5', name)).toMatch(/No book you can bet at/)
  })
})

import { rungEdgeRange } from '../lib/game-ladder'

describe('rungEdgeRange: where every book sits against the market fair price', () => {
  it('runs from the worst book to the best, zero being the fair price, and ends at the best price EV', () => {
    const r = buildLadderRung(MAIN, 'b', { pMarket: 0.435 })
    const e = rungEdgeRange(r)!
    expect(e.hi).toBeCloseTo(0.435 * decimalOf(134) - 1, 10)
    expect(e.lo).toBeCloseTo(0.435 * decimalOf(123) - 1, 10)
    expect(e.lo).toBeLessThan(e.hi)
    expect(e.best).toBeCloseTo(e.hi, 10)
  })
  it('has no range without a market fair value or without books', () => {
    expect(rungEdgeRange(buildLadderRung(MAIN, 'b'))).toBeNull()
    expect(rungEdgeRange(buildLadderRung(row({ books: [] }), 'b', { pMarket: 0.4 }))).toBeNull()
  })
})
