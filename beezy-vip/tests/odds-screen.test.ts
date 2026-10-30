import {
  bestPrice, buildArbRows, buildLongshotRows, buildScreenRows, computeLongshotMetrics, computeOutliers, computeRowArb, devigOver, fairOver,
  computeMiddles, computeSgpProxies, copulaJoint2D, kalshiEffectiveDecimal, buildSgpDeeplinkTs, type BoardRow,
} from '@/lib/odds-screen'

const row = (books: BoardRow['books'], line = 24.5): BoardRow => ({
  key: `g|jo|points|${line}`, game_key: 'nba:2026-10-21:BOS@LAL', commence: '2026-10-22T01:00:00+00:00', away: 'BOS', home: 'LAL',
  player: 'Jo Doe', player_norm: 'jo doe', stat: 'points', line, books,
})
const B = (book: string, over: number | null, under: number | null) => ({ book, over, under })
const opts = { refs: [] as string[], method: 'proportional' as const, bettable: null, minRef: 3, side: 'both' as const }

describe('devigOver', () => {
  it('proportional: -110/-110 is a coin flip', () => expect(devigOver(-110, -110, 'proportional')).toBeCloseTo(0.5, 6))
  it('proportional matches p/(p+q)', () => {
    const pO = 110 / 210, pU = 130 / 230
    expect(devigOver(-110, -130, 'proportional')).toBeCloseTo(pO / (pO + pU), 6)
  })
  it('power solves pO^k + pU^k = 1 and gives the favourite more than proportional on a skewed market', () => {
    const p = devigOver(-250, 200, 'power')!
    const pO = 250 / 350, pU = 100 / 300
    const k = Math.log(p) / Math.log(pO)
    expect(Math.pow(pO, k) + Math.pow(pU, k)).toBeCloseTo(1, 6)
    expect(p).toBeGreaterThan(pO / (pO + pU))
  })
  it('returns null without two sides; an under-100% market falls back to proportional', () => {
    expect(devigOver(-110, null, 'power')).toBeNull()
    expect(devigOver(110, 110, 'power')).toBeCloseTo(0.5, 6)
  })
})

describe('fairOver', () => {
  const books = [B('a', -110, -110), B('b', -130, 110), B('c', 100, -120), B('d', null, -105)]
  it('averages every two-sided book by default, skipping one-sided quotes', () => expect(fairOver(books, null, 'proportional')!.n).toBe(3))
  it('restricts to the reference set and honours leave-self-out', () => {
    expect(fairOver(books, new Set(['a', 'b']), 'proportional')!.n).toBe(2)
    expect(fairOver(books, new Set(['a', 'b']), 'proportional', 'a')!.n).toBe(1)
  })
  it('null when no reference book has two sides', () => expect(fairOver(books, new Set(['d']), 'proportional')).toBeNull())
})

describe('bestPrice', () => {
  const books = [B('draftkings', -110, -110), B('fanduel', 105, -125), B('bovada', 120, -150), B('novig', 105, -130)]
  it('highest payout wins and ties are listed', () => {
    expect(bestPrice(books, 'OVER', null)).toMatchObject({ book: 'bovada', american: 120 })
    expect(bestPrice(books, 'OVER', new Set(['draftkings', 'fanduel', 'novig']))).toMatchObject({ book: 'fanduel', american: 105, ties: ['fanduel', 'novig'] })
  })
  it('prefers -105 over -110 on the under', () => expect(bestPrice([B('x', 0, -110), B('y', 0, -105)], 'UNDER', null)!.book).toBe('y'))
})

describe('buildScreenRows', () => {
  const sharp = [B('pinnacle', -110, -110), B('circa', -110, -110), B('novig', -108, -108)]
  const r = row([...sharp, B('draftkings', 110, -140), B('fanduel', -110, -110)])
  it('prices the best over against the reference books and returns ev = p*dec-1', () => {
    const out = buildScreenRows([r], { ...opts, refs: ['pinnacle', 'circa', 'novig'], side: 'over' })
    expect(out).toHaveLength(1)
    expect(out[0]).toMatchObject({ side: 'OVER', label: 'o24.5', bestBook: 'draftkings', bestAmerican: 110, nRef: 3 })
    expect(out[0].ev!).toBeCloseTo(out[0].pFair! * 2.1 - 1, 9)
    expect(out[0].ev!).toBeGreaterThan(0.03)
  })
  it('lists a row whose reference set is thinner than minRef as prices only (no fair value, no EV)', () => {
    const thin = buildScreenRows([r], { ...opts, refs: ['pinnacle', 'circa'], minRef: 3, side: 'over' })
    expect(thin).toHaveLength(1)
    expect(thin[0]).toMatchObject({ pFair: null, ev: null, fairAmerican: null, nRef: 2, bestBook: 'draftkings', bestAmerican: 110 })
    expect(buildScreenRows([r], { ...opts, refs: ['pinnacle', 'circa'], minRef: 2, side: 'over' })[0].ev).not.toBeNull()
  })
  it('lists a one-sided or unreferenced line (no two-sided book at all) with nRef 0', () => {
    const yes = row([B('fanduel', 120, null), B('draftkings', 115, null)])
    const out = buildScreenRows([yes], { ...opts, side: 'both' })
    expect(out.map(x => x.side)).toEqual(['OVER'])               // no under price anywhere: that side has no row
    expect(out[0]).toMatchObject({ ev: null, pFair: null, nRef: 0, bestBook: 'fanduel' })
  })
  it('never anchors a book to its own price', () => {
    const solo = row([B('pinnacle', 150, -170), B('circa', -110, -110), B('novig', -110, -110), B('fanduel', -110, -110)])
    const out = buildScreenRows([solo], { ...opts, refs: ['pinnacle', 'circa', 'novig'], minRef: 2, side: 'over' })
    expect(out[0].bestBook).toBe('pinnacle')
    expect(out[0].nRef).toBe(2)
    expect(out[0].pFair!).toBeCloseTo(0.5, 6)
  })
  it('bettable cut removes offshore books from best price but they still anchor fair value', () => {
    const out = buildScreenRows([r], { ...opts, refs: ['pinnacle', 'circa', 'novig'], bettable: new Set(['draftkings', 'fanduel']), side: 'under' })
    expect(out[0].bestBook).toBe('fanduel')
    expect(out[0].nRef).toBe(3)
  })
  it('both sides yield an over and an under row', () => expect(buildScreenRows([r], opts).map(x => x.side).sort()).toEqual(['OVER', 'UNDER']))
})

describe('buildScreenRows matches the per-book definition', () => {
  const px = [-130, -120, -115, -110, -105, 100, 105, 110, 120]
  let seed = 11
  const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 }
  const rows: BoardRow[] = Array.from({ length: 60 }, (_, i) => row(
    ['pinnacle', 'novig', 'draftkings', 'fanduel', 'betmgm', 'caesars'].filter(() => rnd() < 0.85).map(b => B(b, px[Math.floor(rnd() * 9)], rnd() < 0.1 ? null : px[Math.floor(rnd() * 9)])), 20.5 + i))
  it.each(['proportional', 'power'] as const)('fairOver with leave-self-out agrees (%s)', method => {
    for (const refs of [[], ['pinnacle', 'novig', 'draftkings']]) {
      for (const r of buildScreenRows(rows, { ...opts, refs, method, minRef: 1 })) {
        const f = fairOver(r.row.books, refs.length ? new Set(refs) : null, method, r.bestBook)
        if (!f) { expect(r.pFair).toBeNull(); expect(r.nRef).toBe(0); continue }
        expect(r.nRef).toBe(f.n)
        expect(r.pFair!).toBeCloseTo(r.side === 'OVER' ? f.pOver : 1 - f.pOver, 12)
      }
    }
  })
})

// --------------------------------------------------------------------------------------------- arbitrage / longshots
describe('computeRowArb', () => {
  it('finds a true two-way arb across two books and splits $100 into equal payouts', () => {
    const a = computeRowArb(row([B('fanduel', 120, -140), B('draftkings', -150, -105)]), null)!
    expect(a).toMatchObject({ hasArb: true, bestOverBook: 'fanduel', bestOverAm: 120, bestUnderBook: 'draftkings', bestUnderAm: -105, feeLeg: false })
    const inv = 1 / 2.2 + 1 / (1 + 100 / 105)
    expect(a.arbPct).toBeCloseTo((1 / inv - 1) * 100, 9)
    expect(a.netArbPct).toBeCloseTo(a.arbPct, 9)
    expect(a.arbPct).toBeGreaterThan(0)
    expect(a.stakeOverPer100 + a.stakeUnderPer100).toBeCloseTo(100, 9)
    expect(a.stakeOverPer100 * 2.2).toBeCloseTo(a.stakeUnderPer100 * (1 + 100 / 105), 9)         // equal payout either way
    expect(a.guaranteedProfitPer100).toBeCloseTo(a.stakeOverPer100 * 2.2 - 100, 9)
    expect(a.guaranteedProfitPer100).toBeCloseTo(a.arbPct, 9)                                    // $100 outlay: profit $ = margin %
  })
  it('never pairs a book with itself', () => {
    // one book is the best over AND the best under: a "hold" below 100% in one book is not an arb across two
    expect(computeRowArb(row([B('fanduel', 130, 120)]), null)).toBeNull()
    const a = computeRowArb(row([B('fanduel', 130, 125), B('draftkings', -150, -135)]), null)!
    expect(a.bestOverBook).not.toBe(a.bestUnderBook)
    expect(a).toMatchObject({ bestOverBook: 'fanduel', bestUnderBook: 'draftkings', hasArb: false })
    expect(a.netArbPct).toBeLessThan(0)
  })
  it('takes a Kalshi leg fee off the net margin only', () => {
    const r = row([B('kalshi', 130, -400), B('draftkings', -180, -105)])
    const a = computeRowArb(r, null)!
    expect(a.feeLeg).toBe(true)
    expect(a.bestOverBook).toBe('kalshi')
    expect(a.netArbPct).toBeLessThan(a.arbPct)
    const p = 1 / 2.3
    expect(kalshiEffectiveDecimal(2.3)).toBeCloseTo(1 / (p + 0.07 * p * (1 - p)), 12)
    const inv = 1 / kalshiEffectiveDecimal(2.3) + 1 / (1 + 100 / 105)
    expect(a.netArbPct).toBeCloseTo((1 / inv - 1) * 100, 9)
    expect(a.guaranteedProfitPer100).toBeCloseTo(a.netArbPct, 9)
    expect(computeRowArb(r, null, false)).toBeNull()                          // exchanges off: only draftkings is left
  })
  it('respects the bettable set and falls back to the runner-up when one book holds both best prices', () => {
    const r = row([B('fanduel', 125, 120), B('draftkings', 110, -125), B('betmgm', -130, 105)])
    const a = computeRowArb(r, null)!
    expect(a.bestOverBook).not.toBe(a.bestUnderBook)
    expect(a.bestOverBook === 'fanduel' || a.bestUnderBook === 'fanduel').toBe(true)
    expect(computeRowArb(r, new Set(['draftkings']))).toBeNull()
  })
  it('buildArbRows keeps live and near arbs by the net margin', () => {
    const live = row([B('fanduel', 120, -140), B('draftkings', -150, -105)], 10.5)
    const near = row([B('fanduel', 105, -140), B('draftkings', -150, -105)], 11.5)       // -0.8%
    const dead = row([B('fanduel', -110, -110), B('draftkings', -110, -110)], 12.5)
    const o = { bettable: null, includeExchanges: true }
    expect(buildArbRows([live, near, dead], { ...o, minNetArbPct: 0 }).map(x => x.row.line)).toEqual([10.5])
    expect(buildArbRows([live, near, dead], { ...o, minNetArbPct: -1.5 }).map(x => x.row.line)).toEqual([10.5, 11.5])
  })
})

describe('computeLongshotMetrics', () => {
  const lo = { refs: [] as string[], method: 'proportional' as const, bettable: null, minRef: 2, useCurve: true }
  // four books agree the over is a ~20% shot; FanDuel pays +420 on it; Novig sells the under at -250
  const books = [B('draftkings', 380, -520), B('betmgm', 400, -560), B('caesars', 360, -480), B('pinnacle', 390, -540),
    B('fanduel', 420, null), B('novig', null, -250)]
  const alt = (b: BoardRow['books'], extra: Partial<BoardRow> = {}): BoardRow => ({ ...row(b, 79.5), ...extra })

  it('classifies a +250 over, an alt line, or a fair P(over) <= 33.3% as a longshot row; a coin flip is not one', () => {
    expect(computeLongshotMetrics(row([B('a', 250, -300), B('b', 240, -300)]), lo).isLongshotRow).toBe(true)
    expect(computeLongshotMetrics(alt([B('a', 150, -170), B('b', 140, -170)], { is_alt: true }), lo)).toMatchObject({ isLongshotRow: true, isAltLine: true })
    expect(computeLongshotMetrics(row([B('a', -110, -110), B('b', -110, -110)]), lo).isLongshotRow).toBe(false)
    expect(computeLongshotMetrics(row([B('a', 190, -230), B('b', 190, -230), B('c', 190, -230)]), lo).isLongshotRow).toBe(true)     // fair ~0.33, price under +200
  })
  it('prices the snipe-over and the fade-under against the consensus with each best book left out', () => {
    const m = computeLongshotMetrics(alt(books), lo)
    expect(m).toMatchObject({ bestOverBook: 'fanduel', bestOverAm: 420, bestUnderBook: 'novig', bestUnderAm: -250, fairSource: 'consensus', nRef: 4 })
    const ps = [[380, -520], [400, -560], [360, -480], [390, -540]].map(([o, u]) => { const a = 100 / (o + 100), b = -u / (-u + 100); return a / (a + b) })
    const p = ps.reduce((x, y) => x + y, 0) / 4
    expect(m.fairOverProb!).toBeCloseTo(p, 9)
    expect(m.overEvPct!).toBeCloseTo((p * 5.2 - 1) * 100, 9)
    expect(m.fadeUnderEvPct!).toBeCloseTo(((1 - p) * 1.4 - 1) * 100, 9)
    expect(m.overImplied!).toBeCloseTo(1 / 5.2, 9)
    expect(m.overEvPct!).toBeGreaterThan(0)
  })
  it('falls back to the curve when too few reference books price the exact line, and not when the curve is off', () => {
    const thin = alt([B('fanduel', 600, null), B('draftkings', 500, -800)], { curve_fair_over: 0.2 })
    const m = computeLongshotMetrics(thin, lo)
    expect(m).toMatchObject({ fairSource: 'curve', nRef: 0, bestOverBook: 'fanduel', bestOverAm: 600 })
    expect(m.fairOverProb).toBe(0.2)
    expect(m.overEvPct!).toBeCloseTo((0.2 * 7 - 1) * 100, 9)
    expect(m.fadeUnderEvPct!).toBeCloseTo((0.8 * (1 + 100 / 800) - 1) * 100, 9)
    expect(computeLongshotMetrics(thin, { ...lo, useCurve: false })).toMatchObject({ fairOverProb: null, fairSource: null, overEvPct: null, fadeUnderEvPct: null })
    expect(computeLongshotMetrics(alt([B('fanduel', 600, null)]), lo).fairOverProb).toBeNull()                 // no curve on the row: no fair value
  })
  it('buildLongshotRows filters by sub-view, EV floor and odds range, and orders sides by EV', () => {
    const rows = [alt(books, { key: 'a' }), alt([B('fanduel', 600, null), B('draftkings', 500, -800)], { key: 'b', curve_fair_over: 0.2, is_alt: true }), row([B('a', -110, -110), B('b', -110, -110)])]
    const o = { ...lo, sub: 'all' as const, minEv: 0, minOverAm: 0 }
    expect(buildLongshotRows(rows, o).map(r => r.key).sort()).toEqual(['a', 'b'])
    expect(buildLongshotRows(rows, { ...o, minOverAm: 500 }).map(r => r.key)).toEqual(['b'])
    expect(buildLongshotRows(rows, { ...o, sub: 'plus_ev_over', minEv: 0.1 }).map(r => r.key)).toEqual(['b'])
    expect(buildLongshotRows(rows, { ...o, sub: 'fade_under', minEv: 0 }).every(r => r.side === 'UNDER' && r.m.fadeUnderEvPct! >= 0)).toBe(true)
  })
})

describe('warm mode switching at 9,000 rows', () => {
  const px = [-130, -120, -115, -110, -105, 100, 105, 110, 120, 250, 400, -300, -600]
  let seed = 7
  const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 }
  const books = ['pinnacle', 'novig', 'kalshi', 'draftkings', 'fanduel', 'betmgm', 'caesars', 'espnbet', 'bet365', 'hardrock']
  const rows: BoardRow[] = Array.from({ length: 9000 }, (_, i) => ({
    ...row(books.filter(() => rnd() < 0.7).map(b => B(b, px[Math.floor(rnd() * px.length)], rnd() < 0.1 ? null : px[Math.floor(rnd() * px.length)])), i), key: `r${i}`,
    is_alt: rnd() < 0.3, curve_fair_over: rnd() < 0.5 ? 0.05 + rnd() * 0.5 : null,
  }))
  const bettable = new Set(books.filter(b => b !== 'pinnacle'))
  const lo = { refs: [] as string[], method: 'proportional' as const, bettable, minRef: 2, useCurve: true }
  it('rebuilds each view in under 50 ms once the de-vig is warm (the screen rebuilds the active view only)', () => {
    buildLongshotRows(rows, { ...lo, sub: 'all', minEv: 0, minOverAm: 0 })                    // warms the per-row de-vig memo
    // best of 7: the full suite runs test files in parallel, and a noisy neighbour should not read as a slow rebuild
    const ms = (f: () => void) => { let best = Infinity; for (let i = 0; i < 7; i++) { const t0 = performance.now(); f(); best = Math.min(best, performance.now() - t0) } return best }
    expect(ms(() => buildArbRows(rows, { bettable, includeExchanges: true, minNetArbPct: -1.5 }))).toBeLessThan(75)
    expect(ms(() => buildLongshotRows(rows, { ...lo, sub: 'plus_ev_over', minEv: 0.02, minOverAm: 200 }))).toBeLessThan(75)
  })
})

describe('computeMiddles', () => {
  const mk = (line: number, books: BoardRow['books'], extra: Partial<BoardRow> = {}): BoardRow => ({ ...row(books, line), key: `k|${line}`, ...extra })
  const mid = (o: Partial<Parameters<typeof computeMiddles>[1]> = {}) => ({
    bettable: null, includeExchanges: true, maxCostPct: 5, minGap: 0, refs: [] as string[], method: 'proportional' as const, minRef: 2, useCurve: true, ...o,
  })
  // over 23.5 at FanDuel (-105) and under 25.5 at DraftKings (-105): a 2-point window for ~ -2.4% on a miss
  const lowRow = mk(23.5, [B('fanduel', -105, -125), B('betmgm', -115, -105)])
  const highRow = mk(25.5, [B('draftkings', 120, -105), B('caesars', 110, -110)])

  it('pairs the best over of the low line with the best under of the high line and prices the miss and the hit', () => {
    const m = computeMiddles([lowRow, highRow], mid())
    expect(m).toHaveLength(1)
    expect(m[0]).toMatchObject({ overBook: 'fanduel', overAm: -105, underBook: 'draftkings', underAm: -105, gap: 2, feeLeg: false })
    const dec = 1 + 100 / 105
    const inv = 2 / dec
    expect(m[0].missPct).toBeCloseTo((1 / inv - 1) * 100, 9)
    expect(m[0].hitPct).toBeCloseTo((2 / inv - 1) * 100, 9)
    expect(m[0].breakevenProb).toBeCloseTo(inv - 1, 9)
    expect(m[0].stakeOverPer100).toBeCloseTo(50, 9)
    // zero-EV check: at the break-even chance the hedge is exactly flat
    const p = m[0].breakevenProb
    expect(p * (m[0].hitPct / 100) + (1 - p) * (m[0].missPct / 100)).toBeCloseTo(0, 9)
  })
  it('never forms a window the wrong way round, and respects the cost and gap filters', () => {
    // over of the HIGHER line with under of the LOWER line wins nothing in the window and loses outside it: not a middle
    expect(computeMiddles([mk(21.5, [B('draftkings', null, -105)]), mk(23.5, [B('fanduel', -105, null)])], mid())).toHaveLength(0)
    expect(computeMiddles([lowRow, highRow], mid({ maxCostPct: 1 }))).toHaveLength(0)
    expect(computeMiddles([lowRow, highRow], mid({ minGap: 3 }))).toHaveLength(0)
    expect(computeMiddles([lowRow, highRow], mid({ bettable: new Set(['fanduel']) }))).toHaveLength(0)
  })
  it('a pair of legs that already arbs has a zero break-even chance and a positive miss', () => {
    const m = computeMiddles([mk(23.5, [B('fanduel', 130, -150)]), mk(25.5, [B('draftkings', 120, 105)])], mid())
    expect(m[0].missPct).toBeGreaterThan(0)
    expect(m[0].breakevenProb).toBe(0)
  })
  it('estimates the window chance and EV from the fair price of each line, and falls back to the curve', () => {
    const lo = mk(23.5, [B('fanduel', -105, -125), B('a', -150, 130), B('b', -150, 130)])
    const hi = mk(25.5, [B('draftkings', 130, -105), B('a', 150, -170), B('b', 150, -170)])
    const m = computeMiddles([lo, hi], mid())[0]
    expect(m.midProb).not.toBeNull()
    expect(m.midProb!).toBeGreaterThan(0)
    expect(m.midEvPct!).toBeCloseTo(((1 + m.midProb!) / (2 / (1 + 100 / 105)) - 1) * 100, 9)
    const thin = computeMiddles([{ ...lowRow, curve_fair_over: 0.6 }, { ...highRow, curve_fair_over: 0.4 }], mid())[0]
    expect(thin.midProb!).toBeCloseTo(0.2, 9)
    expect(computeMiddles([{ ...lowRow, curve_fair_over: 0.6 }, { ...highRow, curve_fair_over: 0.4 }], mid({ useCurve: false }))[0].midProb).toBeNull()
  })
  it('ignores one-sided quotes at whole-number lines (milestone ladder rungs) but keeps half-point ladder overs', () => {
    const under = mk(1.5, [B('novig', -488, 217)])
    expect(computeMiddles([mk(0, [B('fanduel', 800, null)]), under], mid({ maxCostPct: 500 }))).toHaveLength(0)
    expect(computeMiddles([mk(0.5, [B('fanduel', 800, null)]), under], mid({ maxCostPct: 500 }))).toHaveLength(1)
    expect(computeMiddles([mk(0, [B('fanduel', 800, -1500)]), under], mid({ maxCostPct: 500 }))).toHaveLength(1)   // two-sided: a real market
  })
  it('+EV only keeps middles whose estimated window chance beats break-even', () => {
    const lo = { ...lowRow, curve_fair_over: 0.6 }
    const hi = (c: number) => ({ ...highRow, curve_fair_over: c })
    expect(computeMiddles([lo, hi(0.4)], mid({ onlyPositiveEv: true }))).toHaveLength(1)     // 20% window vs ~2.4% break-even
    expect(computeMiddles([lo, hi(0.59)], mid({ onlyPositiveEv: true }))).toHaveLength(0)    // 1%: below break-even
    expect(computeMiddles([lowRow, highRow], mid({ onlyPositiveEv: true, minRef: 5, useCurve: false }))).toHaveLength(0)  // no estimate, no claim
  })
  it('takes the Kalshi fee off a Kalshi leg and drops it with exchanges off', () => {
    const lo = mk(23.5, [B('kalshi', -105, null)])
    const hi = mk(25.5, [B('draftkings', null, -105)])
    const withK = computeMiddles([lo, hi], mid())[0]
    expect(withK.feeLeg).toBe(true)
    expect(withK.missPct).toBeLessThan(computeMiddles([mk(23.5, [B('fanduel', -105, null)]), hi], mid())[0].missPct)
    expect(computeMiddles([lo, hi], mid({ includeExchanges: false }))).toHaveLength(0)
  })
  it('scans 9,000 rows in under 50 ms', () => {
    const books = ['pinnacle', 'novig', 'kalshi', 'draftkings', 'fanduel', 'betmgm', 'caesars', 'espnbet']
    let s = 3
    const rnd = () => { s = (s * 16807) % 2147483647; return s / 2147483647 }
    const rows: BoardRow[] = Array.from({ length: 9000 }, (_, i) => ({
      ...row(books.filter(() => rnd() < 0.7).map(b => B(b, -130 + Math.floor(rnd() * 60), rnd() < 0.1 ? null : -130 + Math.floor(rnd() * 60))), 20.5 + (i % 6)),
      key: `m${i}`, game_key: `g${Math.floor(i / 6)}`, player_norm: `p${Math.floor(i / 6)}`,
    }))
    const o = mid({ maxCostPct: 8 })
    computeMiddles(rows, o)
    let best = Infinity
    for (let i = 0; i < 7; i++) { const t0 = performance.now(); computeMiddles(rows, o); best = Math.min(best, performance.now() - t0) }
    expect(best).toBeLessThan(120)
  })
})

describe('computeOutliers', () => {
  const sample = row([
    B('fanduel', 750, -1100),
    B('draftkings', 400, -600),
    B('betmgm', 400, -600),
    B('caesars', 388, -550),
    B('betrivers', 420, -650),
  ])

  it('a book hanging +750 while 4 peers quote +400, +400, +388, +420 is flagged as an outlier with accurate consensus math and bar ratios', () => {
    const res = computeOutliers([sample], { refs: ['draftkings', 'betmgm', 'caesars', 'betrivers'], method: 'proportional', bettable: null, minPeers: 3, side: 'over' })
    expect(res).toHaveLength(1)
    const o = res[0]
    expect(o.outlierBook).toBe('fanduel')
    expect(o.outlierAmerican).toBe(750)
    expect(o.nPeers).toBe(4)
    expect(o.peers[0].american).toBe(420) // sorted highest peer price first
    expect(o.peers.every(p => p.barRatio < 1.0 && p.barRatio >= 0.12)).toBe(true)
    expect(o.payoutPremiumPct).toBeGreaterThan(50)
    expect(o.trueEvPct).not.toBeNull()
    expect(o.trueEvPct!).toBeGreaterThan(0)
    expect(o.marketAvgAmerican).toBeGreaterThan(390)
    expect(o.marketAvgAmerican).toBeLessThan(420)
  })

  it('lines with fewer than minPeers books are excluded', () => {
    const thin = row([B('fanduel', 750, -1100), B('draftkings', 400, -600), B('betmgm', 400, -600)])
    expect(computeOutliers([thin], { refs: [], method: 'proportional', bettable: null, minPeers: 3, side: 'over' })).toHaveLength(0)
    expect(computeOutliers([thin], { refs: [], method: 'proportional', bettable: null, minPeers: 2, side: 'over' })).toHaveLength(1)
  })

  it('two-way ties at the top price are not flagged as a single-book outlier', () => {
    const tied = row([
      B('fanduel', 400, -600),
      B('draftkings', 400, -600),
      B('betmgm', 300, -400),
      B('caesars', 280, -350),
      B('betrivers', 290, -380),
    ])
    const res = computeOutliers([tied], { refs: [], method: 'proportional', bettable: null, minPeers: 3, side: 'over' })
    expect(res).toHaveLength(0)
  })

  it('offshore books are never selected as outlierBook, and bettable filtering is respected', () => {
    const withOffshore = row([
      B('pinnacle', 750, -1100), // offshore
      B('draftkings', 400, -600),
      B('betmgm', 400, -600),
      B('caesars', 388, -550),
      B('betrivers', 420, -650),
    ])
    // Pinnacle cannot be the outlierBook because it is offshore:
    const resOff = computeOutliers([withOffshore], { refs: [], method: 'proportional', bettable: null, minPeers: 3, side: 'over' })
    expect(resOff.find(x => x.outlierBook === 'pinnacle')).toBeUndefined()

    // Respects bettable (My Books) constraint for the outlierBook:
    const myBooksOnly = new Set(['caesars', 'draftkings', 'betrivers'])
    const resBettable = computeOutliers([sample], { refs: [], method: 'proportional', bettable: myBooksOnly, minPeers: 3, side: 'over' })
    // FanDuel is not in myBooksOnly, so FanDuel cannot be the outlierBook
    expect(resBettable.find(x => x.outlierBook === 'fanduel')).toBeUndefined()
  })
})

describe('computeSgpProxies', () => {
  it('copulaJoint2D satisfies copula independence and Frechet bounds', () => {
    const p1 = 0.25
    const p2 = 0.30

    // Independence
    expect(copulaJoint2D(p1, p2, 0.0)).toBeCloseTo(p1 * p2, 6)

    // Positive correlation
    const pPos = copulaJoint2D(p1, p2, 0.22)
    expect(pPos).toBeGreaterThan(p1 * p2)
    expect(pPos).toBeLessThanOrEqual(Math.min(p1, p2))
  })

  it('NBA player with 10+ Reb and 10+ Ast emits a TD PROXY row with accurate EV', () => {
    const nbaReb: BoardRow = {
      key: 'g|josh_hart|rebounds|9.5',
      game_key: 'nba:2026-10-30:LAL@GSW',
      commence: '2026-10-30T23:00:00Z',
      away: 'LAL',
      home: 'GSW',
      player: 'Josh Hart',
      player_norm: 'josh hart',
      stat: 'rebounds',
      line: 9.5,
      curve_fair_over: 0.25,
      books: [
        B('fanduel', 320, -450),
        B('draftkings', 310, -430),
      ],
    }

    const nbaAst: BoardRow = {
      key: 'g|josh_hart|assists|9.5',
      game_key: 'nba:2026-10-30:LAL@GSW',
      commence: '2026-10-30T23:00:00Z',
      away: 'LAL',
      home: 'GSW',
      player: 'Josh Hart',
      player_norm: 'josh hart',
      stat: 'assists',
      line: 9.5,
      curve_fair_over: 0.22,
      books: [
        B('fanduel', 380, -550),
        B('draftkings', 370, -530),
      ],
    }

    const nbaTd: BoardRow = {
      key: 'g|josh_hart|triple_double|0.5',
      game_key: 'nba:2026-10-30:LAL@GSW',
      commence: '2026-10-30T23:00:00Z',
      away: 'LAL',
      home: 'GSW',
      player: 'Josh Hart',
      player_norm: 'josh hart',
      stat: 'triple_double',
      line: 0.5,
      books: [
        B('fanduel', 1300, null),
        B('draftkings', 1250, null),
      ],
    }

    const res = computeSgpProxies([nbaReb, nbaAst, nbaTd], {
      refs: [],
      method: 'proportional',
      bettable: null,
      minEvPct: -50,
      useCurve: true,
    })

    expect(res.length).toBeGreaterThan(0)
    const fdRow = res.find(r => r.book === 'fanduel')
    expect(fdRow).toBeDefined()
    expect(fdRow!.archetype).toBe('nba_reb_ast_10_10')
    expect(fdRow!.archetypeLabel).toBe('TD PROXY')
    expect(fdRow!.fairJointProb).toBeGreaterThan(0.05)
    expect(fdRow!.estSgpAm).toBeGreaterThan(1200)
    expect(fdRow!.evPct).not.toBeNull()
    expect(fdRow!.directMarketAm).toBe(1300)
    expect(fdRow!.vsDirectPct).toBeGreaterThan(0) // Beats direct market
  })

  it('NHL player with 1+ Goal + 1+ Assist vs Over 1.5 Points computes vsDirectPct accurately', () => {
    const nhlGoal: BoardRow = {
      key: 'g|mcdavid|goals|0.5',
      game_key: 'nhl:2026-10-30:EDM@CGY',
      commence: '2026-10-30T23:00:00Z',
      away: 'EDM',
      home: 'CGY',
      player: 'Connor McDavid',
      player_norm: 'connor mcdavid',
      stat: 'goals',
      line: 0.5,
      books: [
        B('fanduel', 190, -250),
        B('draftkings', 185, -240),
      ],
    }

    const nhlAst: BoardRow = {
      key: 'g|mcdavid|assists|0.5',
      game_key: 'nhl:2026-10-30:EDM@CGY',
      commence: '2026-10-30T23:00:00Z',
      away: 'EDM',
      home: 'CGY',
      player: 'Connor McDavid',
      player_norm: 'connor mcdavid',
      stat: 'assists',
      line: 0.5,
      books: [
        B('fanduel', 130, -170),
        B('draftkings', 125, -165),
      ],
    }

    const nhlPts: BoardRow = {
      key: 'g|mcdavid|points|1.5',
      game_key: 'nhl:2026-10-30:EDM@CGY',
      commence: '2026-10-30T23:00:00Z',
      away: 'EDM',
      home: 'CGY',
      player: 'Connor McDavid',
      player_norm: 'connor mcdavid',
      stat: 'points',
      line: 1.5,
      books: [
        B('fanduel', 240, -320),
        B('draftkings', 230, -300),
      ],
    }

    const res = computeSgpProxies([nhlGoal, nhlAst, nhlPts], {
      refs: [],
      method: 'proportional',
      bettable: null,
      minEvPct: -50,
      useCurve: true,
    })

    expect(res.length).toBeGreaterThan(0)
    const top = res[0]
    expect(top.archetype).toBe('nhl_goal_ast_vs_pts')
    expect(top.archetypeLabel).toBe('G+A PROXY')
    expect(top.directMarketAm).toBe(240)
    expect(top.vsDirectPct).not.toBeNull()
    // Direct decimal for +240 is 3.40; Est SGP for +190 & +130 is higher, so vsDirectPct > 0
    expect(top.vsDirectPct!).toBeGreaterThan(0)
  })

  it('NBA TD proxy matches 10.0 whole-number lines on milestone ladders', () => {
    const reb10: BoardRow = {
      key: 'g|luka|rebounds|10.0',
      game_key: 'nba:2026-10-30:DAL@PHX',
      commence: '2026-10-30T23:00:00Z',
      away: 'DAL',
      home: 'PHX',
      player: 'Luka Doncic',
      player_norm: 'luka doncic',
      stat: 'rebounds',
      line: 10.0,
      curve_fair_over: 0.35,
      books: [B('fanduel', 250, -350), B('draftkings', 245, -340)],
    }
    const ast10: BoardRow = {
      key: 'g|luka|assists|10.0',
      game_key: 'nba:2026-10-30:DAL@PHX',
      commence: '2026-10-30T23:00:00Z',
      away: 'DAL',
      home: 'PHX',
      player: 'Luka Doncic',
      player_norm: 'luka doncic',
      stat: 'assists',
      line: 10.0,
      curve_fair_over: 0.40,
      books: [B('fanduel', 220, -300), B('draftkings', 215, -290)],
    }
    const res = computeSgpProxies([reb10, ast10], {
      refs: [],
      method: 'proportional',
      bettable: null,
      minEvPct: -50,
      useCurve: true,
    })
    expect(res.length).toBeGreaterThan(0)
    expect(res[0].player).toBe('Luka Doncic')
    expect(res[0].archetype).toBe('nba_reb_ast_10_10')
  })

  it('NHL teammates on the same team emit a LINEMATE PTS stack', () => {
    const mcdavidPts: BoardRow = {
      key: 'g|mcdavid|points|0.5',
      game_key: 'nhl:2026-10-30:EDM@CGY',
      commence: '2026-10-30T23:00:00Z',
      away: 'EDM',
      home: 'CGY',
      team: 'EDM',
      player: 'Connor McDavid',
      player_norm: 'connor mcdavid',
      stat: 'points',
      line: 0.5,
      books: [B('fanduel', -350, 270), B('draftkings', -340, 260)],
    }
    const draisaitlPts: BoardRow = {
      key: 'g|draisaitl|points|0.5',
      game_key: 'nhl:2026-10-30:EDM@CGY',
      commence: '2026-10-30T23:00:00Z',
      away: 'EDM',
      home: 'CGY',
      team: 'EDM',
      player: 'Leon Draisaitl',
      player_norm: 'leon draisaitl',
      stat: 'points',
      line: 0.5,
      books: [B('fanduel', -280, 220), B('draftkings', -270, 215)],
    }
    const res = computeSgpProxies([mcdavidPts, draisaitlPts], {
      refs: [],
      method: 'proportional',
      bettable: null,
      minEvPct: -50,
      useCurve: true,
    })
    const linemate = res.find(r => r.archetype === 'nhl_linemate_pts_stack')
    expect(linemate).toBeDefined()
    expect(linemate!.archetypeLabel).toBe('LINEMATE PTS')
    expect(linemate!.player).toContain('Connor McDavid + Leon Draisaitl')
    expect(linemate!.rhoTrue).toBe(0.32)
  })

  it('buildSgpDeeplinkTs never appends internal game_key to /event/ URLs and routes to sport pages', () => {
    const dkUrl = buildSgpDeeplinkTs('draftkings', 'nba:2026-10-30:BOS@MIL', [], 'nba')
    expect(dkUrl).not.toContain('/event/nba:')
    expect(dkUrl).toBe('https://sportsbook.draftkings.com/leagues/basketball/nba')

    const fdUrl = buildSgpDeeplinkTs('fanduel', 'nba:2026-10-30:BOS@MIL', [], 'nba')
    expect(fdUrl).not.toContain('/event/nba:')
    expect(fdUrl).toBe('https://sportsbook.fanduel.com/navigation/nba')

    const sidsUrl = buildSgpDeeplinkTs('draftkings', 'event_123', [{ sid: '111' }, { sid: '222' }])
    expect(sidsUrl).toBe('https://sportsbook.draftkings.com/event/event_123?outcomes=111,222')
  })
})
