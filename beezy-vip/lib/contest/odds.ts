/** American odds, money and line formatting for the Bankroll Challenge. Previews only: the server (contest/odds.py) is authoritative. */

export function americanToDecimal(american: number): number {
  const a = Math.trunc(american)
  if (!Number.isFinite(a) || Math.abs(a) < 100) throw new Error(`not an American price: ${american}`)
  return 1 + (a < 0 ? 100 / -a : a / 100)
}

export function parlayDecimal(prices: readonly number[]): number {
  return prices.reduce((acc, p) => acc * americanToDecimal(p), 1)
}

/** Total return in cents (stake included), rounded half-up to the cent like the engine. The epsilon absorbs float error at x.5. */
export function payoutCents(stakeCents: number, decimalOdds: number): number {
  return Math.floor(stakeCents * decimalOdds + 0.5 + 1e-7)
}

export const fmtPrice = (p: number | null | undefined): string => (p == null ? '--' : p > 0 ? `+${p}` : `${p}`)

export const fmtLineSigned = (l: number | null | undefined): string => (l == null ? '' : l > 0 ? `+${l}` : l === 0 ? 'PK' : `${l}`)

export function fmtMoney(cents: number, opts: { sign?: boolean; whole?: boolean } = {}): string {
  const neg = cents < 0
  const abs = Math.abs(cents) / 100
  const body = abs.toLocaleString('en-US', opts.whole && Number.isInteger(abs) ? { maximumFractionDigits: 0 } : { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return `${neg ? '-' : opts.sign && cents > 0 ? '+' : ''}$${body}`
}

/** "$25" / "25.50" typed by a player -> integer cents, or null when it is not a positive amount with at most two decimals. */
export function parseDollars(raw: string): number | null {
  const m = raw.trim().replace(/^\$/, '').replace(/,/g, '').match(/^(\d+)(?:\.(\d{1,2}))?$/)
  if (!m) return null
  const cents = Number(m[1]) * 100 + Number((m[2] ?? '').padEnd(2, '0') || 0)
  return cents > 0 ? cents : null
}

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
