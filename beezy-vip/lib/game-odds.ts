/**
 * Game-line PRICE screen (/ev/games, view=prices): every book's moneyline, spread and total for NBA / NHL side by side.
 *
 * The backend ships raw American prices per book (`GET /api/public/games/odds`), no fair value. A game row is a two-way
 * market: `a` is the AWAY side (moneyline, spread) or the OVER (total); `b` is the HOME side or the UNDER. That is exactly
 * the over / under shape of a prop line, so the de-vig, leave-self-out fair value, EV and arbitrage maths are NOT
 * re-derived here: a game row is adapted to the Odds Terminal's BoardRow (`a` -> over, `b` -> under) and runs through
 * lib/odds-screen.ts (buildScreenRows, computeRowArb, kalshiEffectiveDecimal ...) unchanged.
 *
 * Everything non-MLB is UNVALIDATED market data: these are prices and a de-vigged consensus, not a tested edge.
 */
import { kellySizing } from '@/lib/ev-math'
import { formatOdds } from '@/lib/odds'
import { isOffshore } from '@/lib/offshore'
import { bookDisplay, canonBook } from '@/lib/tokens'
import { buildScreenRows, computeRowArb, type ArbResult, type BoardRow, type DevigMethod } from '@/lib/odds-screen'

export type GameMarket = 'ml' | 'spread' | 'total'
export type GameOddsStatus = 'live' | 'stale' | 'awaiting_lines' | 'unavailable'

export interface GameOddsBook { book: string; a: number | null; b: number | null }
export interface GameOddsRow {
  key: string
  sport: string
  game_key: string
  away: string
  home: string
  commence: string
  market: GameMarket
  /** spread: the HOME line (home -3.5 => -3.5; the away side gets +3.5); total: the points; moneyline: null */
  line: number | null
  books: GameOddsBook[]
}
export interface GameOddsGame { game_key: string; away: string; home: string; start: string; n_rows: number }
export interface GameOddsFeed {
  sport: string
  status: GameOddsStatus
  as_of: string | null
  age_minutes: number | null
  stale: boolean
  validated: boolean
  lines_source: string
  count: number
  books: string[]
  offshore_books: string[]
  games: GameOddsGame[]
  rows: GameOddsRow[]
  /** added by the viewer-gated proxy / page: how many rows the access policy withheld */
  locked?: number
}

export const GAME_ODDS_SPORTS = ['nba', 'nhl'] as const
export const GAME_ODDS_MARKETS = ['ml', 'spread', 'total'] as const
export const GAME_ODDS_MAX_LIMIT = 5000
/** Fewest reference books behind a fair value (the Odds Terminal's MIN_REF): below it a row shows prices only. */
export const GAME_MIN_REF = 3
export const GAME_PRICES_LABEL = 'UNVALIDATED'
export const GAME_PRICES_NOTE = 'market prices, not a tested edge'

// ------------------------------------------------------------------------------------------------------ query validation

export interface GameOddsQuery { sport: (typeof GAME_ODDS_SPORTS)[number]; day?: string; market?: GameMarket; limit?: number }

function validDay(day: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return false
  const d = new Date(`${day}T00:00:00Z`)
  return !isNaN(d.getTime()) && d.toISOString().slice(0, 10) === day
}

/** Whitelist for the /api/games/odds proxy: nothing but known, well-formed values is forwarded upstream. */
export function parseGameOddsQuery(sp: URLSearchParams): { ok: true; query: GameOddsQuery } | { ok: false; error: string } {
  const sport = (sp.get('sport') ?? '').toLowerCase()
  if (!(GAME_ODDS_SPORTS as readonly string[]).includes(sport)) return { ok: false, error: 'sport must be one of nba, nhl' }
  const query: GameOddsQuery = { sport: sport as GameOddsQuery['sport'] }
  const day = sp.get('day')
  if (day != null && day !== '') {
    if (!validDay(day)) return { ok: false, error: 'day must be a real date as YYYY-MM-DD' }
    query.day = day
  }
  const market = sp.get('market')
  if (market != null && market !== '') {
    const m = market.toLowerCase()
    if (!(GAME_ODDS_MARKETS as readonly string[]).includes(m)) return { ok: false, error: 'market must be ml, spread or total' }
    query.market = m as GameMarket
  }
  const limit = sp.get('limit')
  if (limit != null && limit !== '') {
    const n = Number(limit)
    if (!/^\d{1,4}$/.test(limit) || n < 1 || n > GAME_ODDS_MAX_LIMIT) return { ok: false, error: `limit must be an integer from 1 to ${GAME_ODDS_MAX_LIMIT}` }
    query.limit = n
  }
  return { ok: true, query }
}

export function gameOddsQueryString(q: GameOddsQuery): string {
  const sp = new URLSearchParams({ sport: q.sport })
  if (q.day) sp.set('day', q.day)
  if (q.market) sp.set('market', q.market)
  if (q.limit) sp.set('limit', String(q.limit))
  return sp.toString()
}

// -------------------------------------------------------------------------------------------------- adapter to the props maths

const ADAPTED = new WeakMap<GameOddsRow, BoardRow>()

/** A game row as the terminal's BoardRow: `a` (away / over) -> over, `b` (home / under) -> under. Cached per row object. */
export function toBoardRow(row: GameOddsRow): BoardRow {
  let adapted = ADAPTED.get(row)
  if (!adapted) {
    adapted = {
      key: row.key,
      game_key: row.game_key,
      commence: row.commence,
      away: row.away,
      home: row.home,
      player: `${row.away} @ ${row.home}`,
      player_norm: row.key,
      stat: row.market,
      line: row.line ?? 0,
      books: (row.books ?? []).map(b => ({ book: b.book, over: b.a, under: b.b })),
    }
    ADAPTED.set(row, adapted)
  }
  return adapted
}

// --------------------------------------------------------------------------------------------------------------- labels

/** +3.5 / -3.5 / PK, the sign always shown. */
export function signedLine(n: number): string {
  if (n === 0) return 'PK'
  return n > 0 ? `+${n}` : String(n)
}

export const MARKET_LABEL: Record<GameMarket, string> = { ml: 'Moneyline', spread: 'Spread', total: 'Total' }

/** The line as shown beside the market: moneyline 'ML', spread the HOME line ('MIA -3.5'), total 'O/U 224.5'. */
export function lineLabel(row: Pick<GameOddsRow, 'market' | 'line' | 'home'>): string {
  if (row.market === 'ml') return 'ML'
  if (row.line == null) return row.market === 'spread' ? 'Spread' : 'Total'
  return row.market === 'spread' ? `${row.home} ${signedLine(row.line)}` : `O/U ${row.line}`
}

/** The label of each side: 'BOS ML' / 'MIA ML'; 'BOS +3.5' / 'MIA -3.5' (away gets the opposite of the home line); 'O 224.5' / 'U 224.5'. */
export function sideLabels(row: Pick<GameOddsRow, 'market' | 'line' | 'away' | 'home'>): { a: string; b: string } {
  if (row.market === 'ml') return { a: `${row.away} ML`, b: `${row.home} ML` }
  if (row.market === 'spread') {
    return row.line == null ? { a: row.away, b: row.home } : { a: `${row.away} ${signedLine(-row.line)}`, b: `${row.home} ${signedLine(row.line)}` }
  }
  return row.line == null ? { a: 'Over', b: 'Under' } : { a: `O ${row.line}`, b: `U ${row.line}` }
}

const CT_TIME = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago', weekday: 'short', hour: 'numeric', minute: '2-digit' })

/** 'Mon 6:30p' in Central; '' for an unparseable stamp. */
export function startLabel(iso: string): string {
  const d = new Date(iso)
  return isNaN(d.getTime()) ? '' : CT_TIME.format(d).replace(' AM', 'a').replace(' PM', 'p')
}

/** '12 MIN' / '3.5 H' / '2 D' for the header strip; '--' when the feed has no age. */
export function ageText(mins: number | null | undefined): string {
  if (mins == null || !isFinite(mins)) return '--'
  return mins < 90 ? `${Math.max(1, Math.round(mins))} MIN` : mins < 2880 ? `${(mins / 60).toFixed(1)} H` : `${Math.round(mins / 1440)} D`
}

// ----------------------------------------------------------------------------------------------------------- view model

export interface GamePriceOptions {
  /** reference book keys for the fair value; empty = every book with a two-way price */
  refs: readonly string[]
  method: DevigMethod
  /** books the viewer can bet at (null = no cut). Offshore books are ALWAYS removed, whatever this says. */
  bettable: ReadonlySet<string> | null
  /** feed.offshore_books; lib/offshore.ts is applied on top so an older feed can never put an offshore book on the best price */
  offshore?: readonly string[] | ReadonlySet<string>
  minRef?: number
  /** false leaves Kalshi / Novig / ProphetX out of the arbitrage search */
  includeExchanges?: boolean
  bankroll?: number
}

export interface GameSidePrice {
  side: 'A' | 'B'
  label: string
  book: string | null
  american: number | null
  /** books quoting exactly this best price (bettable only) */
  ties: string[]
  pFair: number | null
  fairAmerican: number | null
  /** reference books behind pFair (the best book itself is left out) */
  nRef: number
  ev: number | null
  /** 1/4 Kelly stake in dollars on `bankroll` (0 when there is no edge) */
  stake: number
}

export interface GamePriceRow {
  key: string
  row: GameOddsRow
  matchup: string
  marketLabel: string
  lineLabel: string
  a: GameSidePrice
  b: GameSidePrice
  /** the better of the two sides' EV (null when neither side has a fair value) */
  bestEv: number | null
  /** best A at one book + best B at a DIFFERENT bettable book, net of Kalshi's fee; null when two books do not price both sides */
  arb: ArbResult | null
  /** net margin > 0 */
  arbLive: boolean
}

function toSet(x: readonly string[] | ReadonlySet<string> | undefined): ReadonlySet<string> {
  return x instanceof Set ? x : new Set<string>(x ? Array.from(x) : [])
}

/** Books that may be a best price: quoted somewhere, inside `bettable`, never offshore. */
export function effectiveBettable(rows: GameOddsRow[], opts: Pick<GamePriceOptions, 'bettable' | 'offshore'>): Set<string> {
  const off = toSet(opts.offshore)
  const out = new Set<string>()
  for (const r of rows) {
    for (const b of r.books ?? []) {
      if (off.has(b.book) || isOffshore(b.book)) continue
      if (opts.bettable && !opts.bettable.has(b.book)) continue
      out.add(b.book)
    }
  }
  return out
}

/**
 * One view row per game x market x line. Fair value per side = equal-weight mean of each reference book's de-vigged two-way
 * probability with the best-price book left out, only when at least `minRef` (3) reference books price the line both ways;
 * EV is that side's fair probability x its best bettable price - 1. Arbitrage reuses computeRowArb (Kalshi's taker fee off
 * its leg). Different lines of the same game are separate rows: a middle between them is out of scope.
 */
export function buildGamePriceRows(rows: GameOddsRow[], opts: GamePriceOptions): GamePriceRow[] {
  const minRef = opts.minRef ?? GAME_MIN_REF
  const bankroll = opts.bankroll ?? 1000
  const bettable = effectiveBettable(rows, opts)
  const adapted = rows.map(toBoardRow)
  const screen = buildScreenRows(adapted, { refs: opts.refs, method: opts.method, bettable, minRef, side: 'both' })
  const byRow = new Map<BoardRow, { OVER?: (typeof screen)[number]; UNDER?: (typeof screen)[number] }>()
  for (const s of screen) {
    const e = byRow.get(s.row) ?? {}
    e[s.side] = s
    byRow.set(s.row, e)
  }
  const side = (which: 'A' | 'B', label: string, s: (typeof screen)[number] | undefined): GameSidePrice => {
    if (!s) return { side: which, label, book: null, american: null, ties: [], pFair: null, fairAmerican: null, nRef: 0, ev: null, stake: 0 }
    const dec = s.implied > 0 ? 1 / s.implied : 1
    const stake = s.pFair != null && s.ev != null && s.ev > 0 ? kellySizing(bankroll, s.pFair, dec).dollars : 0
    return { side: which, label, book: s.bestBook, american: s.bestAmerican, ties: s.bestTies, pFair: s.pFair, fairAmerican: s.fairAmerican, nRef: s.nRef, ev: s.ev, stake }
  }
  return rows.map((row, i) => {
    const e = byRow.get(adapted[i])
    const labels = sideLabels(row)
    const a = side('A', labels.a, e?.OVER)
    const b = side('B', labels.b, e?.UNDER)
    const evs = [a.ev, b.ev].filter((x): x is number => x != null)
    const arb = computeRowArb(adapted[i], bettable, opts.includeExchanges ?? true)
    return {
      key: row.key, row, matchup: `${row.away} @ ${row.home}`, marketLabel: MARKET_LABEL[row.market] ?? row.market, lineLabel: lineLabel(row),
      a, b, bestEv: evs.length ? Math.max(...evs) : null, arb, arbLive: !!arb && arb.netArbPct > 0,
    }
  })
}

export type GameSort = 'ev' | 'time' | 'game'

/** A live arbitrage ranks by its locked net margin so it sits with the best EV rows. */
function rank(r: GamePriceRow): number {
  const ev = r.bestEv ?? -Infinity
  return r.arbLive && r.arb ? Math.max(ev, r.arb.netArbPct / 100) : ev
}

export function sortGamePriceRows(rows: GamePriceRow[], sort: GameSort): GamePriceRow[] {
  const out = rows.slice()
  const time = (r: GamePriceRow) => r.row.commence ?? ''
  if (sort === 'time') out.sort((x, y) => time(x).localeCompare(time(y)) || x.matchup.localeCompare(y.matchup) || x.key.localeCompare(y.key))
  else if (sort === 'game') out.sort((x, y) => x.matchup.localeCompare(y.matchup) || x.key.localeCompare(y.key))
  else out.sort((x, y) => rank(y) - rank(x) || time(x).localeCompare(time(y)) || x.key.localeCompare(y.key))
  return out
}

export interface GamePriceFilters {
  market: 'all' | GameMarket
  /** game_key or 'all' */
  game: string
  q: string
  /** EV floor as a fraction; below 0 = every row, including those with no fair value */
  minEv: number
  arbOnly: boolean
}

/** A live arbitrage clears any EV floor: it beats every EV row. */
export function filterGamePriceRows(rows: GamePriceRow[], f: GamePriceFilters): GamePriceRow[] {
  const q = f.q.trim().toLowerCase()
  return rows.filter(r => {
    if (f.market !== 'all' && r.row.market !== f.market) return false
    if (f.game !== 'all' && r.row.game_key !== f.game) return false
    if (q && !`${r.row.away} ${r.row.home} ${r.matchup}`.toLowerCase().includes(q)) return false
    if (f.arbOnly) return r.arbLive
    if (r.arbLive) return true
    return r.bestEv == null ? f.minEv < 0 : r.bestEv >= f.minEv
  })
}

// ----------------------------------------------------------------------------------------------------------------- books

// Sharp-first column order; any book not listed follows by how many lines it quotes.
const BOOK_ORDER = ['pinnacle', 'novig', 'prophetx', 'kalshi', 'draftkings', 'fanduel', 'betmgm', 'caesars', 'espnbet', 'thescore', 'fanatics', 'hardrock', 'betrivers', 'bet365']
export const SHARP_BOOKS = ['pinnacle', 'novig', 'prophetx']

export interface GameBookColumn { key: string; n: number; offshore: boolean }

/** Every book with a price in `rows`, sharp books first, then by lines quoted. Skins (PartyCasino) collapse into their parent. */
export function gameBookColumns(rows: GameOddsRow[], offshore: readonly string[] | ReadonlySet<string> = []): GameBookColumn[] {
  const off = toSet(offshore)
  const counts = new Map<string, number>()
  for (const r of rows) for (const b of r.books ?? []) if (canonBook(b.book) === b.book) counts.set(b.book, (counts.get(b.book) ?? 0) + 1)
  const rankOf = (k: string) => { const i = BOOK_ORDER.indexOf(k); return i < 0 ? BOOK_ORDER.length : i }
  return [...counts.keys()]
    .sort((x, y) => rankOf(x) - rankOf(y) || (counts.get(y)! - counts.get(x)!) || x.localeCompare(y))
    .map(k => ({ key: k, n: counts.get(k)!, offshore: off.has(k) || isOffshore(k) }))
}

// ------------------------------------------------------------------------------------------------------------- hedging

/** One line a bettor can paste: both legs, their books, the split of `outlay` and the locked profit. null without a live arb. */
export function gameArbHedgeText(r: GamePriceRow, outlay: number): string | null {
  const a = r.arb
  if (!a) return null
  const k = outlay / 100
  const usd = (x: number) => `$${x.toFixed(2)}`
  return `${r.matchup} ${r.marketLabel} ${r.lineLabel}: ${r.a.label} ${formatOdds(a.bestOverAm)} ${bookDisplay(a.bestOverBook).label} ${usd(a.stakeOverPer100 * k)}`
    + ` / ${r.b.label} ${formatOdds(a.bestUnderAm)} ${bookDisplay(a.bestUnderBook).label} ${usd(a.stakeUnderPer100 * k)}`
    + ` = ${usd(a.guaranteedProfitPer100 * k)} locked on ${usd(outlay)}`
}

// ---------------------------------------------------------------------------------------------------------- feed helpers

/** Rows of a feed that are well formed (a book list to price); a broken row never reaches the maths. */
export function feedRows(feed: GameOddsFeed | null | undefined): GameOddsRow[] {
  if (!feed || !Array.isArray(feed.rows)) return []
  return feed.rows.filter(r => r && Array.isArray(r.books) && (r.market === 'ml' || r.market === 'spread' || r.market === 'total'))
}

/** true when no feed has anything published (all missing or `unavailable`). */
export function gameOddsUnavailable(feeds: (GameOddsFeed | null | undefined)[]): boolean {
  return feeds.every(f => !f || f.status === 'unavailable')
}
