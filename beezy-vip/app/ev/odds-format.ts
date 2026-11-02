import { americanToDecimal } from '@/lib/ev-math'
import { formatOdds } from '@/lib/odds'
import { fairAmericanOf, type SgpProxyRow } from '@/lib/odds-screen'

const CT_TIME = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago', weekday: 'short', hour: 'numeric', minute: '2-digit' })

export function startLabel(iso: string): string {
  const d = new Date(iso)
  return isNaN(d.getTime()) ? '' : CT_TIME.format(d).replace(' AM', 'a').replace(' PM', 'p')
}

export function ageLabel(mins: number | null): string {
  if (mins == null) return '--'
  return mins < 90 ? `${Math.max(1, Math.round(mins))}m` : mins < 2880 ? `${(mins / 60).toFixed(1)}h` : `${Math.round(mins / 1440)}d`
}

export function signed(x: number, digits = 1): string {
  return `${x >= 0 ? '+' : ''}${x.toFixed(digits)}`
}

export function usd(x: number): string {
  return `$${x.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function fairText(p: number | null): string {
  if (p == null || !(p > 0 && p < 1)) return '--'
  return formatOdds(fairAmericanOf(p))
}

/** Model EV of an SGP row: neutral text, never the green "positive EV" styling (the number comes from assumed constants). */
export function sgpEvText(r: SgpProxyRow): string {
  return `${r.evPct > 0 ? '+' : ''}${r.evPct.toFixed(1)}% model EV`
}

export function dec(am: number): number { return americanToDecimal(am) }

/** "NO 77c" on an exchange (the price is a cent price), the American price elsewhere. */
export function underText(book: string, am: number): string {
  return book === 'kalshi' || book === 'novig' ? `NO ${Math.round((1 / dec(am)) * 100)}c` : `UNDER ${formatOdds(am)}`
}
