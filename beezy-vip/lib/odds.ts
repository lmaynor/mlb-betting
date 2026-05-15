/**
 * Betting math utilities — mirrors mlb_core/odds/utils.py
 */

/** American odds → implied probability (includes vig) */
export function americanToImpliedProb(odds: number): number {
  if (odds > 0) return 100 / (odds + 100)
  return Math.abs(odds) / (Math.abs(odds) + 100)
}

/** Remove vig from a two-sided market */
export function removeVig(
  prob1: number,
  prob2: number
): { fair1: number; fair2: number; vig: number } {
  const total = prob1 + prob2
  const vig   = (total - 1) * 100           // vig as a percentage
  const fair1 = prob1 / total
  const fair2 = prob2 / total
  return { fair1, fair2, vig }
}

/** Fair probability → American odds */
export function probToAmerican(prob: number): number {
  if (prob >= 0.5) return -Math.round((prob / (1 - prob)) * 100)
  return Math.round(((1 - prob) / prob) * 100)
}

/** Kelly criterion stake sizing */
export function kellyStake(
  bankroll:   number,
  odds:       number,       // American odds
  modelProb:  number,       // Model's estimated win probability
  fraction:   number = 1.0  // 0.5 = half Kelly
): {
  fullKelly:   number
  halfKelly:   number
  stakeFullDollars: number
  stakeHalfDollars: number
  edge:        number
} {
  const impliedProb = americanToImpliedProb(odds)
  const edge        = modelProb - impliedProb

  // Decimal odds
  const decOdds = odds > 0 ? odds / 100 + 1 : 100 / Math.abs(odds) + 1
  const b       = decOdds - 1          // net odds per unit wagered

  const fullKellyPct = edge > 0 ? (b * modelProb - (1 - modelProb)) / b : 0
  const halfKellyPct = fullKellyPct * 0.5

  return {
    fullKelly:        Math.max(0, parseFloat((fullKellyPct * 100).toFixed(2))),
    halfKelly:        Math.max(0, parseFloat((halfKellyPct * 100).toFixed(2))),
    stakeFullDollars: Math.max(0, parseFloat((fullKellyPct * bankroll).toFixed(2))),
    stakeHalfDollars: Math.max(0, parseFloat((halfKellyPct * bankroll).toFixed(2))),
    edge:             parseFloat((edge * 100).toFixed(2)),
  }
}

/** Format American odds for display */
export function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`
}

/** Format probability as percentage */
export function formatProb(prob: number): string {
  return `${(prob * 100).toFixed(1)}%`
}

/** Format ROI */
export function formatROI(roi: number): string {
  return `${roi > 0 ? '+' : ''}${roi.toFixed(1)}%`
}
