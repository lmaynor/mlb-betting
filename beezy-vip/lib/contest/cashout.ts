/**
 * Cash-out preview: a TypeScript mirror of contest/cashout.py so the page can show the value before the server quotes it.
 *
 *   value = full payout x P(every leg still wins) x (1 - margin), rounded down, never above the payout
 *
 * P(leg) is the de-vigged two-sided price on the CURRENT board, moved from the current line to the line the player holds with the
 * contest's margin model (lib/contest/margin.ts: placeholders, UNVALIDATED), so a line that moved in the player's favour can cash out above the stake.
 * The server re-prices and refuses if the number moved. Straights and parlays only; teasers are not offered, and nothing is offered
 * once a game is closed to betting (in-game cash out waits for live prices).
 */
import { americanToDecimal, devig, parlayDecimal, payoutCents } from './odds'
import { fairProbAt } from './margin'
import { gameClosed } from './slip'
import type { BoardGame, ContestRules, PlacedBet } from './types'

export { devig, normCdf, normInv } from './odds'

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
  if (leg.line === q.line) return p
  const sigma = leg.market === 'spread' ? rules.cashoutSigma.spread : rules.cashoutSigma.total
  // the held line against the line now on offer: refit the margin model to the current price and read it at the held line (same model that prices alt lines)
  return fairProbAt(leg.market, leg.side, p, q.line, leg.line, sigma, leg.market === 'spread' ? rules.keyWeights : undefined)
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
