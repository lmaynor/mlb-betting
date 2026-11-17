/**
 * Same-game parlay pricing: a TypeScript mirror of contest/sgp.py so the slip can show the ticket price the server will quote.
 * Two legs on ONE game (a spread or the moneyline, plus the total) are priced together with a Gaussian copula, because a favourite that
 * covers tends to do it in a higher-scoring game. The server re-prices the ticket and refuses a mismatch (PRICE_CHANGED).
 * Correlations are measured on one football season (docs/analysis/contest_sportsbook_experiments_2026-11-17.md): UNVALIDATED.
 */
import { devig, normCdf, normInv } from './odds'
import { probToAmerican } from './margin'
import type { BoardGame, ContestRules, Pick } from './types'

/** P(Z1 < a, Z2 < b) for standard normals with correlation rho: Simpson's rule on int_{-8}^{a} phi(x) Phi((b - rho x) / s) dx (same grid as the server). */
export function bvnCdf(a: number, b: number, rho: number, steps = 2000): number {
  if (a <= -8) return 0
  const s = Math.sqrt(Math.max(1 - rho * rho, 1e-12))
  const h = (a + 8) / steps
  let total = 0
  for (let i = 0; i <= steps; i++) {
    const x = -8 + i * h
    const f = (Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI)) * normCdf((b - rho * x) / s)
    total += i === 0 || i === steps ? f : i % 2 ? 4 * f : 2 * f
  }
  return (total * h) / 3
}

/** The pair a same-game parlay accepts: one game, a spread or the moneyline with the total. */
export function sgpPairOk(picks: readonly Pick[]): boolean {
  if (picks.length !== 2 || picks[0].gameId !== picks[1].gameId) return false
  const m = new Set(picks.map(p => p.market))
  return m.size === 2 && m.has('total') && (m.has('spread') || m.has('ml'))
}

/** De-vigged win probability of the pick on the game's current board, or null. */
function pickProb(g: BoardGame, p: Pick): number | null {
  if (p.market === 'spread' && g.spread && (p.side === 'home' || p.side === 'away')) return devig(g.spread[p.side], g.spread[p.side === 'home' ? 'away' : 'home'])
  if (p.market === 'total' && g.total && (p.side === 'over' || p.side === 'under')) return devig(g.total[p.side], g.total[p.side === 'over' ? 'under' : 'over'])
  if (p.market === 'ml' && g.ml && (p.side === 'home' || p.side === 'away')) return devig(g.ml[p.side], g.ml[p.side === 'home' ? 'away' : 'home'])
  return null
}

/** True when the pick is on the board's own number (an alt line cannot ride in a same-game parlay). */
function onMainLine(g: BoardGame, p: Pick): boolean {
  if (p.market === 'spread' && g.spread) return p.line === (p.side === 'home' ? g.spread.line : -g.spread.line)
  if (p.market === 'total' && g.total) return p.line === g.total.line
  return p.market === 'ml'
}

/** The contest's American ticket price for the pair, or null when it cannot be priced. */
export function sgpPrice(picks: readonly Pick[], g: BoardGame | undefined, rules: ContestRules): number | null {
  if (!g || !rules.sgpEnabled || !sgpPairOk(picks)) return null
  const probs: number[] = []
  for (const p of picks) {
    const q = pickProb(g, p)
    if (q == null || !onMainLine(g, p)) return null
    probs.push(Math.min(Math.max(q, 1e-6), 1 - 1e-6))
  }
  const sideIx = picks.findIndex(p => p.market !== 'total')
  const side = picks[sideIx]
  const total = picks[1 - sideIx]
  const favourite = side.market === 'ml' ? probs[sideIx] > 0.5 : (side.line ?? 0) < 0
  const base = (side.market === 'ml' ? rules.sgpRhoMlBp : rules.sgpRhoBp) / 10_000
  const rho = (favourite && total.side === 'over') || (!favourite && total.side === 'under') ? base : -base
  const joint = bvnCdf(normInv(probs[0]), normInv(probs[1]), rho)
  if (joint < 0.01 || joint > 0.9) return null
  return probToAmerican(Math.min(joint * (1 + rules.sgpMarginBp / 10_000), 0.985))
}
