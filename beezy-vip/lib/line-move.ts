/**
 * Line movement ("flow") for the Game Lines board: how a game's main line and its market fair price have moved since the board first saw
 * the game. The backend appends one point per board run (about every 30 minutes) to `history[game_key|market]`; this file only reads
 * that. Pure functions, no React. Everything here is UNVALIDATED market data: movement is shown, never read as a signal.
 *
 * A spread's fair probability is only comparable between points at the SAME line (it jumps when the line moves), so the probability
 * series is the trailing run of points at the current line, and a line move is reported as a line move, not folded into it.
 */
export interface MovePoint { t: string; line: number | null; p_a: number | null; best_a: number | null; best_b: number | null; n: number }
export interface MoveSeries { points: MovePoint[] }

/** A movement chip needs at least this much time between the first and last point: two runs minutes apart are noise. */
export const MIN_MOVE_SPAN_MIN = 20

export interface MoveSummary {
  /** points in the series */
  n: number
  since: string
  spanMin: number
  lineOpen: number | null
  lineNow: number | null
  lineMoved: boolean
  lineSeries: (number | null)[]
  /** side A (away / over) fair probability, trailing run at the current line */
  pOpen: number | null
  pNow: number | null
  /** now minus open, probability points as a fraction (0.021 = 2.1 points); null with fewer than two comparable points */
  pDelta: number | null
  pSeries: number[]
}

const ts = (s: string) => { const d = Date.parse(s); return isNaN(d) ? null : d }

/** Summary of one series, or null when there are fewer than two usable points. */
export function summarizeMove(series: MoveSeries | null | undefined): MoveSummary | null {
  const pts = (series?.points ?? []).filter(p => p && ts(p.t) != null).slice().sort((a, b) => ts(a.t)! - ts(b.t)!)
  if (pts.length < 2) return null
  const first = pts[0], last = pts[pts.length - 1]
  const spanMin = (ts(last.t)! - ts(first.t)!) / 60_000
  // trailing run at the current line (null lines, i.e. a moneyline, are one run)
  let start = pts.length - 1
  while (start > 0 && pts[start - 1].line === last.line) start--
  const run = pts.slice(start)
  const pSeries = run.map(p => p.p_a).filter((x): x is number => x != null && isFinite(x))
  const pOpen = pSeries.length ? pSeries[0] : null
  const pNow = pSeries.length ? pSeries[pSeries.length - 1] : null
  return {
    n: pts.length, since: first.t, spanMin,
    lineOpen: first.line, lineNow: last.line, lineMoved: first.line != null && last.line != null && first.line !== last.line,
    lineSeries: pts.map(p => p.line),
    pOpen, pNow, pDelta: pSeries.length >= 2 && pOpen != null && pNow != null ? pNow - pOpen : null,
    pSeries,
  }
}

/** The same summary from side B's point of view (home / under): probabilities flip, the line does not. */
export function moveForSide(m: MoveSummary, side: 'a' | 'b'): { pOpen: number | null; pNow: number | null; pDelta: number | null; pSeries: number[] } {
  if (side === 'a') return { pOpen: m.pOpen, pNow: m.pNow, pDelta: m.pDelta, pSeries: m.pSeries }
  return { pOpen: m.pOpen == null ? null : 1 - m.pOpen, pNow: m.pNow == null ? null : 1 - m.pNow, pDelta: m.pDelta == null ? null : -m.pDelta, pSeries: m.pSeries.map(p => 1 - p) }
}

/** Is there enough history to say anything about movement. */
export function hasMovement(m: MoveSummary | null | undefined): m is MoveSummary {
  return !!m && m.spanMin >= MIN_MOVE_SPAN_MIN
}

/**
 * One number to sort games by "who is moving": the fair-probability move plus a half-point line move counted as about three
 * probability points. A sorting heuristic only, not a signal.
 */
export function moveScore(ms: readonly (MoveSummary | null | undefined)[]): number {
  let best = 0
  for (const m of ms) {
    if (!hasMovement(m)) continue
    const line = m.lineMoved && m.lineOpen != null && m.lineNow != null ? Math.abs(m.lineNow - m.lineOpen) * 0.06 : 0
    best = Math.max(best, Math.abs(m.pDelta ?? 0) + line)
  }
  return best
}

/**
 * SVG path of `values` scaled into a w x h box (y flipped, `pad` px of margin) and the last point, for a sparkline. A flat series is
 * drawn as a flat line in the middle. `step` draws a staircase (for a line number that jumps).
 */
export function sparkPath(values: readonly number[], w: number, h: number, opts: { pad?: number; step?: boolean } = {}): { d: string; last: { x: number; y: number } } | null {
  const pad = opts.pad ?? 2
  if (values.length < 2) return null
  const lo = Math.min(...values), hi = Math.max(...values)
  const span = hi - lo
  const x = (i: number) => pad + (i * (w - 2 * pad)) / (values.length - 1)
  const y = (v: number) => (span < 1e-9 ? h / 2 : h - pad - ((v - lo) / span) * (h - 2 * pad))
  let d = `M${x(0).toFixed(1)},${y(values[0]).toFixed(1)}`
  for (let i = 1; i < values.length; i++) {
    if (opts.step) d += ` L${x(i).toFixed(1)},${y(values[i - 1]).toFixed(1)}`
    d += ` L${x(i).toFixed(1)},${y(values[i]).toFixed(1)}`
  }
  return { d, last: { x: x(values.length - 1), y: y(values[values.length - 1]) } }
}
