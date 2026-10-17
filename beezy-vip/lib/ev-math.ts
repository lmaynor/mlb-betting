/**
 * +EV terminal math. Kelly here is the same non-compounding rescale the
 * backend uses (mlb.analysis.ev_kelly_bankroll): stake = clip(kelly * fraction, 0, cap) * bankroll.
 */
import { americanToImpliedProb } from '@/lib/odds'

export function americanToDecimal(odds: number): number {
  return odds > 0 ? odds / 100 + 1 : 100 / Math.abs(odds) + 1
}

/** Full-Kelly fraction of bankroll for a bet paying `decimal` with win probability `p`. */
export function fullKelly(p: number, decimal: number): number {
  const b = decimal - 1
  if (b <= 0 || p <= 0 || p >= 1) return 0
  return Math.max(0, (b * p - (1 - p)) / b)
}

export interface StakeSizing { pct: number; dollars: number; units: number }

/**
 * Concave shrinkage of a CLAIMED edge (a fraction, 0.08 = 8%) before sizing: identity up to `knee`, `tailSlope` per unit
 * above it, capped at `maxEffEdge`. Graded +EV alerts claiming 12%+ edge averaged 61.6% fair probability but hit 49.9%
 * (n=2,474), so the largest apparent edges are partly adverse selection. Mirror of mlb_core.risk.calibration.shrink_edge.
 */
export function shrinkEdge(edge: number, knee = 0.06, tailSlope = 0.35, maxEffEdge = 0.10): number {
  if (!isFinite(edge)) return 0
  const mag = Math.abs(edge)
  const eff = Math.min(mag <= knee ? mag : knee + tailSlope * (mag - knee), maxEffEdge)
  return edge >= 0 ? eff : -eff
}

/**
 * Fractional-Kelly stake (default quarter Kelly, capped at 5% of bankroll). A positive edge over the price
 * (p - 1/decimal) is shrunk with shrinkEdge before sizing; pass `shrink = false` for the raw-probability stake.
 */
export function kellySizing(
  bankroll: number, p: number | null | undefined, decimal: number, fraction = 0.25, cap = 0.05, shrink = true,
): StakeSizing {
  if (p == null || !isFinite(bankroll) || bankroll <= 0) return { pct: 0, dollars: 0, units: 0 }
  const implied = decimal > 0 ? 1 / decimal : 1
  const pEff = shrink && p > implied ? implied + shrinkEdge(p - implied) : p
  const pct = Math.min(Math.max(fullKelly(pEff, decimal) * fraction, 0), cap)
  const dollars = pct * bankroll
  return { pct, dollars, units: pct * 100 }
}

/** American odds of a fair probability, formatted (e.g. 0.478 -> "+109"). */
export function fairAmerican(p: number | null | undefined): string {
  if (p == null || p <= 0 || p >= 1) return '--'
  const a = p >= 0.5 ? -Math.round((p / (1 - p)) * 100) : Math.round(((1 - p) / p) * 100)
  return a > 0 ? `+${a}` : `${a}`
}

export function fmtPct(x: number | null | undefined, digits = 1, signed = false): string {
  if (x == null || !isFinite(x)) return '--'
  const v = (x * 100).toFixed(digits)
  return `${signed && x > 0 ? '+' : ''}${v}%`
}

export function fmtMoney(x: number | null | undefined, signed = false): string {
  if (x == null || !isFinite(x)) return '--'
  const abs = Math.abs(x).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
  return `${x < 0 ? '-' : signed && x > 0 ? '+' : ''}$${abs}`
}

export { americanToImpliedProb }
