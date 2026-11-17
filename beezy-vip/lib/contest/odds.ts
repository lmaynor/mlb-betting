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
