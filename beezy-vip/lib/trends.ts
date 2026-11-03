/**
 * Pure maths for the /edge NBA trends panel: sample windows, side-aware hit rates, the histogram, the minutes regression and the
 * driver colouring. The server (nba/trends.py) supplies the games and the full-sample splits / teammate / driver tables; everything
 * windowed (last 10, this season, all) is computed here so a toggle needs no request.
 */
import type { TrendGame } from '@/lib/types'

export type TrendWindowId = 'l10' | 'l20' | 'season' | 'all'
export const TREND_WINDOWS: { id: TrendWindowId; label: string }[] = [
  { id: 'l10', label: 'Last 10' }, { id: 'l20', label: 'Last 20' }, { id: 'season', label: 'This season' }, { id: 'all', label: 'Both seasons' },
]

/** The games a window covers (the log is chronological). "This season" is the newest season in the log; it can be a handful of games early on. */
export function windowGames(games: TrendGame[], id: TrendWindowId): TrendGame[] {
  if (id === 'l10') return games.slice(-10)
  if (id === 'l20') return games.slice(-20)
  if (id === 'season') {
    const newest = games.length ? games[games.length - 1].season : null
    return games.filter(g => g.season === newest)
  }
  return games
}

const nums = (gs: TrendGame[]) => gs.map(g => g.val).filter((v): v is number => v != null)

export interface Sample { n: number; mean: number | null; median: number | null; sd: number | null; over: number; under: number; push: number }

export function sampleStats(gs: TrendGame[], line: number | null): Sample {
  const v = nums(gs)
  const n = v.length
  if (!n) return { n: 0, mean: null, median: null, sd: null, over: 0, under: 0, push: 0 }
  const mean = v.reduce((a, b) => a + b, 0) / n
  const s = v.slice().sort((a, b) => a - b)
  const median = n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2
  const sd = n > 1 ? Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1)) : null
  const over = line == null ? 0 : v.filter(x => x > line).length
  const under = line == null ? 0 : v.filter(x => x < line).length
  return { n, mean, median, sd, over, under, push: line == null ? 0 : n - over - under }
}

/** Did the game clear the flagged side? A push (equal to an integer line) is neither. */
export function hitSide(val: number | null, line: number | null, under: boolean): boolean | null {
  if (val == null || line == null) return null
  if (val === line) return null
  return under ? val < line : val > line
}

/** Rate of the flagged side over the decided games (pushes excluded), as a 0-1 fraction; null with no decided game. */
export function sideRate(s: Sample, under: boolean): number | null {
  const decided = s.over + s.under
  return decided ? (under ? s.under : s.over) / decided : null
}

/** A readable bin width for a histogram over [lo, hi]: 1 for small counts, then 2 / 5 / 10 so there are about 8-16 bins. */
export function niceWidth(lo: number, hi: number): number {
  const span = Math.max(1, hi - lo)
  for (const w of [1, 2, 5, 10, 20]) if (span / w <= 16) return w
  return 20
}

export interface Hist { lo: number; width: number; counts: number[]; n: number }

export function histogram(vals: number[]): Hist | null {
  if (!vals.length) return null
  const width = niceWidth(Math.min(...vals), Math.max(...vals))
  const lo = Math.floor(Math.min(...vals) / width) * width
  const bins = Math.floor((Math.max(...vals) - lo) / width) + 1
  const counts = Array.from({ length: bins }, () => 0)
  for (const v of vals) counts[Math.min(bins - 1, Math.floor((v - lo) / width))]++
  return { lo, width, counts, n: vals.length }
}

/** Least-squares line of stat on minutes over the window. */
export function minutesFit(gs: TrendGame[]): { slope: number; intercept: number; r: number; n: number } | null {
  const pts = gs.filter(g => g.min != null && g.val != null && g.min > 0).map(g => [g.min as number, g.val as number])
  const n = pts.length
  if (n < 8) return null
  const mx = pts.reduce((a, p) => a + p[0], 0) / n, my = pts.reduce((a, p) => a + p[1], 0) / n
  let sxx = 0, sxy = 0, syy = 0
  for (const [x, y] of pts) { sxx += (x - mx) ** 2; sxy += (x - mx) * (y - my); syy += (y - my) ** 2 }
  if (sxx === 0 || syy === 0) return null
  const slope = sxy / sxx
  return { slope, intercept: my - slope * mx, r: sxy / Math.sqrt(sxx * syy), n }
}

/** Driver colour: how unusual the recent level of a driver is (|z| >= 1 reads hot / cold; the sign is relative to the stat, via corr). */
export function driverTone(corr: number | null, z: number | null): 'up' | 'down' | 'flat' {
  if (corr == null || z == null || Math.abs(z) < 1 || Math.abs(corr) < 0.2) return 'flat'
  return (corr > 0) === (z > 0) ? 'up' : 'down'      // the recent driver level pushes the stat up (or down) from its norm
}

export const pct = (x: number | null | undefined, d = 0) => (x == null ? '--' : `${(x * 100).toFixed(d)}%`)
export const num = (x: number | null | undefined, d = 1) => (x == null ? '--' : x.toFixed(d))
