/**
 * Bet-slip math and validation: a TypeScript mirror of contest/slips.py so the slip can preview exactly what the server will price
 * and refuse. The server re-validates everything; this only keeps the UI honest and fast.
 */
import { americanToDecimal, parlayDecimal, payoutCents } from './odds'
import { sgpPairOk, sgpPrice } from './sgp'
import type { BetKind, BoardGame, ContestRules, Market, Pick, Side } from './types'

export function makePick(game: BoardGame, market: Market, side: Side): Pick | null {
  if (market === 'spread' && game.spread && (side === 'home' || side === 'away'))
    return { gameId: game.id, market, side, line: side === 'home' ? game.spread.line : -game.spread.line, price: game.spread[side] }
  if (market === 'total' && game.total && (side === 'over' || side === 'under'))
    return { gameId: game.id, market, side, line: game.total.line, price: game.total[side] }
  if (market === 'ml' && game.ml && (side === 'home' || side === 'away')) return { gameId: game.id, market, side, line: null, price: game.ml[side] }
  return null
}

export const samePick = (a: Pick, b: Pick) => a.gameId === b.gameId && a.market === b.market && a.side === b.side

/** The line a teaser grades on: moved `points` in the bettor's favour. */
export function teasedLine(p: Pick, points: number): number | null {
  if (p.line == null) return null
  if (p.market === 'spread') return p.line + points
  if (p.market === 'total') return p.side === 'over' ? p.line - points : p.line + points
  return p.line
}

export const teaserPrice = (rules: ContestRules, points: number, legs: number): number | null => rules.teaser[String(points.toFixed(1))]?.[String(legs)] ?? null
export const teaserPointOptions = (rules: ContestRules): number[] => Object.keys(rules.teaser).map(Number).sort((a, b) => a - b)

/** Every k-index combination of 0..n-1, in lexicographic order (matches itertools.combinations). */
export function combinations(n: number, k: number): number[][] {
  const out: number[][] = []
  const cur: number[] = []
  const go = (start: number) => {
    if (cur.length === k) { out.push([...cur]); return }
    for (let i = start; i < n; i++) { cur.push(i); go(i + 1); cur.pop() }
  }
  if (k >= 1 && k <= n) go(0)
  return out
}

export const robinCount = (n: number, sizes: readonly number[]): number => [...new Set(sizes)].reduce((s, k) => s + combinations(n, k).length, 0)

/** One entry per chosen size: "by 2s" of 4 picks is 6 parlays. The slip spells this out so nobody has to guess the bet count. */
export const robinBreakdown = (n: number, sizes: readonly number[]): { k: number; count: number }[] =>
  [...new Set(sizes)].sort((a, b) => a - b).map(k => ({ k, count: combinations(n, k).length }))

export interface Preview { riskCents: number; payoutCents: number; toWinCents: number }
const mk = (risk: number, payout: number): Preview => ({ riskCents: risk, payoutCents: payout, toWinCents: payout - risk })

export const previewStraight = (p: Pick, stake: number): Preview => mk(stake, payoutCents(stake, americanToDecimal(p.price)))
export const previewParlay = (picks: readonly Pick[], stake: number): Preview => mk(stake, payoutCents(stake, parlayDecimal(picks.map(p => p.price))))

export function previewTeaser(picks: readonly Pick[], stake: number, points: number, rules: ContestRules): Preview | null {
  const price = teaserPrice(rules, points, picks.length)
  return price == null ? null : mk(stake, payoutCents(stake, americanToDecimal(price)))
}

/** A same-game parlay pays its quoted ticket price, not the product of its legs. */
export function previewSgp(picks: readonly Pick[], game: BoardGame | undefined, stake: number, rules: ContestRules): Preview | null {
  const price = sgpPrice(picks, game, rules)
  return price == null ? null : mk(stake, payoutCents(stake, americanToDecimal(price)))
}

/** Total at risk and the all-win payout of a round robin: the sum over every parlay (each at `unit`). */
export function previewRobin(picks: readonly Pick[], sizes: readonly number[], unit: number): Preview {
  let payout = 0
  let count = 0
  for (const k of [...new Set(sizes)]) {
    for (const combo of combinations(picks.length, k)) { payout += payoutCents(unit, parlayDecimal(combo.map(i => picks[i].price))); count += 1 }
  }
  return mk(unit * count, payout)
}

/** `chosen`: the player picked a bet type themselves; until then the slip follows the picks (one price is a straight, two or more a parlay). */
export interface SlipState { kind: BetKind; picks: Pick[]; teaserPoints: number; robinSizes: number[]; chosen?: boolean }

export interface Closed { closed: boolean; reason: string }
/** Betting closes `closeBufferMin` before kickoff and never opens on a started game. `now` is the server render time, ticked on the client. */
export function gameClosed(g: BoardGame, now: number, rules: ContestRules): Closed {
  if (rules.halftimeEnabled && g.halftime?.open && g.status !== 'final' && g.status !== 'void') return { closed: false, reason: '' }       // the break: straights only (slipIssue)
  if (g.status !== 'scheduled') return { closed: true, reason: g.status === 'live' ? 'In play' : g.status === 'void' ? 'Postponed' : 'Final' }
  if (now >= Date.parse(g.commence) - rules.closeBufferMin * 60_000) return { closed: true, reason: 'Betting closed' }
  return { closed: false, reason: '' }
}

/** First reason this slip cannot be placed, or null. `stake` is cents (per bet; per parlay for a round robin). Mirrors validate_slip. */
export function slipIssue(s: SlipState, stake: number, balance: number, rules: ContestRules, game?: BoardGame, halftimeGames: ReadonlySet<number> = new Set()): string | null {
  const n = s.picks.length
  const games = new Set(s.picks.map(p => p.gameId))
  if (n === 0) return 'Add a pick'
  const usd = (c: number) => `$${(c / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  const perBet = (risk: number, min = rules.minStakeCents, parlays = 1): string | null => {
    const of = `${parlays} parlays x ${usd(stake)} = ${usd(risk)}`
    const cap = `${rules.maxStakeBp / 100}% of your bankroll (${usd(Math.floor((balance * rules.maxStakeBp) / 10_000))})`
    if (stake < min) return `Minimum stake is ${usd(min)}${parlays > 1 ? ' per parlay' : ''}`
    if (risk > balance) return parlays > 1 ? `${of} is more than your bankroll` : 'Not enough bankroll'
    if (risk > min && risk * 10_000 > balance * rules.maxStakeBp) return parlays > 1 ? `${of} is over ${cap}` : `Maximum is ${cap}`
    return null
  }
  const atTheBreak = s.picks.filter(p => halftimeGames.has(p.gameId))
  if (atTheBreak.length > 0) {
    if (s.kind !== 'straight') return 'Halftime bets are straights only'
    if (stake > rules.minStakeCents && stake * 10_000 > balance * rules.halftimeMaxStakeBp) return `A halftime bet is at most ${rules.halftimeMaxStakeBp / 100}% of your bankroll`
  }
  if (s.kind === 'straight') return perBet(stake * n)
  if (s.kind === 'sgp') {
    if (!rules.sgpEnabled) return 'Same-game parlays are off'
    if (games.size !== 1) return 'Both legs must be on one game'
    if (n < 2) return 'Add the total, or a spread or moneyline, from the same game'
    if (!sgpPairOk(s.picks)) return 'Pair a spread or moneyline with the total'
    if (sgpPrice(s.picks, game, rules) == null) return 'This pair cannot be priced right now'
    return perBet(stake)
  }
  if (games.size !== n) return 'One leg per game: remove the duplicate game'
  if (s.kind === 'parlay') {
    if (n < rules.parlayMinLegs) return `Parlays need ${rules.parlayMinLegs}+ legs`
    if (n > rules.parlayMaxLegs) return `Parlays allow at most ${rules.parlayMaxLegs} legs`
    return perBet(stake)
  }
  if (s.kind === 'teaser') {
    if (n < rules.teaserMinLegs) return `Teasers need ${rules.teaserMinLegs}+ legs`
    if (n > rules.teaserMaxLegs) return `Teasers allow at most ${rules.teaserMaxLegs} legs`
    if (s.picks.some(p => p.market === 'ml')) return 'Teasers are spreads and totals only'
    if (teaserPrice(rules, s.teaserPoints, n) == null) return 'That teaser is not offered'
    return perBet(stake)
  }
  if (n < rules.roundRobinMinPicks) return `Round robins need ${rules.roundRobinMinPicks}+ picks`
  if (n > rules.roundRobinMaxPicks) return `Round robins allow at most ${rules.roundRobinMaxPicks} picks`
  if (s.robinSizes.length === 0) return 'Choose a size (by 2s, by 3s ...)'
  if (s.robinSizes.some(k => k < 2 || k > Math.min(n, rules.roundRobinMaxPicks))) return 'Pick sizes the number of picks allows'
  const count = robinCount(n, s.robinSizes)
  if (count > rules.roundRobinMaxParlays) return `${count} parlays is over the limit of ${rules.roundRobinMaxParlays}`
  return perBet(stake * count, rules.roundRobinMinUnitCents, count)
}
