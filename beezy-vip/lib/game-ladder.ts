/**
 * Game-line LADDER maths (the game deep-dive on /ev/games): pure functions, no React, so the chart's coordinates are pinned by tests.
 *
 * Conventions (all pinned in tests/game-ladder.test.ts):
 *  - Chart Y is a probability in 0..1. A book's dot sits at its BREAK-EVEN probability (`americanToImpliedProb`, vig included), never at
 *    1 - p. A dot BELOW the market-fair curve is a price better than the de-vigged consensus (positive market EV); a dot below the model
 *    curve is a price better than our model says (model gap, UNVALIDATED). Both gaps are returned separately because only the first is
 *    a market fact.
 *  - Book bars (the per-book matrix) put a higher payout FURTHER RIGHT: X comes from the decimal price, never from implied probability.
 *  - Rungs run from the hardest cover on the left to the easiest on the right for the ACTIVE side, so both curves rise left to right.
 *  - A game row's `a` side is away / over and `b` is home / under; a spread row's `line` is the HOME line.
 */
import type { GameMarket, GameOddsRow } from '@/lib/game-odds'
import { americanToImpliedProb, formatOdds, probToAmerican } from '@/lib/odds'
import { isOffshore } from '@/lib/offshore'

export type LadderSide = 'a' | 'b'

/** Fewer books than this on a rung = a thin rung: its prices are shown but never headline an edge. */
export const LADDER_THIN_BELOW = 3

/** A market EV above this is far more often a stale or mispriced quote than a real edge, so it is flagged for a second look and never styled as a win. */
export const LADDER_SUSPECT_EV = 0.15

/** A book's break-even probability on a price (vig included). The Y of its dot. */
export function breakEvenProb(american: number): number {
  return americanToImpliedProb(american)
}

export function decimalOf(american: number): number {
  return american > 0 ? 1 + american / 100 : 1 + 100 / Math.abs(american)
}

/**
 * X position (0..1 of a bar track) of a book price in the per-book matrix: higher decimal payout = further right. `lo` / `hi` are the
 * worst and best decimal on the rung; the result is kept inside [from, to] so a dot never touches the track edge.
 */
export function bookX(decimal: number, lo: number, hi: number, from = 0.1, to = 0.92): number {
  if (!(hi > lo)) return (from + to) / 2
  const t = Math.min(1, Math.max(0, (decimal - lo) / (hi - lo)))
  return from + t * (to - from)
}

/** The opposite side's probability on the same line; a push belongs to neither side. */
export function flipProb(p: number, push = 0): number {
  return Math.max(0, Math.min(1, 1 - p - push))
}

/**
 * Rungs of one market ordered hardest cover first for `side`. A spread row's line is the HOME line: the home side gets easier as it
 * rises, the away side as it falls. A total is easier for the over as the line falls. A moneyline has no ladder and keeps its order.
 */
export function orderRungs<T extends { line: number | null }>(rungs: readonly T[], market: GameMarket, side: LadderSide): T[] {
  const out = rungs.slice()
  if (market === 'ml') return out
  const ascending = side === 'b'   // spread: home (b) gets easier as the line rises; total: under (b) does
  const key = (r: T) => r.line ?? 0
  out.sort((x, y) => (ascending ? key(x) - key(y) : key(y) - key(x)))
  return out
}

/** The main line of one market in one game: the line most books quote; ties go to the line nearest pick'em (spread) or the lowest total. */
export function mainLineOf(rows: readonly Pick<GameOddsRow, 'market' | 'line' | 'books'>[]): number | null {
  let best: { line: number; n: number } | null = null
  for (const r of rows) {
    if (r.line == null) continue
    const n = (r.books ?? []).length
    const closer = best != null && (r.market === 'spread' ? Math.abs(r.line) < Math.abs(best.line) : r.line < best.line)
    if (best == null || n > best.n || (n === best.n && closer)) best = { line: r.line, n }
  }
  return best?.line ?? null
}

/** Keep a selected rung valid when the feed refreshes: the same line if it is still there, else `fallback` (the main line). */
export function selectRung(lines: readonly (number | null)[], wanted: number | null, fallback: number | null): number | null {
  return wanted != null && lines.includes(wanted) ? wanted : fallback
}

export interface LadderDot { book: string; american: number; decimal: number; y: number; best: boolean; offshore: boolean }
export interface LadderGap { /** lower edge of the gap: the best price's break-even */ from: number; /** upper edge: the curve it is below */ to: number }
export interface LadderRung {
  key: string
  line: number | null
  dots: LadderDot[]
  /** the best BETTABLE price on the active side (offshore books never qualify); null when no bettable book quotes it */
  best: LadderDot | null
  nBooks: number
  thin: boolean
  /** the best price beats the market fair value by more than LADDER_SUSPECT_EV: probably a stale quote */
  suspect: boolean
  pModel: number | null
  pMarket: number | null
  /** EV of the best price against the de-vigged market fair value: a market fact */
  evMarket: number | null
  /** EV of the best price against our model: UNVALIDATED */
  evModel: number | null
  /** best price below market fair: real price edge against the consensus */
  marketGap: LadderGap | null
  /** best price below our model: model gap, never headlined as an edge */
  modelGap: LadderGap | null
}

export interface LadderRungOptions {
  /** the model's probability for the active side on this line (null when the feed has none) */
  pModel?: number | null
  /** the de-vigged market fair probability for the active side on this line */
  pMarket?: number | null
  /** books the viewer can bet at; null = no cut. Offshore books are never best whatever this says. */
  bettable?: ReadonlySet<string> | null
}

/** Everything one rung of the ladder chart and its book matrix draws, for the active side. */
export function buildLadderRung(row: GameOddsRow, side: LadderSide, opts: LadderRungOptions = {}): LadderRung {
  const dots: LadderDot[] = []
  for (const b of row.books ?? []) {
    const american = side === 'a' ? b.a : b.b
    if (american == null) continue
    dots.push({ book: b.book, american, decimal: decimalOf(american), y: breakEvenProb(american), best: false, offshore: isOffshore(b.book) })
  }
  const eligible = dots.filter(d => !d.offshore && (opts.bettable == null || opts.bettable.has(d.book)))
  const best = eligible.length ? eligible.reduce((a, d) => (d.decimal > a.decimal ? d : a)) : null
  if (best) best.best = true

  const pModel = opts.pModel ?? null
  const pMarket = opts.pMarket ?? null
  const gap = (p: number | null): LadderGap | null => (best && p != null && best.y < p ? { from: best.y, to: p } : null)
  return {
    key: row.key,
    line: row.line,
    dots,
    best,
    nBooks: dots.length,
    thin: dots.length < LADDER_THIN_BELOW,
    suspect: best != null && pMarket != null && pMarket * best.decimal - 1 > LADDER_SUSPECT_EV,
    pModel,
    pMarket,
    evMarket: best && pMarket != null ? pMarket * best.decimal - 1 : null,
    evModel: best && pModel != null ? pModel * best.decimal - 1 : null,
    marketGap: gap(pMarket),
    modelGap: gap(pModel),
  }
}

/** Lines shown by default around the main line (the rest sit behind "all lines"). */
export const LADDER_WINDOW = 7

/**
 * The rungs worth looking at by default: thin rungs (under 3 books) drop out unless that would leave fewer than three, then a window
 * of `size` rungs centred on the main line (clamped at either end). `ordered` is already hardest cover first.
 */
export function windowRungs<T extends { rung: LadderRung; line: number | null }>(ordered: readonly T[], mainLine: number | null, size = LADDER_WINDOW): T[] {
  const solid = ordered.filter(x => !x.rung.thin)
  const pool = solid.length >= 3 ? solid : ordered.slice()
  if (pool.length <= size) return pool
  let mi = pool.findIndex(x => x.line === mainLine)
  if (mi < 0) mi = Math.floor(pool.length / 2)
  const start = Math.max(0, Math.min(pool.length - size, mi - Math.floor(size / 2)))
  return pool.slice(start, start + size)
}

/** The spread of the book prices on a rung as break-even probabilities (lowest = best price), and how many beat the market fair value. */
export function rungSpread(rung: LadderRung): { lo: number; hi: number; median: number; beating: number } | null {
  const ys = rung.dots.map(d => d.y).sort((a, b) => a - b)
  if (ys.length === 0) return null
  const mid = Math.floor(ys.length / 2)
  const median = ys.length % 2 ? ys[mid] : (ys[mid - 1] + ys[mid]) / 2
  const beating = rung.pMarket == null ? 0 : ys.filter(y => y < rung.pMarket!).length
  return { lo: ys[0], hi: ys[ys.length - 1], median, beating }
}

/**
 * The books worth a row by default on a rung: the top `top` by payout plus any sharp reference book; everything else is summarised.
 * `dots` must be sorted best payout first.
 */
export function splitBooks<T extends { book: string }>(dots: readonly T[], sharp: ReadonlySet<string>, top = 5): { shown: T[]; rest: T[] } {
  const shown: T[] = []
  const rest: T[] = []
  dots.forEach((d, i) => (i < top || sharp.has(d.book.toLowerCase()) ? shown : rest).push(d))
  return { shown, rest }
}

/**
 * One plain sentence for a rung: where the best price is, how it compares with the market fair price, and how many books beat it.
 * Never claims an edge on a thin rung, and says so when a gap is large enough to be a stale quote.
 */
export function rungVerdict(rung: LadderRung, label: string, bookLabel: (book: string) => string): string {
  const best = rung.best
  if (!best) return `No book you can bet at has a price on ${label}.`
  const head = `Best price on ${label}: ${formatOdds(best.american)} at ${bookLabel(best.book)}.`
  if (rung.thin) return `${head} Only ${rung.nBooks} ${rung.nBooks === 1 ? 'book quotes' : 'books quote'} this line, so no edge is claimed.`
  if (rung.pMarket == null || rung.evMarket == null) return `${head} There is no market fair price for this line yet (it needs 3 reference books).`
  const fair = formatOdds(probToAmerican(rung.pMarket))
  const ev = `${rung.evMarket >= 0 ? '+' : ''}${(rung.evMarket * 100).toFixed(1)}%`
  const sp = rungSpread(rung)
  const beat = sp ? sp.beating : 0
  const beatText = beat > 0 ? `${beat} of ${rung.nBooks} books beat it.` : 'No book beats it.'
  const sus = rung.suspect ? ' That gap is unusually large: check the price at the book before trusting it.' : ''
  return `${head} That is ${ev} EV against the market fair price of ${fair}. ${beatText}${sus}`
}

/**
 * How far every book's price on a rung sits from the market fair price, as EV (fair probability x decimal payout - 1): the lowest and
 * highest, and the best BETTABLE price's. Zero is the fair price; above it a book beats fair. Null without a market fair value.
 */
export function rungEdgeRange(rung: LadderRung): { lo: number; hi: number; best: number | null } | null {
  if (rung.pMarket == null || rung.dots.length === 0) return null
  const evs = rung.dots.map(d => rung.pMarket! * d.decimal - 1)
  return { lo: Math.min(...evs), hi: Math.max(...evs), best: rung.evMarket }
}
