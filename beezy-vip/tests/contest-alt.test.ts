/** lib/contest/margin: alt-line pricing. The golden prices below were produced by the Python engine (contest.altlines.alt_price)
 *  with football sigmas 13.5 / 13.0 and key weights {3: 1.6, 7: 1.4}; the mirror must quote exactly the same prices. */
import { altPick, altPrice, fairProbAt, onGrid, probToAmerican, stepLine } from '@/lib/contest/margin'
import { SAMPLE_RULES } from '@/lib/contest/fixture'
import type { BoardGame, ContestRules, Pick } from '@/lib/contest/types'

const R: ContestRules = { ...SAMPLE_RULES, cashoutSigma: { spread: 13.5, total: 13.0 }, keyWeights: { '3': 1.6, '7': 1.4 }, altMarginBp: 500, altMaxPoints: 14, altLinesEnabled: true }
const game = (over: Partial<BoardGame> = {}): BoardGame => ({
  id: 1, round: 'Bowl', commence: '2026-12-19T17:00:00Z', status: 'scheduled',
  away: { abbr: 'AAA', name: 'Away U', espnId: null }, home: { abbr: 'HHH', name: 'Home U', espnId: null },
  spread: { line: -3.5, away: -110, home: -110 }, total: { line: 50.5, over: -110, under: -110 }, ml: { away: 150, home: -178 }, ...over,
})

describe('alt prices match the Python engine', () => {
  it('home spread -3.5 at -110 both ways', () => {
    const want: [number, number][] = [[-10.5, 220], [-7.5, 150], [-6.5, 130], [-4.5, 100], [-3.0, -120], [-2.5, -135], [-0.5, -170], [3.5, -305], [6.5, -470]]
    for (const [line, price] of want) expect([line, altPrice(game(), 'spread', 'home', line, R)]).toEqual([line, price])
  })
  it('away spread +6.5 at -115 / -105', () => {
    const g = game({ spread: { line: -6.5, away: -115, home: -105 } })
    const want: [number, number][] = [[2.5, 150], [3.5, 125], [6.5, -115], [9.5, -175], [13.5, -295]]
    for (const [line, price] of want) expect([line, altPrice(g, 'spread', 'away', line, R)]).toEqual([line, price])
  })
  it('totals over and under', () => {
    const over: [number, number][] = [[43.5, -285], [47.5, -165], [50.0, -120], [53.5, 135], [57.5, 225]]
    for (const [line, price] of over) expect([line, altPrice(game(), 'total', 'over', line, R)]).toEqual([line, price])
    const g = game({ total: { line: 50.5, over: -112, under: -108 } })
    const under: [number, number][] = [[44.5, 200], [50.5, -108], [56.5, -240], [60.5, -440]]
    for (const [line, price] of under) expect([line, altPrice(g, 'total', 'under', line, R)]).toEqual([line, price])
  })
})

describe('what is and is not offered', () => {
  it('rejects off-grid numbers, numbers past the reach, a moneyline, and everything when the flag is off', () => {
    expect(onGrid(-3.5) && onGrid(7) && !onGrid(-3.25)).toBe(true)
    expect(altPrice(game(), 'spread', 'home', -3.25, R)).toBeNull()
    expect(altPrice(game(), 'spread', 'home', -3.5 - 14.5, R)).toBeNull()
    expect(altPrice(game(), 'ml', 'home', -3.5, R)).toBeNull()
    expect(altPrice(game(), 'spread', 'home', -2.5, { ...R, altLinesEnabled: false })).toBeNull()
  })
  it('withholds a leg that would pass 96 percent', () => {
    expect(altPrice(game({ spread: { line: -20.5, home: -1000, away: 650 } }), 'spread', 'home', -10.5, R)).toBeNull()
  })
  it('the main line is the board price, not the model price', () => {
    expect(altPrice(game({ spread: { line: -3.5, home: -118, away: -102 } }), 'spread', 'home', -3.5, R)).toBe(-118)
  })
})

describe('the model', () => {
  it('buying through a key number costs more than the same distance elsewhere', () => {
    const across = fairProbAt('spread', 'home', 0.5, -3.5, -2.5, 13.5, R.keyWeights) - 0.5
    const plain = fairProbAt('spread', 'home', 0.5, -3.5, -2.5, 13.5) - 0.5
    expect(across).toBeGreaterThan(plain * 1.4)
  })
  it('both sides of a total are complements before the margin', () => {
    expect(fairProbAt('total', 'over', 0.5, 50.5, 47.5, 13) + fairProbAt('total', 'under', 0.5, 50.5, 47.5, 13)).toBeCloseTo(1, 6)
  })
  it('American prices round to the nearest 5 and never sit inside +/-100', () => {
    expect(probToAmerican(0.5238)).toBe(-110)
    expect(probToAmerican(0.3)).toBe(235)
    expect(Math.abs(probToAmerican(0.4999))).toBeGreaterThanOrEqual(100)
  })
})

describe('stepping a pick', () => {
  const pick: Pick = { gameId: 1, market: 'spread', side: 'home', line: -3.5, price: -110 }
  it('moves half a point and reprices from the board price', () => {
    expect(altPick(game(), pick, -4.5, R)).toEqual({ ...pick, line: -4.5, price: 100 })
    expect(stepLine(game(), pick, -1, R)).toEqual({ ...pick, line: -4, price: -105 })
    expect(stepLine(game(), pick, 1, R)?.line).toBe(-3)
  })
  it('steps return to the main line price', () => {
    const away = stepLine(game(), stepLine(game(), pick, -1, R)!, 1, R)
    expect(away).toEqual(pick)
  })
  it('stops at the edge of the reach', () => {
    const edge = { ...pick, line: -17.5, price: 500 }
    expect(stepLine(game(), edge, -1, R)).toBeNull()
  })
})
