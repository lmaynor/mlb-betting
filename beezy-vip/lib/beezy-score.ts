import type { Bet } from './types'
import { type ScoreTier, TIER_COLOR, TIER_LABEL } from './tokens'

export type { ScoreTier }
export { TIER_COLOR, TIER_LABEL }

/**
 * Composite 0-100 confidence score for a bet.
 *
 * Components (max 100, deliberately hard to saturate):
 *   Edge (45 pts max, full at 15% edge) - primary signal
 *   Model-market prob gap (25 pts max, full at ~12.5 pct points)
 *   Kelly trigger (15 / 7)
 *   Odds value (15 / 10 / 5) - favor playable prices over heavy chalk
 *
 * 100 requires a 15%+ edge AND a 12.5-point prob gap AND a Kelly-sized bet
 * at better than -120 -- exceptional by construction. Distribution goal:
 * ~15% strong, ~45% lean, ~40% watch. Re-tune against settled results.
 */
// edge is stored as a FRACTION (0.08) on some rows and already-PERCENT (8)
// on others; probs are usually 0-1 but occasionally pct. Normalize both --
// without this the raw-unit rows blew past every cap and ~everything scored
// 100 (the score's whole failure mode).
function edgeAsPct(e: number | null | undefined): number {
  if (e == null) return 0
  return Math.abs(e) < 2 ? e * 100 : e
}
function probAsPct(p: number | null | undefined): number {
  if (p == null) return 0
  return p <= 1.5 ? p * 100 : p
}

export function beezyscore(bet: Bet): number {
  const edgePct = edgeAsPct(bet.edge)
  const edgePoints = Math.min(Math.max(edgePct, 0) * 3, 45)
  const probGap = probAsPct(bet.model_prob) - probAsPct(bet.market_prob)
  const probPoints = Math.min(Math.max(probGap * 2, 0), 25)
  const kellyPoints = bet.kelly_triggered ? 15 : 7
  const odds = bet.odds ?? -200
  const oddsPoints = odds > -120 ? 15 : odds > -170 ? 10 : 5
  return Math.round(Math.max(0, Math.min(100, edgePoints + probPoints + kellyPoints + oddsPoints)))
}

export function scoreTier(score: number): ScoreTier {
  if (score >= 65) return 'strong'
  if (score >= 40) return 'lean'
  return 'watch'
}
