/**
 * Odds screen math (/ev/odds). The backend ships every book's raw American over / under price per prop line and NO fair
 * value (ev/odds_board.py); the viewer picks the reference books and the de-vig method, and everything below is computed
 * from that choice in the browser.
 *
 * Fair value = the EQUAL-WEIGHT mean of each reference book's own two-sided de-vigged P(over) (the +EV terminal's backend
 * consensus uses the median; the mean is what lets a viewer weight a chosen set of sharp books). A book never anchors its
 * own price: when the best price sits at a reference book, that book is left out of the fair value (leave-self-out, the
 * same rule as ev.props.scan_book_outliers).
 *
 * Two more views read the same rows (v1.1): ARBITRAGE (`computeRowArb`: the best over and the best under at two different
 * books, Kalshi's taker fee taken off its leg) and ALT & LONGSHOTS (`computeLongshotMetrics`: the lottery-ticket over and
 * the fade-it under, priced against the reference books or, for a line too thinly quoted to de-vig, the player's curve).
 */
import { americanToDecimal } from '@/lib/ev-math'
import { americanToImpliedProb } from '@/lib/odds'

export type DevigMethod = 'proportional' | 'power'
export type SideFilter = 'both' | 'over' | 'under'
export type OddsScreenMode = 'ev' | 'arb' | 'longshots'
export type LongshotSubfilter = 'all' | 'plus_ev_over' | 'fade_under'

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
  /** headshot image id resolved server-side by name (lib/odds-headshots.ts); the feed's player_id is not an image id */
  headshot_id?: number | string | null
  team?: string | null
  stat: string
  line: number
  books: BoardBook[]
  /** off the player's modal line by >= 1, or quoted only through alternate / one-sided ladder markets (ev/odds_board.py) */
  is_alt?: boolean
  /** the line most books price two-sided for this player and stat */
  modal_point?: number | null
  /** fair P(over) from the player's own multi-rung curve at this line; a fallback for lines too thin to de-vig */
  curve_fair_over?: number | null
  /** rungs behind the curve */
  curve_n?: number
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

export function fairAmericanOf(p: number): number {
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
  home_runs: 'Home Runs', strikeouts: 'Strikeouts', outs: 'Pitcher Outs', total_bases: 'Total Bases', earned_runs: 'Earned Runs',
  stolen_bases: 'Stolen Bases',
}

// ---------------------------------------------------------------------------------------------------------- ARBITRAGE

/** Exchanges whose quotes the Arbitrage view can leave out. Only Kalshi's fee is modelled (Novig charges no pre-game taker
 * fee); another exchange's commission is not, so its price is taken as quoted. */
export const EXCHANGE_BOOKS: ReadonlySet<string> = new Set(['kalshi', 'novig', 'prophetx'])
export const KALSHI_FEE_RATE = 0.07

/** Decimal payout of a Kalshi contract bought at `dec` once its taker fee (0.07 * p * (1 - p) per contract) is paid. */
export function kalshiEffectiveDecimal(dec: number): number {
  const p = 1 / dec
  return 1 / (p + KALSHI_FEE_RATE * p * (1 - p))
}

export interface ArbResult {
  /** true when the NET margin is positive: both legs together pay more than the outlay whichever side wins */
  hasArb: boolean
  /** Gross margin on the outlay, in percent: (1 / (1/decOver + 1/decUnder) - 1) * 100. Positive = risk-free profit. */
  arbPct: number
  /** The same after Kalshi's taker fee on a Kalshi leg (equals arbPct when neither leg is Kalshi). */
  netArbPct: number
  bestOverBook: string
  bestOverAm: number
  bestOverDec: number
  bestUnderBook: string
  bestUnderAm: number
  bestUnderDec: number
  /** true when a leg is at Kalshi, so the net figures differ from the gross ones */
  feeLeg: boolean
  /** Stake split of a $100 outlay that pays the same on either outcome (net of the Kalshi fee). */
  stakeOverPer100: number
  stakeUnderPer100: number
  /** Locked profit on that $100, net of the fee. */
  guaranteedProfitPer100: number
}

/**
 * The best two-way arbitrage on one line: the best over and the best under at two DIFFERENT books (when one book holds
 * both, the better of its runner-up pairings). `visible` = books the viewer can bet at (null = all); `includeExchanges` =
 * false leaves Kalshi / Novig / ProphetX out. Kalshi's taker fee is taken off its leg before the legs are compared, so the
 * pair chosen, the stake split and the locked profit are all net. null when no two books price opposite sides.
 *
 * Runs once per row on every filter change over thousands of rows, so it keeps the two best legs per side in locals
 * (effective decimal, book index) instead of allocating leg objects.
 */
export function computeRowArb(row: BoardRow, visible: ReadonlySet<string> | null, includeExchanges = true): ArbResult | null {
  const books = row.books
  // per side: best and runner-up effective decimal (fee-adjusted) and the index of the book behind each
  let oE1 = 0, oE2 = 0, oI1 = -1, oI2 = -1
  let uE1 = 0, uE2 = 0, uI1 = -1, uI2 = -1
  for (let i = 0; i < books.length; i++) {
    const b = books[i]
    if (visible && !visible.has(b.book)) continue
    if (!includeExchanges && EXCHANGE_BOOKS.has(b.book)) continue
    const kalshi = b.book === 'kalshi'
    const o = b.over
    if (o != null && isFinite(o) && o !== 0) {
      const dec = americanToDecimal(o)
      const e = kalshi ? kalshiEffectiveDecimal(dec) : dec
      if (e > oE1) { oE2 = oE1; oI2 = oI1; oE1 = e; oI1 = i } else if (e > oE2) { oE2 = e; oI2 = i }
    }
    const u = b.under
    if (u != null && isFinite(u) && u !== 0) {
      const dec = americanToDecimal(u)
      const e = kalshi ? kalshiEffectiveDecimal(dec) : dec
      if (e > uE1) { uE2 = uE1; uI2 = uI1; uE1 = e; uI1 = i } else if (e > uE2) { uE2 = e; uI2 = i }
    }
  }
  if (oI1 < 0 || uI1 < 0) return null
  // the best over / under pairing across two different books (top two per side covers every candidate)
  let oi = -1, ui = -1, oe = 0, inv = Infinity
  const tryPair = (i: number, ie: number, j: number, je: number) => {
    if (i < 0 || j < 0 || i === j) return
    const v = 1 / ie + 1 / je
    if (v < inv) { inv = v; oi = i; ui = j; oe = ie }
  }
  tryPair(oI1, oE1, uI1, uE1)
  tryPair(oI1, oE1, uI2, uE2)
  tryPair(oI2, oE2, uI1, uE1)
  tryPair(oI2, oE2, uI2, uE2)
  if (oi < 0) return null
  const ob = books[oi]
  const ub = books[ui]
  const decO = americanToDecimal(ob.over as number)
  const decU = americanToDecimal(ub.under as number)
  const grossInv = 1 / decO + 1 / decU
  const netArbPct = (1 / inv - 1) * 100
  const stakeOver = (100 * (1 / oe)) / inv
  return {
    hasArb: netArbPct > 0,
    arbPct: (1 / grossInv - 1) * 100,
    netArbPct,
    bestOverBook: ob.book, bestOverAm: ob.over as number, bestOverDec: decO,
    bestUnderBook: ub.book, bestUnderAm: ub.under as number, bestUnderDec: decU,
    feeLeg: ob.book === 'kalshi' || ub.book === 'kalshi',
    stakeOverPer100: stakeOver,
    stakeUnderPer100: 100 - stakeOver,
    guaranteedProfitPer100: stakeOver * oe - 100,
  }
}

export interface ArbRow { key: string; row: BoardRow; arb: ArbResult }

/** Every line whose net margin is above `minNetArbPct` (percent; 0 = live arbs, -1.5 = near arbs). */
export function buildArbRows(rows: BoardRow[], opts: { bettable: ReadonlySet<string> | null; includeExchanges: boolean; minNetArbPct: number }): ArbRow[] {
  const out: ArbRow[] = []
  for (const row of rows) {
    const arb = computeRowArb(row, opts.bettable, opts.includeExchanges)
    if (arb && arb.netArbPct > opts.minNetArbPct) out.push({ key: row.key, row, arb })
  }
  return out
}

// ------------------------------------------------------------------------------------------------ ALT & LONGSHOTS

/** A row is a longshot row when the lottery-ticket over pays at least this (American) or its fair P(over) is this low. */
export const LONGSHOT_MIN_AMERICAN = 200
export const LONGSHOT_MAX_FAIR = 0.333

export interface LongshotMetrics {
  isLongshotRow: boolean
  isAltLine: boolean
  /** Best OVER price at a bettable book: the lottery-ticket side. */
  bestOverBook: string | null
  bestOverAm: number | null
  /** Best UNDER / NO price at a bettable book: the fade (house) side. */
  bestUnderBook: string | null
  bestUnderAm: number | null
  /** Fair P(over): the reference books' de-vigged mean when >= minRef of them price this exact line both ways, else the curve. */
  fairOverProb: number | null
  /** The same for the under side, with ITS best book left out of the average (equals 1 - fairOverProb unless the two best prices sit at different reference books). */
  fairUnderProb: number | null
  fairSource: 'consensus' | 'curve' | null
  /** reference books behind the consensus fair value (0 when the curve is used) */
  nRef: number
  /** implied probability of each best price (1 / decimal) */
  overImplied: number | null
  underImplied: number | null
  /** EV% of taking the longshot OVER at bestOverAm. */
  overEvPct: number | null
  /** EV% of fading it: buying the UNDER / NO at bestUnderAm. */
  fadeUnderEvPct: number | null
}

export interface LongshotOptions {
  refs: readonly string[]
  method: DevigMethod
  bettable: ReadonlySet<string> | null
  /** fewest reference books for a consensus fair value; below it the curve (when allowed) stands in */
  minRef: number
  useCurve: boolean
}

interface FairPick { p: number; source: 'consensus' | 'curve'; n: number }

interface Best { i: number; book: string; am: number; dec: number }

/** The best bettable price on a side (higher decimal wins) with the index of its book; no tie list, unlike bestPrice. */
function bestOnSide(books: BoardBook[], side: 'OVER' | 'UNDER', bettable: ReadonlySet<string> | null): Best | null {
  let best: Best | null = null
  for (let i = 0; i < books.length; i++) {
    const b = books[i]
    if (bettable && !bettable.has(b.book)) continue
    const a = side === 'OVER' ? b.over : b.under
    if (a == null || !isFinite(a) || a === 0) continue
    const dec = americanToDecimal(a)
    if (!best || dec > best.dec) best = { i, book: b.book, am: a, dec }
  }
  return best
}

export function computeLongshotMetrics(row: BoardRow, opts: LongshotOptions, refSet?: ReadonlySet<string> | null): LongshotMetrics {
  const refs = refSet !== undefined ? refSet : opts.refs.length ? new Set(opts.refs) : null
  const books = row.books
  const fairs = rowFairs(row, opts.method)
  let sum = 0
  let n = 0
  for (let i = 0; i < books.length; i++) {
    const p = fairs[i]
    if (p == null || (refs && !refs.has(books[i].book))) continue
    sum += p
    n += 1
  }
  const curve = opts.useCurve && row.curve_fair_over != null && row.curve_fair_over > 0 && row.curve_fair_over < 1 ? row.curve_fair_over : null
  // fair P(over) with the book whose price is being judged (index `own`) left out; the curve when the books are too few
  const fairFor = (own: number): FairPick | null => {
    let s = sum
    let k = n
    const mine = fairs[own]
    if (mine != null && (!refs || refs.has(books[own].book))) { s -= mine; k -= 1 }
    if (k >= 1 && k >= opts.minRef) {
      const p = s / k
      if (p > 0 && p < 1) return { p, source: 'consensus', n: k }
    }
    return curve != null ? { p: curve, source: 'curve', n: 0 } : null
  }
  const bo = bestOnSide(books, 'OVER', opts.bettable)
  const bu = bestOnSide(books, 'UNDER', opts.bettable)
  const overFair = bo ? fairFor(bo.i) : null
  const underFair = bu ? fairFor(bu.i) : null
  const head = overFair ?? underFair
  const fairOverProb = head ? head.p : null
  const isAltLine = row.is_alt === true
  return {
    isLongshotRow: isAltLine || (bo != null && bo.am >= LONGSHOT_MIN_AMERICAN) || (fairOverProb != null && fairOverProb <= LONGSHOT_MAX_FAIR),
    isAltLine,
    bestOverBook: bo ? bo.book : null,
    bestOverAm: bo ? bo.am : null,
    bestUnderBook: bu ? bu.book : null,
    bestUnderAm: bu ? bu.am : null,
    fairOverProb,
    fairUnderProb: underFair ? 1 - underFair.p : overFair ? 1 - overFair.p : null,
    fairSource: head ? head.source : null,
    nRef: head ? head.n : 0,
    overImplied: bo ? 1 / bo.dec : null,
    underImplied: bu ? 1 / bu.dec : null,
    overEvPct: overFair && bo ? (overFair.p * bo.dec - 1) * 100 : null,
    fadeUnderEvPct: underFair && bu ? ((1 - underFair.p) * bu.dec - 1) * 100 : null,
  }
}

export interface LongshotRow {
  key: string
  row: BoardRow
  m: LongshotMetrics
  /** the side with the higher EV (null when neither side has a fair value) */
  side: 'OVER' | 'UNDER' | null
  /** that side's EV in percent */
  bestEv: number | null
}

export interface LongshotScreenOptions extends LongshotOptions {
  sub: LongshotSubfilter
  /** fraction (0.02 = 2%): the Snipe / Fade sub-filters keep EV at or above it */
  minEv: number
  /** fewest American odds of the longshot over (0 = no cut) */
  minOverAm: number
}

export function buildLongshotRows(rows: BoardRow[], opts: LongshotScreenOptions): LongshotRow[] {
  const refSet = opts.refs.length ? new Set(opts.refs) : null
  const floor = opts.minEv * 100
  const out: LongshotRow[] = []
  for (const row of rows) {
    const m = computeLongshotMetrics(row, opts, refSet)
    if (!m.isLongshotRow) continue
    if (opts.minOverAm > 0 && !(m.bestOverAm != null && m.bestOverAm >= opts.minOverAm)) continue
    const o = m.overEvPct
    const f = m.fadeUnderEvPct
    if (opts.sub === 'plus_ev_over' && !(o != null && o >= floor)) continue
    if (opts.sub === 'fade_under' && !(f != null && f >= floor)) continue
    // a Snipe / Fade view shows its own side; All shows whichever side pays more
    const side: LongshotRow['side'] = opts.sub === 'plus_ev_over' ? 'OVER' : opts.sub === 'fade_under' ? 'UNDER'
      : o == null && f == null ? null : f == null || (o != null && o >= f) ? 'OVER' : 'UNDER'
    out.push({ key: row.key, row, m, side, bestEv: side === 'OVER' ? o : side === 'UNDER' ? f : null })
  }
  return out
}

// ---------------------------------------------------------------------------------------------------------- MIDDLES

/**
 * A middle: the OVER of a lower line and the UNDER of a higher line on the same player and stat. If the stat lands strictly
 * between the two lines both bets win; anywhere else exactly one wins, and with near-even prices the hedge costs only the
 * combined hold. It is NOT risk-free (the miss costs `missPct`), it is a cheap lottery ticket on the window.
 */
export interface MiddleResult {
  key: string
  low: BoardRow
  high: BoardRow
  overBook: string
  overAm: number
  underBook: string
  underAm: number
  /** high line minus low line: the width of the window in points */
  gap: number
  /** profit on the outlay, percent, when the stat lands OUTSIDE the window (one leg wins). Negative = the cost of the hedge. */
  missPct: number
  /** profit on the outlay, percent, when it lands INSIDE the window (both legs win) */
  hitPct: number
  /** chance the window must hit for the middle to break even: inverse-sum minus one (0 when the two legs already arb) */
  breakevenProb: number
  /** stake split of $100 that pays the same on either single win (net of the Kalshi fee) */
  stakeOverPer100: number
  stakeUnderPer100: number
  /** fair P(window) = fair P(over low) - fair P(over high) from the reference books (curve when allowed); null without both */
  midProb: number | null
  /** EV on the outlay at midProb, percent */
  midEvPct: number | null
  feeLeg: boolean
}

export interface MiddleOptions {
  bettable: ReadonlySet<string> | null
  includeExchanges: boolean
  /** keep middles whose miss costs at most this many percent of the outlay (5 = a -5% miss) */
  maxCostPct: number
  /** narrowest window kept, in points */
  minGap: number
  refs: readonly string[]
  method: DevigMethod
  minRef: number
  useCurve: boolean
}

interface EffPrice { i: number; book: string; am: number; dec: number; eff: number }

function bestEff(row: BoardRow, side: 'OVER' | 'UNDER', visible: ReadonlySet<string> | null, includeExchanges: boolean): EffPrice | null {
  let best: EffPrice | null = null
  const books = row.books
  for (let i = 0; i < books.length; i++) {
    const b = books[i]
    if (visible && !visible.has(b.book)) continue
    if (!includeExchanges && EXCHANGE_BOOKS.has(b.book)) continue
    const am = side === 'OVER' ? b.over : b.under
    if (am == null || !isFinite(am) || am === 0) continue
    const dec = americanToDecimal(am)
    const eff = b.book === 'kalshi' ? kalshiEffectiveDecimal(dec) : dec
    if (!best || eff > best.eff) best = { i, book: b.book, am, dec, eff }
  }
  return best
}

/** Fair P(over) of one line from the reference books with `exclude` (the leg's own book) left out; the curve when too few. */
function fairOverAt(row: BoardRow, method: DevigMethod, refs: ReadonlySet<string> | null, exclude: number, minRef: number, useCurve: boolean): number | null {
  const fairs = rowFairs(row, method)
  let sum = 0
  let n = 0
  for (let i = 0; i < row.books.length; i++) {
    const p = fairs[i]
    if (p == null || i === exclude || (refs && !refs.has(row.books[i].book))) continue
    sum += p
    n += 1
  }
  if (n >= 1 && n >= minRef) { const p = sum / n; if (p > 0 && p < 1) return p }
  const c = row.curve_fair_over
  return useCurve && c != null && c > 0 && c < 1 ? c : null
}

/**
 * Every middle on the board: for each player and stat, each pair of lines (low < high) priced as the best over on the low line
 * and the best under on the high line. Kept when the window is at least `minGap` wide and a miss costs at most `maxCostPct`.
 * Sorted by estimated EV where the reference books (or curve) price both lines, else by the lowest break-even chance.
 */
export function computeMiddles(rows: BoardRow[], opts: MiddleOptions): MiddleResult[] {
  const groups = new Map<string, BoardRow[]>()
  for (const r of rows) {
    const k = `${r.game_key}|${r.player_norm}|${r.stat}`
    const g = groups.get(k)
    if (g) g.push(r)
    else groups.set(k, [r])
  }
  const refs = opts.refs.length ? new Set(opts.refs) : null
  const out: MiddleResult[] = []
  for (const g of groups.values()) {
    if (g.length < 2) continue
    g.sort((a, b) => a.line - b.line)
    const overs = g.map(r => bestEff(r, 'OVER', opts.bettable, opts.includeExchanges))
    const unders = g.map(r => bestEff(r, 'UNDER', opts.bettable, opts.includeExchanges))
    for (let a = 0; a < g.length; a++) {
      const o = overs[a]
      if (!o) continue
      for (let b = a + 1; b < g.length; b++) {
        const u = unders[b]
        const gap = g[b].line - g[a].line
        if (!u || gap < opts.minGap) continue
        const inv = 1 / o.eff + 1 / u.eff
        const missPct = (1 / inv - 1) * 100
        if (missPct < -opts.maxCostPct) continue
        const stakeOver = (100 * (1 / o.eff)) / inv
        const pLow = fairOverAt(g[a], opts.method, refs, o.i, opts.minRef, opts.useCurve)
        const pHigh = fairOverAt(g[b], opts.method, refs, u.i, opts.minRef, opts.useCurve)
        const midProb = pLow != null && pHigh != null && pLow > pHigh ? pLow - pHigh : null
        out.push({
          key: `${g[a].key}>${g[b].line}`, low: g[a], high: g[b],
          overBook: o.book, overAm: o.am, underBook: u.book, underAm: u.am,
          gap, missPct, hitPct: (2 / inv - 1) * 100, breakevenProb: Math.max(inv - 1, 0),
          stakeOverPer100: stakeOver, stakeUnderPer100: 100 - stakeOver,
          midProb, midEvPct: midProb == null ? null : ((1 + midProb) / inv - 1) * 100,
          feeLeg: o.book === 'kalshi' || u.book === 'kalshi',
        })
      }
    }
  }
  return out.sort((x, y) => (y.midEvPct ?? -Infinity) - (x.midEvPct ?? -Infinity) || x.breakevenProb - y.breakevenProb || y.gap - x.gap)
}
