import { hasMovement, MIN_MOVE_SPAN_MIN, moveForSide, moveScore, sparkPath, summarizeMove, type MovePoint } from '../lib/line-move'

const pt = (min: number, line: number | null, p_a: number | null, n = 6): MovePoint => ({ t: new Date(Date.UTC(2026, 10, 3, 14, min)).toISOString(), line, p_a, best_a: -110, best_b: -110, n })

describe('summarizeMove', () => {
  it('needs two points', () => {
    expect(summarizeMove(null)).toBeNull()
    expect(summarizeMove({ points: [pt(0, -1.5, 0.4)] })).toBeNull()
  })
  it('opens at the first point and ends at the last, however the points arrive', () => {
    const m = summarizeMove({ points: [pt(60, -1.5, 0.46), pt(0, -1.5, 0.42), pt(30, -1.5, 0.44)] })!
    expect(m.n).toBe(3)
    expect(m.pOpen).toBe(0.42)
    expect(m.pNow).toBe(0.46)
    expect(m.pDelta).toBeCloseTo(0.04, 10)
    expect(m.spanMin).toBe(60)
    expect(m.lineMoved).toBe(false)
  })
  it('only compares probabilities at the current line: a line move restarts the probability run', () => {
    const m = summarizeMove({ points: [pt(0, -1.0, 0.50), pt(30, -1.0, 0.52), pt(60, -1.5, 0.43), pt(90, -1.5, 0.44)] })!
    expect(m.lineMoved).toBe(true)
    expect(m.lineOpen).toBe(-1.0)
    expect(m.lineNow).toBe(-1.5)
    expect(m.pSeries).toEqual([0.43, 0.44])
    expect(m.pDelta).toBeCloseTo(0.01, 10)
    expect(m.lineSeries).toEqual([-1.0, -1.0, -1.5, -1.5])
  })
  it('a moneyline has no line and is one run; missing probabilities are skipped, not zero', () => {
    const m = summarizeMove({ points: [pt(0, null, null), pt(30, null, 0.55), pt(60, null, 0.57)] })!
    expect(m.lineMoved).toBe(false)
    expect(m.pSeries).toEqual([0.55, 0.57])
    expect(m.pDelta).toBeCloseTo(0.02, 10)
    expect(summarizeMove({ points: [pt(0, null, null), pt(30, null, null)] })!.pDelta).toBeNull()
  })
  it('ignores a point with an unreadable time', () => {
    expect(summarizeMove({ points: [pt(0, -1.5, 0.4), { ...pt(30, -1.5, 0.5), t: 'nope' }] })).toBeNull()
  })
})

describe('side, span and score', () => {
  const m = summarizeMove({ points: [pt(0, -1.5, 0.40), pt(60, -1.5, 0.45)] })!
  it('side B (home / under) flips the probabilities and the move', () => {
    const b = moveForSide(m, 'b')
    expect(b.pOpen).toBeCloseTo(0.60, 10)
    expect(b.pNow).toBeCloseTo(0.55, 10)
    expect(b.pDelta).toBeCloseTo(-0.05, 10)
    expect(b.pSeries).toEqual([0.6, 0.55].map(x => expect.closeTo(x, 10)))
    expect(moveForSide(m, 'a').pDelta).toBeCloseTo(0.05, 10)
  })
  it('two runs minutes apart are not movement', () => {
    expect(hasMovement(summarizeMove({ points: [pt(0, -1.5, 0.4), pt(MIN_MOVE_SPAN_MIN - 1, -1.5, 0.5)] }))).toBe(false)
    expect(hasMovement(m)).toBe(true)
    expect(hasMovement(null)).toBe(false)
  })
  it('scores the biggest mover, counting a half-point line move as several probability points', () => {
    const lineMove = summarizeMove({ points: [pt(0, -1.0, 0.5), pt(60, -1.5, 0.5)] })!
    expect(moveScore([m])).toBeCloseTo(0.05, 10)
    expect(moveScore([lineMove])).toBeCloseTo(0.03, 10)
    expect(moveScore([m, lineMove, null])).toBeCloseTo(0.05, 10)
    expect(moveScore([null, undefined])).toBe(0)
  })
})

describe('sparkPath', () => {
  it('needs two values, flips y so a rise goes up, and ends at the last value', () => {
    expect(sparkPath([1], 60, 20)).toBeNull()
    const s = sparkPath([1, 2, 3], 60, 20, { pad: 2 })!
    expect(s.d.startsWith('M2.0,18.0')).toBe(true)
    expect(s.last).toEqual({ x: 58, y: 2 })
  })
  it('draws a flat series flat and a step as a staircase', () => {
    expect(sparkPath([5, 5, 5], 60, 20)!.last.y).toBe(10)
    const step = sparkPath([-1, -1, -1.5], 60, 20, { step: true })!
    expect(step.d.split('L').length).toBe(1 + 2 * 2)
  })
})
