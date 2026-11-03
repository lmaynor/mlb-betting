/**
 * Pure maths for the /edge NBA shot chart: rates, shrinkage, the diverging colour scale, hex geometry and the court lines.
 * Canonical court frame (feet): x lateral -25..25, y from the baseline 0..; hoop (0, 5.25). SVG space is 10 px per foot with the
 * baseline at the bottom. Colours are literal hex because var() does not resolve in SVG paint attributes.
 */
import type { ShotHexCell, ShotTuple } from '@/lib/types'

export const PX = 10                       // svg units per foot
export const COURT_X0 = -25
export const COURT_W = 50
export const VIEW_TOP = 38                 // feet from the baseline shown at the top of the chart
export const VIEW_W = COURT_W * PX
export const VIEW_H = VIEW_TOP * PX + 10   // 10 units of margin under the baseline

// mirrored: negative x is the shooter's RIGHT in the source frame, and "right" must read on the right of the chart
export const sx = (x: number) => (COURT_X0 + COURT_W - x) * PX
export const sy = (y: number) => (VIEW_TOP - y) * PX

export const MAKE = '#57F287'
export const MISS = '#ef4444'
export const LINE = '#3a4052'

/** Shrink a rate toward the league rate by `k` pseudo-attempts: a 1-for-1 cell is not +50 points. */
export const SHRINK_K = 5
export function shrunkRate(made: number, att: number, leagueRate: number, k = SHRINK_K): number {
  return (made + k * leagueRate) / (att + k)
}

/** Percentage-point difference of the player's shrunk rate vs the league rate in the same cell (0 attempts => 0). */
export function diffPoints(made: number, att: number, leagueMade: number, leagueAtt: number, k = SHRINK_K): number {
  if (!leagueAtt) return 0
  const l = leagueMade / leagueAtt
  return (shrunkRate(made, att, l, k) - l) * 100
}

/** Break points in percentage points; six bins: < -9, -9..-3, -3..0, 0..3, 3..9, >= 9. */
export const DIFF_BREAKS = [-9, -3, 0, 3, 9] as const
export const DIFF_COLORS = ['#b91c1c', '#f06262', '#8a6f78', '#6f8a7d', '#3fc77a', '#57F287'] as const
export const DIFF_LABELS = ['-9 or worse', '-9 to -3', '-3 to 0', '0 to +3', '+3 to +9', '+9 or better'] as const

export function diffBin(d: number): number {
  let i = 0
  while (i < DIFF_BREAKS.length && d >= DIFF_BREAKS[i]) i++
  return i
}
export const diffColor = (d: number) => DIFF_COLORS[diffBin(d)]

export const pct1 = (made: number, att: number) => (att ? `${((made / att) * 100).toFixed(1)}%` : '--')
export const signed1 = (n: number) => `${n >= 0 ? '+' : '-'}${Math.abs(n).toFixed(1)}`

// -- hexes ----------------------------------------------------------------------------------------------------------------
export function hexCenter(q: number, r: number, size: number): [number, number] {
  return [size * Math.sqrt(3) * (q + r / 2), size * 1.5 * r]
}

/** SVG polygon points for a pointy-top hex of circumradius `rad` feet centred on the cell (q, r) of a `size`-foot grid. */
export function hexPoints(q: number, r: number, size: number, rad: number): string {
  const [cx, cy] = hexCenter(q, r, size)
  const pts: string[] = []
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 180) * (60 * i - 30)
    pts.push(`${sx(cx + rad * Math.cos(a)).toFixed(1)},${sy(cy + rad * Math.sin(a)).toFixed(1)}`)
  }
  return pts.join(' ')
}

/** Frequency bin of each cell by percentile of attempts (terciles): 0 small, 1 medium, 2 large. */
export function sizeBins(counts: number[]): (n: number) => 0 | 1 | 2 {
  const s = counts.slice().sort((a, b) => a - b)
  if (!s.length) return () => 0
  const at = (p: number) => s[Math.min(s.length - 1, Math.floor(p * s.length))]
  const lo = at(1 / 3), hi = at(2 / 3)
  return n => (n > hi ? 2 : n > lo ? 1 : 0)
}
export const SIZE_FRACTION = [0.5, 0.75, 1] as const

export interface HexMark { key: string; points: string; fill: string; bin: 0 | 1 | 2; q: number; r: number; att: number; made: number; lAtt: number; lMade: number; diff: number }

export function hexMarks(cells: ShotHexCell[], size: number): HexMark[] {
  const bin = sizeBins(cells.map(c => c[2]))
  return cells.map(([q, r, a, m, la, lm]) => {
    const b = bin(a)
    const d = diffPoints(m, a, lm, la)
    return { key: `${q},${r}`, points: hexPoints(q, r, size, size * SIZE_FRACTION[b] * 0.98), fill: diffColor(d), bin: b, q, r, att: a, made: m, lAtt: la, lMade: lm, diff: d }
  })
}

// -- shots ----------------------------------------------------------------------------------------------------------------
export const isMade = (s: ShotTuple) => (s[2] & 1) === 1
export const isThree = (s: ShotTuple) => (s[2] & 2) === 2

/** One path for all makes (circles) and one for all misses (x marks): ~1,500 shots stay two DOM nodes. */
export function shotPaths(shots: ShotTuple[], r = 2.3): { made: string; missed: string } {
  const made: string[] = [], missed: string[] = []
  for (const s of shots) {
    const x = sx(s[0] / 10), y = sy(s[1] / 10)
    if (isMade(s)) made.push(`M${(x - r).toFixed(1)} ${y.toFixed(1)}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`)
    else missed.push(`M${(x - r).toFixed(1)} ${(y - r).toFixed(1)}l${2 * r} ${2 * r}m${-2 * r} 0l${2 * r} ${-2 * r}`)
  }
  return { made: made.join(''), missed: missed.join('') }
}

// -- court lines ----------------------------------------------------------------------------------------------------------
const HOOP_Y = 5.25
const ARC3 = 23.75
const CORNER3_X = 22
const CORNER3_Y = HOOP_Y + Math.sqrt(ARC3 * ARC3 - CORNER3_X * CORNER3_X)

/** The half court as SVG path strings (feet converted to svg units). */
export function courtPaths(): { boundary: string; paint: string; ftCircle: string; restricted: string; threePoint: string; backboard: string; rim: string } {
  const P = (x: number, y: number) => `${sx(x).toFixed(1)} ${sy(y).toFixed(1)}`
  const arc = (r: number, x0: number, y0: number, x1: number, y1: number, large = 0, sweep = 1) => `A${r * PX} ${r * PX} 0 ${large} ${sweep} ${P(x1, y1)}`
  return {
    boundary: `M${P(-25, 0)}L${P(25, 0)}L${P(25, VIEW_TOP)}L${P(-25, VIEW_TOP)}Z`,
    paint: `M${P(-8, 0)}L${P(-8, 19)}L${P(8, 19)}L${P(8, 0)}M${P(-6, 0)}L${P(-6, 19)}M${P(6, 0)}L${P(6, 19)}`,
    ftCircle: `M${P(-6, 19)}${arc(6, -6, 19, 6, 19, 0, 0)}M${P(-6, 19)}${arc(6, -6, 19, 6, 19, 0, 1)}`,
    restricted: `M${P(-4, HOOP_Y)}${arc(4, -4, HOOP_Y, 4, HOOP_Y, 0, 1)}`,
    threePoint: `M${P(-CORNER3_X, 0)}L${P(-CORNER3_X, CORNER3_Y)}${arc(ARC3, -CORNER3_X, CORNER3_Y, CORNER3_X, CORNER3_Y, 0, 1)}L${P(CORNER3_X, 0)}`,
    backboard: `M${P(-3, 4)}L${P(3, 4)}`,
    rim: `M${P(-0.75, HOOP_Y)}${arc(0.75, -0.75, HOOP_Y, 0.75, HOOP_Y, 0, 0)}${arc(0.75, 0.75, HOOP_Y, -0.75, HOOP_Y, 0, 0)}`,
  }
}
