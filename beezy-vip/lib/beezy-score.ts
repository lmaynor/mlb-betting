import type { Bet } from './types'
import { type ScoreTier, TIER_COLOR, TIER_LABEL } from './tokens'

export type { ScoreTier }
export { TIER_COLOR, TIER_LABEL }

/**
 * Composite 0-100 confidence score for a bet.
 *
 * Components:
 *   Edge (40 pts max) - primary signal
 *   Kelly trigger (20 pts) - model has enough confidence to size
 *   Odds value (20 pts) - favor moderate favorites, penalize big chalk
 *   Model vs market gap (20 pts max) - independent of edge direction
 *
 * Tune the edge multiplier (currently 4x) against settled results once
 * ~200 bets are in. Distribution goal: ~20% strong, ~45% lean, ~35% watch.
 */
export function beezyscore(bet: Bet): number {
  const edgePoints = Math.min(((bet.edge ?? 0) * 100) * 4, 40)
  const kellyPoints = bet.kelly_triggered ? 20 : 10
  const oddsPoints = (bet.odds ?? -200) > -130 ? 20
    : (bet.odds ?? -200) > -180 ? 12 : 6
  const probGap = ((bet.model_prob ?? 0) - (bet.market_prob ?? 0)) * 100
  const probPoints = Math.min(Math.max(probGap * 2, 0), 20)
  return Math.round(Math.max(0, Math.min(100, edgePoints + kellyPoints + oddsPoints + probPoints)))
}

export function scoreTier(score: number): ScoreTier {
  if (score >= 65) return 'strong'
  if (score >= 40) return 'lean'
  return 'watch'
}
