/**
 * Query-string filters shared by every picks page (/picks, /picks/[sport]). Pure, so the server pages and the FilterBar
 * agree on what a URL means and the behavior is testable without rendering.
 *
 *   market  registry system key (NFL_REC_YDS, K, EV)        tier   STRONG | LEAN | WATCH
 *   book    one sportsbook (display name, any case/spaces)  books  comma list of canonical book keys ("my books")
 *   q       player / team / game text                        sort   score | edge | prob | odds | date   dir  asc | desc
 *   sport   cross-sport page only
 */
import { scoreTier, TIER_CUTS } from '@/lib/beezy-score'

export type TierFilter = 'STRONG' | 'LEAN' | 'WATCH'
export type PickSort = 'score' | 'edge' | 'prob' | 'odds' | 'date'

export const SORT_OPTIONS: { value: PickSort; label: string }[] = [
  { value: 'score', label: 'SCORE' }, { value: 'edge', label: 'EDGE %' }, { value: 'prob', label: 'MODEL PROB' },
  { value: 'odds', label: 'ODDS' }, { value: 'date', label: 'TIME' },
]

export const TIER_OPTIONS: { value: TierFilter; label: string }[] = [
  { value: 'STRONG', label: `STRONG (${TIER_CUTS.strong}+)` },
  { value: 'LEAN', label: `LEAN (${TIER_CUTS.lean}-${TIER_CUTS.strong - 1})` },
  { value: 'WATCH', label: `WATCH (<${TIER_CUTS.lean})` },
]

/** Sportsbooks and exchanges offered as chips; the value is what ?book= carries (lowercase, no spaces). */
export const BOOK_CHIPS = ['DraftKings', 'FanDuel', 'BetMGM', 'Caesars', 'Bet365', 'Novig', 'Kalshi'] as const
export const bookParam = (name: string) => name.toLowerCase().replace(/ /g, '')

export interface PickFilters {
  market?: string
  tier?: TierFilter
  book?: string
  books: string[]
  q: string
  sort: PickSort
  dir: 'asc' | 'desc'
  sport?: string
}

const SORTS = new Set<string>(SORT_OPTIONS.map(o => o.value))

export function parsePickFilters(sp: Record<string, string | undefined>): PickFilters {
  const up = (sp.tier ?? '').toUpperCase()
  const tier = up === 'STRONG' || up === 'LEAN' || up === 'WATCH' ? (up as TierFilter) : undefined
  const sort = SORTS.has(sp.sort ?? '') ? (sp.sort as PickSort) : 'score'
  const books = (sp.books ?? '').split(',').map(b => b.trim().toLowerCase()).filter(Boolean)
  return {
    market: sp.market && sp.market !== 'ALL' ? sp.market : undefined,
    tier,
    book: sp.book && sp.book !== 'ALL' ? bookParam(sp.book) : undefined,
    books,
    q: (sp.q ?? '').slice(0, 60),
    sort,
    dir: sp.dir === 'asc' ? 'asc' : 'desc',
    sport: sp.sport && sp.sport !== 'ALL' ? sp.sport.toLowerCase() : undefined,
  }
}

export function matchesTier(score: number, tier: TierFilter | undefined): boolean {
  return tier == null || scoreTier(score).toUpperCase() === tier
}

/** A row passes the book filters when it has no book to judge (kept visible) or its book is the one / one of "my books". */
export function matchesBook(book: string | null | undefined, f: Pick<PickFilters, 'book' | 'books'>): boolean {
  const b = (book ?? '').toLowerCase().replace(/ /g, '')
  if (f.book && b !== f.book) return false
  if (f.books.length > 0 && !f.books.includes(b)) return false
  return true
}

export function matchesQuery(text: string, q: string): boolean {
  const needle = q.trim().toLowerCase()
  return !needle || text.toLowerCase().includes(needle)
}
