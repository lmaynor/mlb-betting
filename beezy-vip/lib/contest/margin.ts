/**
 * Margin model and alt-line pricing: a TypeScript mirror of contest/margin_model.py and contest/altlines.py, so the slip can show the
 * price the server will quote for an alt line. The server re-prices every alt leg and refuses a mismatch (PRICE_CHANGED), so this only
 * keeps the page fast and honest.
 *
 * The main line's two-sided price gives the picked side's de-vigged win probability p. A discretised normal for the final margin (or
 * the game total) is refit so it reproduces p at the main line; a football spread margin gets key-number weights (3 and 7 land more
 * often than a normal curve says). The price at another line is that model's probability there, plus the margin, once. UNVALIDATED:
 * sigmas and weights are fitted to one season of closing lines (docs/analysis/contest_sportsbook_experiments_2026-11-17.md).
 */
import { devig, normCdf } from './odds'
import type { BoardGame, ContestRules, Market, Pick, Side } from './types'

const SPAN = 90
const EPS = 1e-9
const STEP = 0.5
export const ALT_MIN_PROB = 0.04
export const ALT_MAX_PROB = 0.96

type Weights = Record<string, number> | undefined

/** Probability of each integer outcome in [lo, hi] for a normal(mu, sigma) with key weights on |k|, renormalised (mu and sigma to the thousandth, as the server does). */
function pmf(mu: number, sigma: number, weights: Weights, lo: number, hi: number): number[] {
  const m = Math.round(mu * 1000) / 1000
  const s = Math.round(sigma * 1000) / 1000
  const raw: number[] = []
  let sum = 0
  for (let k = lo; k <= hi; k++) {
    const p = (normCdf((k + 0.5 - m) / s) - normCdf((k - 0.5 - m) / s)) * (weights?.[String(Math.abs(k))] ?? 1)
    raw.push(p)
    sum += p
  }
  return raw.map(r => r / sum)
}

export function winLosePush(mu: number, sigma: number, weights: Weights, handicap: number): [number, number, number] {
  const pm = pmf(mu, sigma, weights, -SPAN, SPAN)
  let win = 0, lose = 0, push = 0
  pm.forEach((p, i) => {
    const v = i - SPAN + handicap
    if (v > EPS) win += p
    else if (v < -EPS) lose += p
    else push += p
  })
  return [win, lose, push]
}

export function totalWinLosePush(mu: number, sigma: number, line: number, side: 'over' | 'under'): [number, number, number] {
  const pm = pmf(mu, sigma, undefined, 0, 2 * SPAN)
  let over = 0, under = 0, push = 0
  pm.forEach((p, t) => {
    if (t > line + EPS) over += p
    else if (t < line - EPS) under += p
    else push += p
  })
  return side === 'over' ? [over, under, push] : [under, over, push]
}

const fair = (w: number, l: number) => (w + l > 0 ? w / (w + l) : 0.5)

function bisect(p: number, lo: number, hi: number, at: (mu: number) => number): number {
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2
    if (at(mid) < p) lo = mid
    else hi = mid
  }
  return (lo + hi) / 2
}

/** Fair (push-excluded) probability that `side` wins at `line`, given its de-vigged probability `pMain` at `mainLine`. */
export function fairProbAt(market: 'spread' | 'total', side: Side, pMain: number, mainLine: number, line: number, sigma: number, weights?: Weights): number {
  const p = Math.min(Math.max(pMain, 0.001), 0.999)
  if (market === 'spread') {
    const mu = bisect(p, -70, 70, m => { const [w, l] = winLosePush(m, sigma, weights, mainLine); return fair(w, l) })
    const [w, l] = winLosePush(mu, sigma, weights, line)
    return fair(w, l)
  }
  const pOver = side === 'over' ? p : 1 - p
  const mu = bisect(pOver, 5, 180, m => { const [w, l] = totalWinLosePush(m, sigma, mainLine, 'over'); return fair(w, l) })
  const [w, l] = totalWinLosePush(mu, sigma, line, 'over')
  return side === 'over' ? fair(w, l) : 1 - fair(w, l)
}

/** Implied probability (0 < p < 1) to an American price, to the nearest 5 (so a float wobble never changes a quoted price). */
export function probToAmerican(implied: number): number {
  if (implied >= 0.5) {
    const raw = (-100 * implied) / (1 - implied)
    return Math.min(-Math.floor(-raw / 5 + 0.5) * 5, -100)
  }
  const raw = (100 * (1 - implied)) / implied
  return Math.max(Math.floor(raw / 5 + 0.5) * 5, 100)
}

export const onGrid = (line: number) => Math.abs(line * 2 - Math.round(line * 2)) < 1e-9

const sigmaFor = (rules: ContestRules, market: 'spread' | 'total') => (market === 'spread' ? rules.cashoutSigma.spread : rules.cashoutSigma.total)

/** The board's current quotes for the pick's market from the pick's side: its (main) line and both prices. */
function mainQuote(g: BoardGame, market: 'spread' | 'total', side: Side): { line: number; price: number; oppPrice: number } | null {
  if (market === 'spread' && g.spread && (side === 'home' || side === 'away'))
    return { line: side === 'home' ? g.spread.line : -g.spread.line, price: g.spread[side], oppPrice: g.spread[side === 'home' ? 'away' : 'home'] }
  if (market === 'total' && g.total && (side === 'over' || side === 'under'))
    return { line: g.total.line, price: g.total[side], oppPrice: g.total[side === 'over' ? 'under' : 'over'] }
  return null
}

/** The contest's price for the pick's side at `line`, or null when that number is not offered. */
export function altPrice(g: BoardGame, market: Market, side: Side, line: number, rules: ContestRules): number | null {
  if (!rules.altLinesEnabled || (market !== 'spread' && market !== 'total')) return null
  const main = mainQuote(g, market, side)
  if (!main) return null
  if (line === main.line) return main.price
  if (!onGrid(line) || Math.abs(line - main.line) > rules.altMaxPoints + 1e-9 || (market === 'total' && line <= 0)) return null
  const p = fairProbAt(market, side, devig(main.price, main.oppPrice), main.line, line, sigmaFor(rules, market), market === 'spread' ? rules.keyWeights : undefined)
  if (p < ALT_MIN_PROB || p > ALT_MAX_PROB) return null
  return probToAmerican(Math.min(p * (1 + rules.altMarginBp / 10_000), 0.985))
}

/** The pick moved to `line` (null when it is not offered). */
export function altPick(g: BoardGame, pick: Pick, line: number, rules: ContestRules): Pick | null {
  const price = altPrice(g, pick.market, pick.side, line, rules)
  return price == null ? null : { ...pick, line, price }
}

/** The next offered number in `dir` (+1 = more points for the bettor on a spread / a lower over / a higher under; handled by the caller via sign), or null. */
export function stepLine(g: BoardGame, pick: Pick, dir: 1 | -1, rules: ContestRules): Pick | null {
  if (pick.line == null) return null
  const max = rules.altMaxPoints / STEP
  for (let k = 1; k <= max * 2; k++) {
    const next = altPick(g, pick, pick.line + dir * k * STEP, rules)
    if (next) return next
  }
  return null
}
