/**
 * Odds screen math (/ev/odds). The backend ships every book's raw American over / under price per prop line and NO fair
 * value (ev/odds_board.py); the viewer picks the reference books and the de-vig method, and everything below is computed
 * from that choice in the browser.
 *
 * Fair value = the EQUAL-WEIGHT mean of each reference book's own two-sided de-vigged P(over) (the +EV terminal's backend
 * consensus uses the median; the mean is what lets a viewer weight a chosen set of sharp books). A book never anchors its
 * own price: when the best price sits at a reference book, that book is left out of the fair value (leave-self-out, the
 * same rule as ev.props.scan_book_outliers).
 */
import { americanToDecimal } from '@/lib/ev-math'
import { americanToImpliedProb } from '@/lib/odds'

export type DevigMethod = 'proportional' | 'power'
export type SideFilter = 'both' | 'over' | 'under'

export interface BoardBook { book: string; over: number | null; under: number | null }
export interface BoardRow {
  key: string
  game_key: string
  commence: string
  away: string
  home: string
  player: string
  player_norm: string
  player_id?: number | string | null
  team?: string | null
  stat: string
  line: number
  books: BoardBook[]
}
export interface OddsBoard {
  sport: string
  status: string
  stale: boolean
  as_of: string | null
  age_minutes: number | null
  count: number
  rows: BoardRow[]
  books: string[]
  offshore_books: string[]
  stats: string[]
  games: { game_key: string; start: string; away: string; home: string }[]
  lines_source: string[]
  lines_age_minutes: number | null
  locked?: number
}

/** De-vig one book's two-sided market -> fair P(over). Power: the exponent k with pO^k + pU^k = 1 (solved by bisection). */
export function devigOver(over: number | null, under: number | null, method: DevigMethod): number | null {
  if (over == null || under == null || !isFinite(over) || !isFinite(under) || over === 0 || under === 0) return null
  const pO = americanToImpliedProb(over)
  const pU = americanToImpliedProb(under)
  const total = pO + pU
  if (!(total > 0)) return null
  if (method === 'proportional' || total <= 1) return pO / total
  let lo = 1
  let hi = 8
  for (let i = 0; i < 40; i++) {   // 7 / 2^40 on k: far below any displayed digit
    const k = (lo + hi) / 2
    if (Math.pow(pO, k) + Math.pow(pU, k) > 1) lo = k
    else hi = k
  }
  return Math.pow(pO, (lo + hi) / 2)
}

export interface Fair { pOver: number; n: number }

/**
 * Equal-weight fair P(over) over `refs` (null = every book with a two-sided price), skipping `exclude`. null when no
 * reference book has a two-sided price.
 */
export function fairOver(books: BoardBook[], refs: ReadonlySet<string> | null, method: DevigMethod, exclude?: string): Fair | null {
  let sum = 0
  let n = 0
  for (const b of books) {
    if (b.book === exclude || (refs && !refs.has(b.book))) continue
    const p = devigOver(b.over, b.under, method)
    if (p == null) continue
    sum += p
    n += 1
  }
  return n ? { pOver: sum / n, n } : null
}

export interface ScreenOptions {
  /** reference book keys; empty = every book */
  refs: readonly string[]
  method: DevigMethod
  /** books a viewer can bet at (offshore books and, when set, anything outside My Books are excluded); null = no cut */
  bettable: ReadonlySet<string> | null
  /** fewest reference books behind a fair value; below it the row is still listed (prices only) with no fair value / EV */
  minRef: number
  side: SideFilter
}

export interface ScreenRow {
  key: string
  row: BoardRow
  side: 'OVER' | 'UNDER'
  /** the line label as shown: o24.5 / u24.5 */
  label: string
  bestBook: string
  bestAmerican: number
  /** books quoting this exact best price on the target side */
  bestTies: string[]
  /** null = fewer than `minRef` reference books priced this line both ways: a price-comparison row with no fair value */
  pFair: number | null
  nRef: number
  /** fair value of the side as American odds (null with pFair) */
  fairAmerican: number | null
  /** de-vigged implied probability of the best price (before vig removal it is just 1/decimal) */
  implied: number
  /** null with pFair */
  ev: number | null
}

function fairAmericanOf(p: number): number {
  return p >= 0.5 ? -Math.round((p / (1 - p)) * 100) : Math.round(((1 - p) / p) * 100)
}

/** Best price on `side` among bettable books: higher decimal payout wins. */
export function bestPrice(books: BoardBook[], side: 'OVER' | 'UNDER', bettable: ReadonlySet<string> | null): { book: string; american: number; ties: string[] } | null {
  let best: { book: string; american: number; dec: number } | null = null
  for (const b of books) {
    if (bettable && !bettable.has(b.book)) continue
    const a = side === 'OVER' ? b.over : b.under
    if (a == null || !isFinite(a) || a === 0) continue
    const dec = americanToDecimal(a)
    if (!best || dec > best.dec) best = { book: b.book, american: a, dec }
  }
  if (!best) return null
  const ties = books
    .filter(b => (!bettable || bettable.has(b.book)) && (side === 'OVER' ? b.over : b.under) === best!.american)
    .map(b => b.book)
  return { book: best.book, american: best.american, ties }
}

// Each quote is de-vigged once per (row, method) and kept for as long as the row object lives (a refresh replaces the
// rows), so a filter click or a search keystroke never repeats the power-method solve.
const FAIRS = new WeakMap<BoardRow, Partial<Record<DevigMethod, (number | null)[]>>>()
function rowFairs(row: BoardRow, method: DevigMethod): (number | null)[] {
  let e = FAIRS.get(row)
  if (!e) { e = {}; FAIRS.set(row, e) }
  return e[method] ?? (e[method] = row.books.map(b => devigOver(b.over, b.under, method)))
}

/** One screen row per (prop line, side): the best bettable price, the fair value of that side from the reference books, EV. */
export function buildScreenRows(rows: BoardRow[], opts: ScreenOptions): ScreenRow[] {
  const refs = opts.refs.length ? new Set(opts.refs) : null
  const sides: ('OVER' | 'UNDER')[] = opts.side === 'over' ? ['OVER'] : opts.side === 'under' ? ['UNDER'] : ['OVER', 'UNDER']
  const out: ScreenRow[] = []
  for (const row of rows) {
    const fairs = rowFairs(row, opts.method)
    let sum = 0
    let n = 0
    const own = new Map<string, number>()
    row.books.forEach((b, i) => {
      const p = fairs[i]
      if (p == null || (refs && !refs.has(b.book))) return
      sum += p
      n += 1
      own.set(b.book, p)
    })
    for (const side of sides) {
      const best = bestPrice(row.books, side, opts.bettable)
      if (!best) continue
      // leave-self-out: the best book never anchors its own price (same result as fairOver(..., best.book))
      const mine = own.get(best.book)
      const nRef = mine == null ? n : n - 1
      const dec = americanToDecimal(best.american)
      const label = `${side === 'OVER' ? 'o' : 'u'}${row.line}`
      // Too few reference books to price it: still a line worth comparing, so list it without a fair value or EV.
      const pOver = nRef >= 1 && nRef >= opts.minRef ? (mine == null ? sum : sum - mine) / nRef : null
      const pFair = pOver == null ? null : side === 'OVER' ? pOver : 1 - pOver
      if (pFair == null || !(pFair > 0 && pFair < 1)) {
        out.push({ key: `${row.key}|${side}`, row, side, label, bestBook: best.book, bestAmerican: best.american, bestTies: best.ties,
          pFair: null, nRef: Math.max(nRef, 0), fairAmerican: null, implied: 1 / dec, ev: null })
        continue
      }
      out.push({
        key: `${row.key}|${side}`, row, side,
        label,
        bestBook: best.book, bestAmerican: best.american, bestTies: best.ties,
        pFair, nRef, fairAmerican: fairAmericanOf(pFair),
        implied: 1 / dec, ev: pFair * dec - 1,
      })
    }
  }
  return out
}

/** Stat keys -> short labels for the Prop column (mirrors ev/daily_projections.STAT_LABEL). */
export const STAT_LABEL: Record<string, string> = {
  pass_yds: 'Pass Yds', rush_yds: 'Rush Yds', rec_yds: 'Rec Yds', receptions: 'Receptions', pass_tds: 'Pass TDs',
  anytime_td: 'Anytime TD', pass_completions: 'Completions', pass_attempts: 'Pass Att', rush_attempts: 'Carries',
  interceptions: 'INTs', sog: 'SOG', goals: 'Goals', points: 'Points', assists: 'Assists', saves: 'Saves',
  blocked_shots: 'Blocks (shots)', rebounds: 'Rebounds', threes: '3PM', pra: 'Pts+Reb+Ast', pr: 'Pts+Reb', pa: 'Pts+Ast',
  ra: 'Reb+Ast', steals: 'Steals', blocks: 'Blocks', turnovers: 'Turnovers', stocks: 'Stl+Blk', minutes: 'Minutes',
  hits: 'Hits', pim: 'PIM', pp_points: 'PP Points',
}
