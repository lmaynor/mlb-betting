/** lib/contest/sgp: same-game parlay pricing. The golden ticket prices were produced by the Python engine (contest.sgp.sgp_price). */
import { SAMPLE_RULES } from '@/lib/contest/fixture'
import { bvnCdf, sgpPairOk, sgpPrice } from '@/lib/contest/sgp'
import type { BoardGame, Pick } from '@/lib/contest/types'

const R = SAMPLE_RULES
const game = (over: Partial<BoardGame> = {}): BoardGame => ({
  id: 1, round: 'Bowl', commence: '2026-12-19T17:00:00Z', status: 'scheduled',
  away: { abbr: 'AAA', name: 'Away U', espnId: null }, home: { abbr: 'HHH', name: 'Home U', espnId: null },
  spread: { line: -3.5, away: -110, home: -110 }, total: { line: 50.5, over: -110, under: -110 }, ml: { away: 145, home: -170 }, ...over,
})
const sp = (side: 'home' | 'away', line: number, price = -110): Pick => ({ gameId: 1, market: 'spread', side, line, price })
const tot = (side: 'over' | 'under', price = -110): Pick => ({ gameId: 1, market: 'total', side, line: 50.5, price })
const ml = (side: 'home' | 'away', price: number): Pick => ({ gameId: 1, market: 'ml', side, line: null, price })

describe('ticket prices match the Python engine', () => {
  it('spread x total, all four orientations', () => {
    expect(sgpPrice([sp('home', -3.5), tot('over')], game(), R)).toBe(235)
    expect(sgpPrice([sp('home', -3.5), tot('under')], game(), R)).toBe(320)
    expect(sgpPrice([sp('away', 3.5), tot('over')], game(), R)).toBe(320)
    expect(sgpPrice([sp('away', 3.5), tot('under')], game(), R)).toBe(235)
  })
  it('a heavy favourite with the dog and the under, and the moneyline pairs', () => {
    const g = game({ spread: { line: -9.5, away: -115, home: -105 }, total: { line: 50.5, over: -105, under: -115 } })
    expect(sgpPrice([sp('away', 9.5, -115), tot('under', -115)], g, R)).toBe(225)
    expect(sgpPrice([ml('home', -170), tot('over')], game(), R)).toBe(200)
    expect(sgpPrice([ml('away', 145), tot('under')], game(), R)).toBe(355)
  })
})

describe('what a same-game parlay accepts', () => {
  it('needs two legs on one game: a spread or the moneyline, with the total', () => {
    expect(sgpPairOk([sp('home', -3.5), tot('over')])).toBe(true)
    expect(sgpPairOk([ml('home', -170), tot('over')])).toBe(true)
    expect(sgpPairOk([sp('home', -3.5), ml('home', -170)])).toBe(false)             // the legs nest
    expect(sgpPairOk([sp('home', -3.5), sp('away', 3.5)])).toBe(false)
    expect(sgpPairOk([sp('home', -3.5), { ...tot('over'), gameId: 2 }])).toBe(false)
    expect(sgpPairOk([sp('home', -3.5)])).toBe(false)
  })
  it('is not priced on an alt line, when it is off, or without a quote', () => {
    expect(sgpPrice([sp('home', -4.5), tot('over')], game(), R)).toBeNull()
    expect(sgpPrice([sp('home', -3.5), tot('over')], game(), { ...R, sgpEnabled: false })).toBeNull()
    expect(sgpPrice([sp('home', -3.5), tot('over')], game({ total: null }), R)).toBeNull()
    expect(sgpPrice([sp('home', -3.5), tot('over')], undefined, R)).toBeNull()
  })
  it('positive correlation shortens the price against a plain parlay and negative lengthens it', () => {
    const parlay = 264                                                               // two -110 legs
    expect(sgpPrice([sp('home', -3.5), tot('over')], game(), R)!).toBeLessThan(parlay)
    expect(sgpPrice([sp('home', -3.5), tot('under')], game(), R)!).toBeGreaterThan(parlay)
  })
})

describe('bvnCdf', () => {
  it('matches closed forms', () => {
    expect(bvnCdf(0, 0, 0)).toBeCloseTo(0.25, 6)
    expect(bvnCdf(0, 0, 0.5)).toBeCloseTo(1 / 3, 6)
    expect(bvnCdf(-9, 0, 0.3)).toBe(0)
  })
})
