/**
 * Query-string state shared by every picks page (/picks, /picks/[sport], and a system's page). Pure, so the server pages and
 * the FilterBar agree on what a URL means and the behavior is testable without rendering.
 *
 *   day     today | yesterday (default today)           game    one game of the day (the games strip)
 *   market  registry system key (NFL_REC_YDS, K, EV)    signal  priced | confluence | ev | strong | lean (see SIGNAL_OPTIONS)
 *   book    one sportsbook (display name, any case)     books   comma list of canonical book keys ("my books")
 *   q       player / team / city text                   sort    score | edge | prob | odds     dir  asc | desc
 *   result  win | loss | pending (settled MLB days)     sport   cross-sport page only
 *   tier    legacy (STRONG | LEAN | WATCH), still honoured so old links keep working
 */
import { scoreTier, TIER_CUTS } from '@/lib/beezy-score'

export type TierFilter = 'STRONG' | 'LEAN' | 'WATCH'
export type PickSort = 'score' | 'edge' | 'prob' | 'odds'
export type PickDay = 'today' | 'yesterday'
export type SignalFilter = 'priced' | 'confluence' | 'ev' | 'strong' | 'lean'

export const SORT_OPTIONS: { value: PickSort; label: string }[] = [
  { value: 'score', label: 'Score' }, { value: 'edge', label: 'Edge %' }, { value: 'prob', label: 'Model prob' }, { value: 'odds', label: 'Best price' },
]

export const SIGNAL_OPTIONS: { value: SignalFilter; label: string }[] = [
  { value: 'priced', label: 'Has a line' },
  { value: 'confluence', label: 'Model + market agree' },
  { value: 'ev', label: '+EV only' },
  { value: 'strong', label: `Strong (${TIER_CUTS.strong}+)` },
  { value: 'lean', label: `Lean or better (${TIER_CUTS.lean}+)` },
]

export const TIER_OPTIONS: { value: TierFilter; label: string }[] = [
  { value: 'STRONG', label: `STRONG (${TIER_CUTS.strong}+)` },
  { value: 'LEAN', label: `LEAN (${TIER_CUTS.lean}-${TIER_CUTS.strong - 1})` },
  { value: 'WATCH', label: `WATCH (<${TIER_CUTS.lean})` },
]

/** Sportsbooks and exchanges offered in the book menu; the value is what ?book= carries (lowercase, no spaces). */
export const BOOK_CHIPS = ['DraftKings', 'FanDuel', 'BetMGM', 'Caesars', 'Bet365', 'Novig', 'Kalshi'] as const
export const bookParam = (name: string) => name.toLowerCase().replace(/ /g, '')

export interface PickFilters {
  day: PickDay
  /** an upcoming game day (YYYY-MM-DD) of today's board: a view, like `day`, not a filter */
  date?: string
  game?: string
  market?: string
  signal?: SignalFilter
  tier?: TierFilter
  book?: string
  books: string[]
  q: string
  sort: PickSort
  dir: 'asc' | 'desc'
  result?: 'win' | 'loss' | 'pending'
  sport?: string
}

const SORTS = new Set<string>(SORT_OPTIONS.map(o => o.value))
const SIGNALS = new Set<string>(SIGNAL_OPTIONS.map(o => o.value))

export function parsePickFilters(sp: Record<string, string | undefined>): PickFilters {
  const up = (sp.tier ?? '').toUpperCase()
  const tier = up === 'STRONG' || up === 'LEAN' || up === 'WATCH' ? (up as TierFilter) : undefined
  const sig = (sp.signal ?? '').toLowerCase()
  const sort = SORTS.has(sp.sort ?? '') ? (sp.sort as PickSort) : 'score'
  const books = (sp.books ?? '').split(',').map(b => b.trim().toLowerCase()).filter(Boolean)
  const result = (sp.result ?? sp.status ?? '').toLowerCase()
  return {
    day: sp.day === 'yesterday' ? 'yesterday' : 'today',
    date: sp.day !== 'yesterday' && /^\d{4}-\d{2}-\d{2}$/.test(sp.date ?? '') ? sp.date : undefined,
    game: sp.game ? sp.game.slice(0, 80) : undefined,
    market: sp.market && sp.market !== 'ALL' ? sp.market : undefined,
    signal: SIGNALS.has(sig) ? (sig as SignalFilter) : undefined,
    tier,
    book: sp.book && sp.book !== 'ALL' ? bookParam(sp.book) : undefined,
    books,
    q: (sp.q ?? '').slice(0, 60),
    sort,
    dir: sp.dir === 'asc' ? 'asc' : 'desc',
    result: result === 'win' || result === 'loss' || result === 'pending' ? result : undefined,
    sport: sp.sport && sp.sport !== 'ALL' ? sp.sport.toLowerCase() : undefined,
  }
}

export function matchesTier(score: number, tier: TierFilter | undefined): boolean {
  return tier == null || scoreTier(score).toUpperCase() === tier
}

/** The facts about a row the SIGNAL menu needs; every picks row shape can supply them. */
export interface SignalFacts { score: number; hasLine: boolean; confluence: boolean; edge: number | null }

export function matchesSignal(f: SignalFacts, signal: SignalFilter | undefined): boolean {
  switch (signal) {
    case undefined: return true
    case 'priced': return f.hasLine
    case 'confluence': return f.confluence
    case 'ev': return (f.edge ?? 0) > 0
    case 'strong': return f.score >= TIER_CUTS.strong
    case 'lean': return f.score >= TIER_CUTS.lean
  }
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

/** The filters that are set, for the bar's "clear" button and the empty-state wording. `day` and `sort` are views, not filters. */
export function activeFilterCount(f: PickFilters): number {
  return [f.game, f.market, f.signal, f.tier, f.book, f.books.length > 0, f.q, f.result, f.sport].filter(Boolean).length
}
