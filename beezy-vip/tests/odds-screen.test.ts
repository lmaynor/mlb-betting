import { bestPrice, buildScreenRows, devigOver, fairOver, type BoardRow } from '@/lib/odds-screen'

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
