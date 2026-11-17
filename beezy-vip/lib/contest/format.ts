/** How prices are shown. The engine and the server always use American odds; this only changes the display of a price. */
import { americanToDecimal } from './odds'

export type OddsFormat = 'american' | 'decimal' | 'fractional'
export const ODDS_FORMATS: { id: OddsFormat; label: string }[] = [{ id: 'american', label: 'American' }, { id: 'decimal', label: 'Decimal' }, { id: 'fractional', label: 'Fractional' }]
export const isOddsFormat = (x: unknown): x is OddsFormat => x === 'american' || x === 'decimal' || x === 'fractional'

const gcd = (a: number, b: number): number => (b === 0 ? a : gcd(b, a % b))

/** +150 -> "3/2", -110 -> "10/11", +100 -> "1/1". Exact: the American price fixes the fraction (profit per unit staked). */
export function toFractional(american: number): string {
  const a = Math.trunc(american)
  const [num, den] = a > 0 ? [a, 100] : [100, -a]
  const g = gcd(num, den)
  return `${num / g}/${den / g}`
}

export function fmtOdds(price: number | null | undefined, format: OddsFormat = 'american'): string {
  if (price == null) return '--'
  if (format === 'decimal') return americanToDecimal(price).toFixed(2)
  if (format === 'fractional') return toFractional(price)
  return price > 0 ? `+${price}` : `${price}`
}
