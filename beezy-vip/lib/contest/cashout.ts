/**
 * Cash-out preview: a TypeScript mirror of contest/cashout.py so the page can show the value before the server quotes it.
 *
 *   value = full payout x P(every leg still wins) x (1 - margin), rounded down, never above the payout
 *
 * P(leg) is the de-vigged two-sided price on the CURRENT board, moved from the current line to the line the player holds with a
 * normal model (sigma is a placeholder, UNVALIDATED), so a line that moved in the player's favour can cash out above the stake.
 * The server re-prices and refuses if the number moved. Straights and parlays only; teasers are not offered, and nothing is offered
 * once a game is closed to betting (in-game cash out waits for live prices).
 */
import { americanToDecimal, parlayDecimal, payoutCents } from './odds'
import { gameClosed } from './slip'
import type { BoardGame, ContestRules, PlacedBet } from './types'

/** Standard normal CDF (Abramowitz and Stegun 7.1.26 style erfc, absolute error about 1e-7). */
export function normCdf(x: number): number {
  const z = Math.abs(x) / Math.SQRT2
  const t = 1 / (1 + 0.5 * z)
  const erfc = t * Math.exp(-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (-0.18628806 + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))))
  const cdf = 0.5 * erfc
  return x >= 0 ? 1 - cdf : cdf
}

/** Inverse of the standard normal CDF (Acklam's algorithm, relative error about 1e-9), for p in (0, 1). */
export function normInv(p: number): number {
  const a = [-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716, 2.506628277459239]
  const b = [-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572]
  const c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783]
  const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416]
  const lo = 0.02425
  if (p < lo) { const q = Math.sqrt(-2 * Math.log(p)); return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1) }
  if (p > 1 - lo) { const q = Math.sqrt(-2 * Math.log(1 - p)); return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1) }
  const q = p - 0.5
  const r = q * q
  return ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
}

export const devig = (priceA: number, priceB: number): number => {
  const a = 1 / americanToDecimal(priceA)
  const b = 1 / americanToDecimal(priceB)
  return a / (a + b)
}

type Leg = PlacedBet['legs'][number]

/** The board's current quote for the leg's side: its line (the picked side's handicap for a spread) and both prices. */
function currentQuote(g: BoardGame, leg: Leg): { line: number | null; price: number; oppPrice: number } | null {
  if (leg.market === 'spread' && g.spread && (leg.side === 'home' || leg.side === 'away'))
    return { line: leg.side === 'home' ? g.spread.line : -g.spread.line, price: g.spread[leg.side], oppPrice: g.spread[leg.side === 'home' ? 'away' : 'home'] }
  if (leg.market === 'total' && g.total && (leg.side === 'over' || leg.side === 'under'))
    return { line: g.total.line, price: g.total[leg.side], oppPrice: g.total[leg.side === 'over' ? 'under' : 'over'] }
  if (leg.market === 'ml' && g.ml && (leg.side === 'home' || leg.side === 'away'))
    return { line: null, price: g.ml[leg.side], oppPrice: g.ml[leg.side === 'home' ? 'away' : 'home'] }
  return null
}

export function legProbability(leg: Leg, g: BoardGame, rules: ContestRules): number | null {
  const q = currentQuote(g, leg)
  if (!q) return null
  const p = devig(q.price, q.oppPrice)
  if (leg.market === 'ml') return p
  if (leg.line == null || q.line == null) return null
  const delta = leg.market === 'spread' ? leg.line - q.line : leg.side === 'over' ? q.line - leg.line : leg.line - q.line
  const sigma = leg.market === 'spread' ? rules.cashoutSigma.spread : rules.cashoutSigma.total
  return normCdf(normInv(Math.min(Math.max(p, 1e-6), 1 - 1e-6)) + delta / sigma)
}

export type CashoutReason = 'settled' | 'disabled' | 'teaser' | 'closed' | 'no_quote'
export type CashoutResult = { ok: true; cents: number; payoutCents: number; winProb: number } | { ok: false; reason: CashoutReason }

export const CASHOUT_REASON_TEXT: Record<CashoutReason, string> = {
  settled: '', disabled: 'Cash out is off', teaser: 'Not offered on teasers', closed: 'Closed for cash out', no_quote: 'No price to cash out against',
}

/** What cashing out one open bet pays right now, or why it cannot be cashed out. */
export function cashoutOffer(bet: PlacedBet, games: ReadonlyMap<number, BoardGame>, rules: ContestRules, now: number): CashoutResult {
  if (bet.status !== 'pending') return { ok: false, reason: 'settled' }
  if (!rules.cashoutEnabled) return { ok: false, reason: 'disabled' }
  if (bet.kind === 'teaser') return { ok: false, reason: 'teaser' }
  const probs: number[] = []
  for (const leg of bet.legs) {
    const g = games.get(leg.gameId)
    if (!g || gameClosed(g, now, rules).closed) return { ok: false, reason: 'closed' }
    const p = legProbability(leg, g, rules)
    if (p == null) return { ok: false, reason: 'no_quote' }
    probs.push(p)
  }
  const dec = bet.kind === 'straight' ? americanToDecimal(bet.legs[0].price) : parlayDecimal(bet.legs.map(l => l.price))
  const payout = payoutCents(bet.stakeCents, dec)
  const winProb = probs.reduce((a, b) => a * b, 1)
  const value = Math.floor(payout * winProb * (1 - rules.cashoutMarginBp / 10_000))
  return { ok: true, cents: Math.max(0, Math.min(value, payout)), payoutCents: payout, winProb }
}

/** The total for a set of bets (a round robin's open parlays): every one must be offerable, or the group is not. */
export function cashoutGroup(bets: readonly PlacedBet[], games: ReadonlyMap<number, BoardGame>, rules: ContestRules, now: number):
  { ok: true; cents: number; stakeCents: number; offers: { id: string; cents: number }[] } | { ok: false; reason: CashoutReason } {
  const open = bets.filter(b => b.status === 'pending')
  if (open.length === 0) return { ok: false, reason: 'settled' }
  const offers: { id: string; cents: number }[] = []
  for (const b of open) {
    const o = cashoutOffer(b, games, rules, now)
    if (!o.ok) return o
    offers.push({ id: b.id, cents: o.cents })
  }
  return { ok: true, cents: offers.reduce((a, o) => a + o.cents, 0), stakeCents: open.reduce((a, b) => a + b.stakeCents, 0), offers }
}
