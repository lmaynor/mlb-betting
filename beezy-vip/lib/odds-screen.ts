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
import { isOffshore } from '@/lib/offshore'

export type DevigMethod = 'proportional' | 'power'
export type SideFilter = 'both' | 'over' | 'under'
export type OddsScreenMode = 'ev' | 'outliers' | 'arb' | 'middles' | 'longshots' | 'sgp'
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

// -------------------------------------------------------------------------------------------------- MARKET OUTLIERS

export interface OutlierPeerQuote {
  book: string
  american: number
  decimal: number
  impliedProb: number
  /** 0..1 bar width ratio relative to the outlier book's payout/edge */
  barRatio: number
}

export interface OutlierRow {
  key: string
  row: BoardRow
  side: 'OVER' | 'UNDER'
  label: string
  outlierBook: string
  outlierAmerican: number
  outlierDecimal: number
  outlierImplied: number
  /** Leave-self-out peer quotes on the SAME side, sorted best price first */
  peers: OutlierPeerQuote[]
  nPeers: number
  /** Average implied probability across peers -> converted to American odds ("Market avg / N books") */
  marketAvgAmerican: number
  marketAvgDecimal: number
  /** Payout premium vs peer average decimal: (outlierDecimal / marketAvgDecimal - 1) * 100 */
  payoutPremiumPct: number
  /** True de-vigged EV% when >= minRef two-sided reference books (or player curve) exist, else null */
  trueEvPct: number | null
  fairAmerican: number | null
  fairSource: 'consensus' | 'curve' | null
}

export interface OutlierOptions {
  bettable: ReadonlySet<string> | null
  refs: readonly string[]
  method: DevigMethod
  /** Minimum peer books required on the same side to declare an outlier (default 3) */
  minPeers?: number
  /** Minimum payout premium % over the peer average decimal (default 8, i.e. +8% above market avg) */
  minPremiumPct?: number
  /** Optional minimum true de-vigged EV% (default 0; when > 0 requires trueEvPct >= minEvPct) */
  minEvPct?: number
  side?: SideFilter
  /** Odds filter: 'all' | 'main' (<= +200) | 'plus_money' (+100+) | 'longshots' (+250+) */
  oddsBand?: 'all' | 'main' | 'plus_money' | 'longshots'
  useCurve?: boolean
  minRef?: number
}

/**
 * Identify single-book price dislocations where one bettable book is hanging a number significantly above the
 * rest of the market on the same side. Leave-self-out math: compares the outlier book's payout to the average
 * of the remaining peer books, requiring at least minPeers peer quotes and a >= 3% gap over the runner-up book.
 */
export function computeOutliers(rows: BoardRow[], opts: OutlierOptions): OutlierRow[] {
  const minPeers = opts.minPeers ?? 3
  const minPremiumPct = opts.minPremiumPct ?? 8
  const minEvPct = opts.minEvPct ?? 0
  const sideFilter = opts.side ?? 'both'
  const oddsBand = opts.oddsBand ?? 'all'
  const useCurve = opts.useCurve ?? true
  const minRef = opts.minRef ?? 2
  const refs = opts.refs && opts.refs.length ? new Set(opts.refs) : null
  const sides: ('OVER' | 'UNDER')[] = sideFilter === 'over' ? ['OVER'] : sideFilter === 'under' ? ['UNDER'] : ['OVER', 'UNDER']
  const out: OutlierRow[] = []

  for (const row of rows) {
    const isWholeNumberLine = Math.abs(row.line % 1) !== 0.5
    const fairs = rowFairs(row, opts.method)

    for (const side of sides) {
      let bestBook: string | null = null
      let bestAmerican = 0
      let bestDecimal = 0

      for (let i = 0; i < row.books.length; i++) {
        const b = row.books[i]
        if (isOffshore(b.book)) continue
        if (opts.bettable && !opts.bettable.has(b.book)) continue
        const am = side === 'OVER' ? b.over : b.under
        if (am == null || !isFinite(am) || am === 0) continue
        if ((b.over == null || b.under == null) && isWholeNumberLine) continue

        const dec = americanToDecimal(am)
        if (dec > bestDecimal) {
          bestDecimal = dec
          bestAmerican = am
          bestBook = b.book
        }
      }

      if (!bestBook) continue

      if (oddsBand === 'main' && bestAmerican > 200) continue
      if (oddsBand === 'plus_money' && bestAmerican < 100) continue
      if (oddsBand === 'longshots' && bestAmerican < 250) continue

      const peerList: { book: string; american: number; decimal: number; impliedProb: number }[] = []
      for (const b of row.books) {
        if (b.book === bestBook) continue
        const am = side === 'OVER' ? b.over : b.under
        if (am == null || !isFinite(am) || am === 0) continue
        const dec = americanToDecimal(am)
        peerList.push({ book: b.book, american: am, decimal: dec, impliedProb: 1 / dec })
      }

      if (peerList.length < minPeers) continue

      peerList.sort((a, b) => b.decimal - a.decimal)

      if (bestDecimal < peerList[0].decimal * 1.03) continue

      const sumImplied = peerList.reduce((acc, p) => acc + p.impliedProb, 0)
      const meanPeerImplied = sumImplied / peerList.length
      const marketAvgDecimal = 1 / meanPeerImplied
      const marketAvgAmerican = fairAmericanOf(meanPeerImplied)
      const payoutPremiumPct = ((bestDecimal / marketAvgDecimal) - 1) * 100

      if (payoutPremiumPct < minPremiumPct) continue

      let sumFair = 0
      let nRef = 0
      for (let i = 0; i < row.books.length; i++) {
        const b = row.books[i]
        if (b.book === bestBook) continue
        if (refs && !refs.has(b.book)) continue
        const p = fairs[i]
        if (p == null) continue
        sumFair += p
        nRef += 1
      }

      let pOver: number | null = null
      let fairSource: 'consensus' | 'curve' | null = null
      if (nRef >= minRef) {
        pOver = sumFair / nRef
        fairSource = 'consensus'
      } else if (useCurve && row.curve_fair_over != null && row.curve_fair_over > 0 && row.curve_fair_over < 1) {
        pOver = row.curve_fair_over
        fairSource = 'curve'
      }

      let trueEvPct: number | null = null
      let fairAmerican: number | null = null
      if (pOver != null) {
        const pFair = side === 'OVER' ? pOver : 1 - pOver
        trueEvPct = (pFair * bestDecimal - 1) * 100
        fairAmerican = fairAmericanOf(pFair)
      }

      if (minEvPct > 0 && (trueEvPct == null || trueEvPct < minEvPct)) continue

      const outlierNet = Math.max(0.01, bestDecimal - 1)
      const peers: OutlierPeerQuote[] = peerList.map(p => {
        const pNet = Math.max(0, p.decimal - 1)
        const ratio = Math.max(0.12, Math.min(1.0, pNet / outlierNet))
        return {
          book: p.book,
          american: p.american,
          decimal: p.decimal,
          impliedProb: p.impliedProb,
          barRatio: Math.round(ratio * 1000) / 1000,
        }
      })

      const label = `${side === 'OVER' ? 'o' : 'u'}${row.line}`
      out.push({
        key: `${row.key}|${side}`,
        row,
        side,
        label,
        outlierBook: bestBook,
        outlierAmerican: bestAmerican,
        outlierDecimal: bestDecimal,
        outlierImplied: 1 / bestDecimal,
        peers,
        nPeers: peers.length,
        marketAvgAmerican,
        marketAvgDecimal,
        payoutPremiumPct,
        trueEvPct,
        fairAmerican,
        fairSource,
      })
    }
  }

  return out.sort((a, b) => {
    const scoreA = a.trueEvPct ?? a.payoutPremiumPct
    const scoreB = b.trueEvPct ?? b.payoutPremiumPct
    return scoreB - scoreA || b.payoutPremiumPct - a.payoutPremiumPct
  })
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
  /** keep only middles whose estimated EV (from the fair price of each line) is positive */
  onlyPositiveEv?: boolean
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
    // A one-sided quote at a whole-number line is a milestone ladder rung ("N+"), whose point is not an over/under line (a
    // "0" rung at +800 is not P(over 0)): only half-point lines, or a book's own two-sided market, make a middle leg.
    if ((b.over == null || b.under == null) && Math.abs(row.line % 1) !== 0.5) continue
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
    const lowFair: (number | null | undefined)[] = new Array(g.length)
    const highFair: (number | null | undefined)[] = new Array(g.length)
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
        // a line's fair value depends only on its own best leg's book, so it is computed once per line, not once per pair
        if (lowFair[a] === undefined) lowFair[a] = fairOverAt(g[a], opts.method, refs, o.i, opts.minRef, opts.useCurve)
        if (highFair[b] === undefined) highFair[b] = fairOverAt(g[b], opts.method, refs, u.i, opts.minRef, opts.useCurve)
        const pLow = lowFair[a]
        const pHigh = highFair[b]
        const midProb = pLow != null && pHigh != null && pLow > pHigh ? pLow - pHigh : null
        if (opts.onlyPositiveEv && !(midProb != null && (1 + midProb) / inv - 1 > 0)) continue
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

// -----------------------------------------------------------------------------
// SGP Proxies & Synthetic Combo Engine
// -----------------------------------------------------------------------------

export interface SgpLeg {
  stat: string
  label: string
  line: number
  side: 'OVER' | 'UNDER'
  bookAm: number
  fairProb: number
  sid?: string
  marketId?: string
  selectionId?: string
}

export interface SgpProxyRow {
  key: string
  archetype: string
  archetypeLabel: string
  sport: string
  player: string
  player_norm: string
  player_id?: number | string | null
  headshot_id?: number | string | null
  team?: string | null
  game_key: string
  commence: string
  away: string
  home: string
  book: string
  legs: SgpLeg[]
  naiveSingleProductAm: number
  estSgpDec: number
  estSgpAm: number
  fairJointProb: number
  fairJointAm: number
  rhoTrue: number
  rhoBook: number
  holdBook: number
  evPct: number
  directMarketLabel?: string | null
  directMarketAm?: number | null
  directMarketBook?: string | null
  vsDirectPct?: number | null
  deeplink?: string | null
  runnerUpBooks?: { book: string; evPct: number; estSgpAm: number }[]
}

export interface SgpProxyOptions {
  refs: readonly string[]
  method: DevigMethod
  bettable: ReadonlySet<string> | null
  minEvPct: number
  archetype?: string
  book?: string
  useCurve: boolean
}

/** Standard normal cumulative distribution function (Abramowitz & Stegun 7.1.26, max error 1.5e-7). */
export function normalCdf(x: number): number {
  if (x === 0) return 0.5
  const b1 = 0.319381530
  const b2 = -0.356563782
  const b3 = 1.781477937
  const b4 = -1.821255978
  const b5 = 1.330274429
  const p = 0.2316419
  const absX = Math.abs(x)
  const t = 1.0 / (1.0 + p * absX)
  const phi = (1.0 / Math.sqrt(2 * Math.PI)) * Math.exp(-0.5 * absX * absX)
  const poly = ((((b5 * t + b4) * t + b3) * t + b2) * t + b1) * t
  const cdf = 1.0 - phi * poly
  return x > 0 ? cdf : 1.0 - cdf
}

/** Inverse standard normal CDF (Acklam rational approximation, max error 1.15e-9). */
export function normalInvCdf(p: number): number {
  if (p <= 0) return -8.0
  if (p >= 1) return 8.0
  if (p === 0.5) return 0.0

  const a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
  const b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
  const c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
  const d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]

  const pLow = 0.02425
  const pHigh = 1.0 - pLow

  if (p < pLow) {
    const q = Math.sqrt(-2 * Math.log(p))
    return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
           ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
  }
  if (p > pHigh) {
    const q = Math.sqrt(-2 * Math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
  }
  const q = p - 0.5
  const r = q * q
  return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q /
         (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
}

const GL_X = [0.1488743389816312, 0.4333953941292472, 0.6794095682990244, 0.8650633666889845, 0.9739065285171717]
const GL_W = [0.2955242247147529, 0.2692667193099963, 0.2190863625159820, 0.1494513491505806, 0.0666713443086881]

export function bivariateNormalCdf(z1: number, z2: number, rho: number): number {
  const p1 = normalCdf(z1)
  const p2 = normalCdf(z2)
  if (Math.abs(rho) < 1e-9) return p1 * p2

  const rClamped = Math.max(-0.999, Math.min(0.999, rho))
  let integral = 0
  for (let i = 0; i < 5; i++) {
    for (const sign of [-1, 1]) {
      const xi = sign * GL_X[i]
      const r = 0.5 * rClamped * (xi + 1.0)
      const w = 0.5 * rClamped * GL_W[i]
      const denom = Math.max(1e-12, 1.0 - r * r)
      const exponent = -(z1 * z1 - 2.0 * r * z1 * z2 + z2 * z2) / (2.0 * denom)
      const phi2 = (1.0 / (2.0 * Math.PI * Math.sqrt(denom))) * Math.exp(exponent)
      integral += w * phi2
    }
  }
  const val = p1 * p2 + integral
  const minBound = Math.max(0, p1 + p2 - 1)
  const maxBound = Math.min(p1, p2)
  return Math.min(maxBound, Math.max(minBound, val))
}

export function copulaJoint2D(p1: number, p2: number, rho: number): number {
  if (Math.abs(rho) < 1e-9) return p1 * p2
  const z1 = normalInvCdf(p1)
  const z2 = normalInvCdf(p2)
  const val = bivariateNormalCdf(z1, z2, rho)
  if (rho > 0 && val <= p1 * p2) {
    return Math.min(Math.min(p1, p2), p1 * p2 + 1e-7)
  }
  return val
}

const SPORT_LEAGUE_URLS_TS: Record<string, Record<string, string>> = {
  draftkings: {
    nba: 'https://sportsbook.draftkings.com/leagues/basketball/nba',
    nfl: 'https://sportsbook.draftkings.com/leagues/football/nfl',
    nhl: 'https://sportsbook.draftkings.com/leagues/hockey/nhl',
    mlb: 'https://sportsbook.draftkings.com/leagues/baseball/mlb',
  },
  fanduel: {
    nba: 'https://sportsbook.fanduel.com/navigation/nba',
    nfl: 'https://sportsbook.fanduel.com/navigation/nfl',
    nhl: 'https://sportsbook.fanduel.com/navigation/nhl',
    mlb: 'https://sportsbook.fanduel.com/navigation/mlb',
  },
  betmgm: {
    nba: 'https://sports.betmgm.com/en/sports/basketball-7/betting/usa-9/nba-6004',
    nfl: 'https://sports.betmgm.com/en/sports/football-11/betting/usa-9/nfl-35',
    nhl: 'https://sports.betmgm.com/en/sports/hockey-12/betting/usa-9/nhl-43',
    mlb: 'https://sports.betmgm.com/en/sports/baseball-23/betting/usa-9/mlb-75',
  },
  caesars: {
    nba: 'https://sportsbook.caesars.com/us/il/bet/basketball/events/all',
    nfl: 'https://sportsbook.caesars.com/us/il/bet/americanfootball/events/all',
    nhl: 'https://sportsbook.caesars.com/us/il/bet/icehockey/events/all',
    mlb: 'https://sportsbook.caesars.com/us/il/bet/baseball/events/all',
  },
}

export function buildSgpDeeplinkTs(
  book: string,
  gameKey: string,
  legs: { sid?: string; over_sid?: string; marketId?: string; selectionId?: string; link?: string; over_link?: string; deeplink?: string; stat?: string }[],
  sport?: string
): string | null {
  const b = book.toLowerCase()
  let sp = (sport || '').toLowerCase()
  if (!sp && gameKey) {
    if (gameKey.includes(':')) sp = gameKey.split(':')[0].toLowerCase()
    else if (gameKey.includes('_')) sp = gameKey.split('_')[0].toLowerCase()
  }
  if (!sp && legs.length > 0) {
    const st = (legs[0].stat || '').toLowerCase()
    if (['points', 'rebounds', 'assists', 'pra', 'pr', 'pa', 'ra', 'threes', 'triple_double', 'double_double'].includes(st)) sp = 'nba'
    else if (['pass_yds', 'rush_yds', 'rec_yds', 'receptions', 'pass_tds', 'anytime_td'].includes(st)) sp = 'nfl'
    else if (['goals', 'sog', 'saves', 'blocked_shots'].includes(st)) sp = 'nhl'
  }
  if (!sp) sp = 'nba'

  const isInternal = !gameKey || gameKey.includes(':') || gameKey.includes('@') || gameKey.startsWith('nba_') || gameKey.startsWith('nfl_') || gameKey.startsWith('nhl_') || gameKey.startsWith('mlb_')

  const directUrls: string[] = []
  for (const l of legs) {
    const lnk = l.over_link || l.link || l.deeplink
    if (lnk && typeof lnk === 'string' && lnk.startsWith('http')) directUrls.push(lnk)
  }

  if (b === 'draftkings' || b === 'dk') {
    const sids = legs.map(l => l.over_sid || l.sid).filter(Boolean)
    let dkEid: string | null = !isInternal ? gameKey : null
    if (!dkEid) {
      for (const u of directUrls) {
        if (u.includes('sportsbook.draftkings.com/event/')) {
          const part = u.split('sportsbook.draftkings.com/event/')[1]?.split('?')[0]?.split('/')[0]
          if (part && !part.includes(':') && !part.includes('@')) {
            dkEid = part
            break
          }
        }
      }
    }
    if (sids.length >= 2 && dkEid) return `https://sportsbook.draftkings.com/event/${dkEid}?outcomes=${sids.join(',')}`
    if (dkEid) return `https://sportsbook.draftkings.com/event/${dkEid}`
    for (const u of directUrls) {
      if (u.includes('sportsbook.draftkings.com')) return u
    }
    return SPORT_LEAGUE_URLS_TS.draftkings?.[sp] || 'https://sportsbook.draftkings.com'
  }

  if (b === 'fanduel' || b === 'fd') {
    const pairs = legs.filter(l => l.marketId && (l.selectionId || l.over_sid || l.sid))
    if (pairs.length >= 2) {
      const q = pairs.map((p, i) => `marketId[${i}]=${p.marketId}&selectionId[${i}]=${p.selectionId || p.over_sid || p.sid}`).join('&')
      return `https://sportsbook.fanduel.com/addToBetslip?${q}`
    }
    for (const u of directUrls) {
      if (u.includes('fanduel.com')) return u
    }
    if (gameKey && !isInternal) return `https://sportsbook.fanduel.com/event/${gameKey}`
    return SPORT_LEAGUE_URLS_TS.fanduel?.[sp] || 'https://sportsbook.fanduel.com'
  }

  if (b === 'betmgm') {
    if (gameKey && !isInternal) return `https://sports.betmgm.com/en/sports/events/${gameKey}`
    return SPORT_LEAGUE_URLS_TS.betmgm?.[sp] || 'https://sports.betmgm.com'
  }

  if (b === 'caesars') {
    if (gameKey && !isInternal) return `https://www.caesars.com/sportsbook-and-casino/us/event/${gameKey}`
    return SPORT_LEAGUE_URLS_TS.caesars?.[sp] || 'https://www.caesars.com/sportsbook-and-casino'
  }

  if (b === 'bet365') return 'https://www.bet365.com/#/AC/B1/'
  if (b === 'fanatics') return 'https://sportsbook.fanatics.com'
  if (b === 'hardrock') return 'https://hardrock.bet'
  if (b === 'novig') return 'https://novig.us'
  if (b === 'prophetx') return 'https://prophetx.co'
  if (b === 'kalshi') return 'https://kalshi.com'

  return `https://www.${b}.com`
}

interface SgpArchDef {
  key: string
  label: string
  sport: string
  rhoTrue: number
  rhoBook: number
  holdBook: number
}

const SGP_ARCH_DEFS: Record<string, SgpArchDef> = {
  nba_reb_ast_10_10: {
    key: 'nba_reb_ast_10_10',
    label: 'TD PROXY',
    sport: 'nba',
    rhoTrue: 0.22,
    rhoBook: 0.08,
    holdBook: 0.12,
  },
  nba_pts_reb_vs_pr: {
    key: 'nba_pts_reb_vs_pr',
    label: 'P+R PROXY',
    sport: 'nba',
    rhoTrue: 0.15,
    rhoBook: 0.06,
    holdBook: 0.14,
  },
  nba_pts_ast_vs_pa: {
    key: 'nba_pts_ast_vs_pa',
    label: 'P+A PROXY',
    sport: 'nba',
    rhoTrue: 0.18,
    rhoBook: 0.07,
    holdBook: 0.14,
  },
  nba_reb_ast_vs_ra: {
    key: 'nba_reb_ast_vs_ra',
    label: 'R+A PROXY',
    sport: 'nba',
    rhoTrue: 0.22,
    rhoBook: 0.08,
    holdBook: 0.14,
  },
  nba_dd_proxy: {
    key: 'nba_dd_proxy',
    label: 'DD PROXY',
    sport: 'nba',
    rhoTrue: 0.16,
    rhoBook: 0.07,
    holdBook: 0.12,
  },
  nhl_goal_ast_vs_pts: {
    key: 'nhl_goal_ast_vs_pts',
    label: 'G+A PROXY',
    sport: 'nhl',
    rhoTrue: 0.25,
    rhoBook: 0.10,
    holdBook: 0.15,
  },
  nhl_linemate_pts_stack: {
    key: 'nhl_linemate_pts_stack',
    label: 'LINEMATE PTS',
    sport: 'nhl',
    rhoTrue: 0.32,
    rhoBook: 0.10,
    holdBook: 0.14,
  },
  nfl_qb_wr_stack: {
    key: 'nfl_qb_wr_stack',
    label: 'QB+WR STACK',
    sport: 'nfl',
    rhoTrue: 0.35,
    rhoBook: 0.18,
    holdBook: 0.15,
  },
  nba_teammate_ast_pts_stack: {
    key: 'nba_teammate_ast_pts_stack',
    label: 'AST+PTS STACK',
    sport: 'nba',
    rhoTrue: 0.26,
    rhoBook: 0.08,
    holdBook: 0.14,
  },
  nfl_td_yds_stack: {
    key: 'nfl_td_yds_stack',
    label: 'TD+YDS STACK',
    sport: 'nfl',
    rhoTrue: 0.32,
    rhoBook: 0.15,
    holdBook: 0.15,
  },
}

function resolveFairOver(
  row: BoardRow,
  method: DevigMethod,
  refs: ReadonlySet<string> | null,
  excludeBook: string | null,
  useCurve: boolean
): number | null {
  let sum = 0
  let n = 0
  const onesidedImplied: number[] = []
  for (const b of row.books) {
    if (b.book === excludeBook || (refs && !refs.has(b.book))) continue
    const p = devigOver(b.over, b.under, method)
    if (p != null) {
      sum += p
      n += 1
    } else if (b.over != null && b.under == null && b.over !== 0) {
      const q = americanToImpliedProb(b.over)
      if (q > 0 && q < 1) onesidedImplied.push(q)
    }
  }
  if (n >= 1) return sum / n
  if (useCurve && row.curve_fair_over != null && row.curve_fair_over > 0 && row.curve_fair_over < 1) {
    return row.curve_fair_over
  }
  if (onesidedImplied.length >= 2) {
    onesidedImplied.sort((a, b) => a - b)
    const mid = Math.floor(onesidedImplied.length / 2)
    const med = onesidedImplied.length % 2 !== 0
      ? onesidedImplied[mid]
      : (onesidedImplied[mid - 1] + onesidedImplied[mid]) / 2
    return Math.min(Math.max(med * 0.86, 0.01), 0.95)
  }
  return null
}

function getBestPriceOnBoardRow(
  row: BoardRow,
  bettable: ReadonlySet<string> | null
): { book: string; american: number; dec: number } | null {
  let best: { book: string; american: number; dec: number } | null = null
  for (const b of row.books) {
    if (bettable && !bettable.has(b.book)) continue
    if (b.over == null || !isFinite(b.over) || b.over === 0) continue
    const dec = americanToDecimal(b.over)
    if (!best || dec > best.dec) {
      best = { book: b.book, american: b.over, dec }
    }
  }
  return best
}

/**
 * Scan banked BoardRow props for synthetic SGP proxy combinations.
 */
export function computeSgpProxies(rows: BoardRow[], opts: SgpProxyOptions): SgpProxyRow[] {
  const groups = new Map<string, BoardRow[]>()
  const gameTeamGroups = new Map<string, BoardRow[]>()

  for (const r of rows) {
    const k = `${r.game_key}|${r.player_norm}`
    const g = groups.get(k)
    if (g) g.push(r)
    else groups.set(k, [r])

    if (r.team) {
      const gtk = `${r.game_key}|${r.team.toUpperCase().trim()}`
      const gtg = gameTeamGroups.get(gtk)
      if (gtg) gtg.push(r)
      else gameTeamGroups.set(gtk, [r])
    }
  }

  const refs = opts.refs.length ? new Set(opts.refs) : null
  const raw: SgpProxyRow[] = []

  // 1. Single-Player Proxies & Combos
  for (const g of groups.values()) {
    if (g.length < 2) continue
    const sample = g[0]
    const sp = ((sample as { sport?: string }).sport || (sample.game_key.includes(':') ? sample.game_key.split(':')[0] : 'nba')).toLowerCase()
    const statMap = new Map<string, BoardRow>()
    for (const r of g) {
      statMap.set(`${r.stat}|${r.line}`, r)
    }

    // Candidate 1: NBA Triple-Double Proxy (10+ Reb + 10+ Ast)
    const reb10 = statMap.get('rebounds|9.5') || statMap.get('rebounds|10') || statMap.get('rebounds|10.0')
    const ast10 = statMap.get('assists|9.5') || statMap.get('assists|10') || statMap.get('assists|10.0')
    if (reb10 && ast10 && sp === 'nba') {
      const arch = SGP_ARCH_DEFS.nba_reb_ast_10_10
      if (!opts.archetype || opts.archetype === 'all' || opts.archetype === arch.key) {
        const directRow = g.find(r => r.stat === 'triple_double' || r.stat === 'td' || r.stat === 'player_triple_double')
        const directBest = directRow ? getBestPriceOnBoardRow(directRow, opts.bettable) : null

        const booksReb = new Map(reb10.books.filter(b => b.over != null).map(b => [b.book, b]))
        const booksAst = new Map(ast10.books.filter(b => b.over != null).map(b => [b.book, b]))

        for (const [bName, bReb] of booksReb) {
          if (opts.bettable && !opts.bettable.has(bName)) continue
          if (opts.book && opts.book !== 'all' && opts.book !== bName) continue
          const bAst = booksAst.get(bName)
          if (!bAst || bAst.over == null || bReb.over == null) continue

          const fReb = resolveFairOver(reb10, opts.method, refs, bName, opts.useCurve)
          const fAst = resolveFairOver(ast10, opts.method, refs, bName, opts.useCurve)
          if (fReb == null || fAst == null) continue
          if (fReb < 0.04 || fReb > 0.88 || fAst < 0.04 || fAst > 0.88) continue

          const fairJointProb = copulaJoint2D(fReb, fAst, arch.rhoTrue)
          const fairJointAm = fairAmericanOf(fairJointProb)

          const qReb = americanToImpliedProb(bReb.over)
          const qAst = americanToImpliedProb(bAst.over)
          const pNaive = qReb * qAst

          const pSgp = Math.min(0.99, (1 + arch.holdBook) * copulaJoint2D(qReb, qAst, arch.rhoBook))
          const estSgpDec = 1 / pSgp
          const estSgpAm = fairAmericanOf(pSgp)
          const evPct = (fairJointProb * estSgpDec - 1) * 100
          if (evPct < opts.minEvPct || evPct > 45.0) continue

          const vsDirectPct = directBest ? ((estSgpDec / directBest.dec) - 1) * 100 : null
          const legs: SgpLeg[] = [
            { stat: 'rebounds', label: `o${reb10.line} Reb`, line: reb10.line, side: 'OVER', bookAm: bReb.over, fairProb: fReb },
            { stat: 'assists', label: `o${ast10.line} Ast`, line: ast10.line, side: 'OVER', bookAm: bAst.over, fairProb: fAst },
          ]
          const deeplink = buildSgpDeeplinkTs(bName, sample.game_key, legs, 'nba')

          raw.push({
            key: `${sample.game_key}|${sample.player_norm}|${arch.key}|${bName}`,
            archetype: arch.key,
            archetypeLabel: arch.label,
            sport: 'nba',
            player: sample.player,
            player_norm: sample.player_norm,
            player_id: sample.player_id,
            headshot_id: sample.headshot_id,
            team: sample.team,
            game_key: sample.game_key,
            commence: sample.commence,
            away: sample.away,
            home: sample.home,
            book: bName,
            legs,
            naiveSingleProductAm: fairAmericanOf(pNaive),
            estSgpDec,
            estSgpAm,
            fairJointProb,
            fairJointAm,
            rhoTrue: arch.rhoTrue,
            rhoBook: arch.rhoBook,
            holdBook: arch.holdBook,
            evPct,
            directMarketLabel: directBest ? 'Triple-Double' : null,
            directMarketAm: directBest ? directBest.american : null,
            directMarketBook: directBest ? directBest.book : null,
            vsDirectPct,
            deeplink,
          })
        }
      }
    }

    // Candidate 2: NBA Stat Combos (P+R, P+A, R+A)
    const mainPts = g.find(r => r.stat === 'points' && !r.is_alt)
    const mainReb = g.find(r => r.stat === 'rebounds' && !r.is_alt)
    const mainAst = g.find(r => r.stat === 'assists' && !r.is_alt)

    const comboConfigs = [
      { r1: mainPts, r2: mainReb, directCode: 'pr', directAliases: ['pr', 'points_rebounds'], arch: SGP_ARCH_DEFS.nba_pts_reb_vs_pr, lbl1: 'Pts', lbl2: 'Reb' },
      { r1: mainPts, r2: mainAst, directCode: 'pa', directAliases: ['pa', 'points_assists'], arch: SGP_ARCH_DEFS.nba_pts_ast_vs_pa, lbl1: 'Pts', lbl2: 'Ast' },
      { r1: mainReb, r2: mainAst, directCode: 'ra', directAliases: ['ra', 'rebounds_assists'], arch: SGP_ARCH_DEFS.nba_reb_ast_vs_ra, lbl1: 'Reb', lbl2: 'Ast' },
    ]

    if (sp === 'nba') {
      for (const { r1, r2, directCode, directAliases, arch, lbl1, lbl2 } of comboConfigs) {
        if (!r1 || !r2) continue
        if (!opts.archetype || opts.archetype === 'all' || opts.archetype === arch.key) {
          const expectedSum = r1.line + r2.line
          const directRow = g.find(r => directAliases.includes(r.stat) && Math.abs(r.line - expectedSum) <= 1.0)
          const directBest = directRow ? getBestPriceOnBoardRow(directRow, opts.bettable) : null

          const books1 = new Map(r1.books.filter(b => b.over != null).map(b => [b.book, b]))
          const books2 = new Map(r2.books.filter(b => b.over != null).map(b => [b.book, b]))

          for (const [bName, b1] of books1) {
            if (opts.bettable && !opts.bettable.has(bName)) continue
            if (opts.book && opts.book !== 'all' && opts.book !== bName) continue
            const b2 = books2.get(bName)
            if (!b2 || b2.over == null || b1.over == null) continue

            const f1 = resolveFairOver(r1, opts.method, refs, bName, opts.useCurve)
            const f2 = resolveFairOver(r2, opts.method, refs, bName, opts.useCurve)
            if (f1 == null || f2 == null) continue
            if (f1 < 0.04 || f1 > 0.88 || f2 < 0.04 || f2 > 0.88) continue

            const fairJointProb = copulaJoint2D(f1, f2, arch.rhoTrue)
            const fairJointAm = fairAmericanOf(fairJointProb)

            const q1 = americanToImpliedProb(b1.over)
            const q2 = americanToImpliedProb(b2.over)
            const pNaive = q1 * q2

            const pSgp = Math.min(0.99, (1 + arch.holdBook) * copulaJoint2D(q1, q2, arch.rhoBook))
            const estSgpDec = 1 / pSgp
            const estSgpAm = fairAmericanOf(pSgp)
            const evPct = (fairJointProb * estSgpDec - 1) * 100
            if (evPct < opts.minEvPct || evPct > 45.0) continue

            const vsDirectPct = directBest ? ((estSgpDec / directBest.dec) - 1) * 100 : null
            const legs: SgpLeg[] = [
              { stat: r1.stat, label: `o${r1.line} ${lbl1}`, line: r1.line, side: 'OVER', bookAm: b1.over, fairProb: f1 },
              { stat: r2.stat, label: `o${r2.line} ${lbl2}`, line: r2.line, side: 'OVER', bookAm: b2.over, fairProb: f2 },
            ]
            const deeplink = buildSgpDeeplinkTs(bName, sample.game_key, legs, 'nba')

            raw.push({
              key: `${sample.game_key}|${sample.player_norm}|${arch.key}|${bName}`,
              archetype: arch.key,
              archetypeLabel: arch.label,
              sport: 'nba',
              player: sample.player,
              player_norm: sample.player_norm,
              player_id: sample.player_id,
              headshot_id: sample.headshot_id,
              team: sample.team,
              game_key: sample.game_key,
              commence: sample.commence,
              away: sample.away,
              home: sample.home,
              book: bName,
              legs,
              naiveSingleProductAm: fairAmericanOf(pNaive),
              estSgpDec,
              estSgpAm,
              fairJointProb,
              fairJointAm,
              rhoTrue: arch.rhoTrue,
              rhoBook: arch.rhoBook,
              holdBook: arch.holdBook,
              evPct,
              directMarketLabel: directBest && directRow ? `${directCode.toUpperCase()} o${directRow.line}` : null,
              directMarketAm: directBest ? directBest.american : null,
              directMarketBook: directBest ? directBest.book : null,
              vsDirectPct,
              deeplink,
            })
          }
        }
      }
    }

    // Candidate 3: NHL 1+ Goal + 1+ Assist vs 2+ Points (Points o1.5)
    const goal05 = statMap.get('goals|0.5')
    const ast05 = statMap.get('assists|0.5')
    const pts15 = statMap.get('points|1.5')
    if (goal05 && ast05 && sp === 'nhl') {
      const arch = SGP_ARCH_DEFS.nhl_goal_ast_vs_pts
      if (!opts.archetype || opts.archetype === 'all' || opts.archetype === arch.key) {
        const directBest = pts15 ? getBestPriceOnBoardRow(pts15, opts.bettable) : null

        const booksG = new Map(goal05.books.filter(b => b.over != null).map(b => [b.book, b]))
        const booksA = new Map(ast05.books.filter(b => b.over != null).map(b => [b.book, b]))

        for (const [bName, bG] of booksG) {
          if (opts.bettable && !opts.bettable.has(bName)) continue
          if (opts.book && opts.book !== 'all' && opts.book !== bName) continue
          const bA = booksA.get(bName)
          if (!bA || bA.over == null || bG.over == null) continue

          const fG = resolveFairOver(goal05, opts.method, refs, bName, opts.useCurve)
          const fA = resolveFairOver(ast05, opts.method, refs, bName, opts.useCurve)
          if (fG == null || fA == null) continue
          if (fG < 0.04 || fG > 0.88 || fA < 0.04 || fA > 0.88) continue

          const fairJointProb = copulaJoint2D(fG, fA, arch.rhoTrue)
          const fairJointAm = fairAmericanOf(fairJointProb)

          const qG = americanToImpliedProb(bG.over)
          const qA = americanToImpliedProb(bA.over)
          const pNaive = qG * qA

          const pSgp = Math.min(0.99, (1 + arch.holdBook) * copulaJoint2D(qG, qA, arch.rhoBook))
          const estSgpDec = 1 / pSgp
          const estSgpAm = fairAmericanOf(pSgp)
          const evPct = (fairJointProb * estSgpDec - 1) * 100
          if (evPct < opts.minEvPct || evPct > 45.0) continue

          const vsDirectPct = directBest ? ((estSgpDec / directBest.dec) - 1) * 100 : null
          const legs: SgpLeg[] = [
            { stat: 'goals', label: '1+ Goal', line: 0.5, side: 'OVER', bookAm: bG.over, fairProb: fG },
            { stat: 'assists', label: '1+ Assist', line: 0.5, side: 'OVER', bookAm: bA.over, fairProb: fA },
          ]
          const deeplink = buildSgpDeeplinkTs(bName, sample.game_key, legs, 'nhl')

          raw.push({
            key: `${sample.game_key}|${sample.player_norm}|${arch.key}|${bName}`,
            archetype: arch.key,
            archetypeLabel: arch.label,
            sport: 'nhl',
            player: sample.player,
            player_norm: sample.player_norm,
            player_id: sample.player_id,
            headshot_id: sample.headshot_id,
            team: sample.team,
            game_key: sample.game_key,
            commence: sample.commence,
            away: sample.away,
            home: sample.home,
            book: bName,
            legs,
            naiveSingleProductAm: fairAmericanOf(pNaive),
            estSgpDec,
            estSgpAm,
            fairJointProb,
            fairJointAm,
            rhoTrue: arch.rhoTrue,
            rhoBook: arch.rhoBook,
            holdBook: arch.holdBook,
            evPct,
            directMarketLabel: directBest ? 'Over 1.5 Points' : null,
            directMarketAm: directBest ? directBest.american : null,
            directMarketBook: directBest ? directBest.book : null,
            vsDirectPct,
            deeplink,
          })
        }
      }
    }
  }

  // 2. Multi-Player Teammate Combos (NHL Linemates, NFL QB+WR, NBA Ast+Pts)
  for (const [gtKey, tRows] of gameTeamGroups.entries()) {
    if (tRows.length < 2) continue
    const sample = tRows[0]
    const sp = ((sample as { sport?: string }).sport || (sample.game_key.includes(':') ? sample.game_key.split(':')[0] : '')).toLowerCase()
    const teamCode = sample.team || ''

    const teamPlayers = new Map<string, BoardRow[]>()
    for (const r of tRows) {
      const g = teamPlayers.get(r.player_norm)
      if (g) g.push(r)
      else teamPlayers.set(r.player_norm, [r])
    }

    // Teammate 1: NHL Linemates 1+ Point Stack
    if (sp === 'nhl') {
      const arch = SGP_ARCH_DEFS.nhl_linemate_pts_stack
      if (!opts.archetype || opts.archetype === 'all' || opts.archetype === arch.key) {
        const skaters: { player: string; player_norm: string; fairProb: number; row: BoardRow }[] = []
        for (const [pn, plRows] of teamPlayers.entries()) {
          const ptsRow = plRows.find(r => r.stat === 'points' && r.line === 0.5)
          if (ptsRow) {
            const fPts = resolveFairOver(ptsRow, opts.method, refs, null, opts.useCurve)
            if (fPts != null && fPts >= 0.04 && fPts <= 0.88) {
              skaters.push({ player: ptsRow.player, player_norm: pn, fairProb: fPts, row: ptsRow })
            }
          }
        }
        skaters.sort((a, b) => b.fairProb - a.fairProb)
        const topSkaters = skaters.slice(0, 3)

        for (let i = 0; i < topSkaters.length; i++) {
          for (let j = i + 1; j < topSkaters.length; j++) {
            const s1 = topSkaters[i]
            const s2 = topSkaters[j]
            const b1Map = new Map(s1.row.books.filter(b => b.over != null).map(b => [b.book, b]))
            const b2Map = new Map(s2.row.books.filter(b => b.over != null).map(b => [b.book, b]))

            for (const [bName, b1] of b1Map) {
              if (opts.bettable && !opts.bettable.has(bName)) continue
              if (opts.book && opts.book !== 'all' && opts.book !== bName) continue
              const b2 = b2Map.get(bName)
              if (!b2 || b2.over == null || b1.over == null) continue

              const pTrue = copulaJoint2D(s1.fairProb, s2.fairProb, arch.rhoTrue)
              const q1 = americanToImpliedProb(b1.over)
              const q2 = americanToImpliedProb(b2.over)
              const pNaive = q1 * q2

              const pSgp = Math.min(0.99, (1 + arch.holdBook) * copulaJoint2D(q1, q2, arch.rhoBook))
              const estSgpDec = 1 / pSgp
              const estSgpAm = fairAmericanOf(pSgp)
              const evPct = (pTrue * estSgpDec - 1) * 100
              if (evPct < opts.minEvPct || evPct > 45.0) continue

              const legs: SgpLeg[] = [
                { stat: 'points', label: `${s1.player} 1+ Pt`, line: 0.5, side: 'OVER', bookAm: b1.over, fairProb: s1.fairProb },
                { stat: 'points', label: `${s2.player} 1+ Pt`, line: 0.5, side: 'OVER', bookAm: b2.over, fairProb: s2.fairProb },
              ]
              const deeplink = buildSgpDeeplinkTs(bName, sample.game_key, legs, 'nhl')

              raw.push({
                key: `${sample.game_key}|${s1.player_norm}+${s2.player_norm}|${arch.key}|${bName}`,
                archetype: arch.key,
                archetypeLabel: arch.label,
                sport: 'nhl',
                player: `${s1.player} + ${s2.player} (${teamCode})`,
                player_norm: `${s1.player_norm}+${s2.player_norm}`,
                team: teamCode,
                game_key: sample.game_key,
                commence: sample.commence,
                away: sample.away,
                home: sample.home,
                book: bName,
                legs,
                naiveSingleProductAm: fairAmericanOf(pNaive),
                estSgpDec,
                estSgpAm,
                fairJointProb: pTrue,
                fairJointAm: fairAmericanOf(pTrue),
                rhoTrue: arch.rhoTrue,
                rhoBook: arch.rhoBook,
                holdBook: arch.holdBook,
                evPct,
                deeplink,
              })
            }
          }
        }
      }
    }

    // Teammate 2: NFL QB Pass Yds + WR Rec Yds Stack
    if (sp === 'nfl') {
      const arch = SGP_ARCH_DEFS.nfl_qb_wr_stack
      if (!opts.archetype || opts.archetype === 'all' || opts.archetype === arch.key) {
        let qbRow: { player: string; player_norm: string; fairProb: number; line: number; row: BoardRow } | null = null
        const receivers: { player: string; player_norm: string; fairProb: number; line: number; row: BoardRow }[] = []

        for (const [pn, plRows] of teamPlayers.entries()) {
          const passRow = plRows.find(r => r.stat === 'pass_yds' && !r.is_alt)
          if (passRow && !qbRow) {
            const fPass = resolveFairOver(passRow, opts.method, refs, null, opts.useCurve)
            if (fPass != null && fPass >= 0.04 && fPass <= 0.88) {
              qbRow = { player: passRow.player, player_norm: pn, fairProb: fPass, line: passRow.line, row: passRow }
            }
          }
          const recRow = plRows.find(r => r.stat === 'rec_yds' && !r.is_alt && r.line >= 40.5)
          if (recRow) {
            const fRec = resolveFairOver(recRow, opts.method, refs, null, opts.useCurve)
            if (fRec != null && fRec >= 0.04 && fRec <= 0.88) {
              receivers.push({ player: recRow.player, player_norm: pn, fairProb: fRec, line: recRow.line, row: recRow })
            }
          }
        }

        receivers.sort((a, b) => b.line - a.line)
        const topWrs = receivers.filter(w => !qbRow || w.player_norm !== qbRow.player_norm).slice(0, 2)

        if (qbRow && topWrs.length > 0) {
          for (const wr of topWrs) {
            const b1Map = new Map(qbRow.row.books.filter(b => b.over != null).map(b => [b.book, b]))
            const b2Map = new Map(wr.row.books.filter(b => b.over != null).map(b => [b.book, b]))

            for (const [bName, b1] of b1Map) {
              if (opts.bettable && !opts.bettable.has(bName)) continue
              if (opts.book && opts.book !== 'all' && opts.book !== bName) continue
              const b2 = b2Map.get(bName)
              if (!b2 || b2.over == null || b1.over == null) continue

              const pTrue = copulaJoint2D(qbRow.fairProb, wr.fairProb, arch.rhoTrue)
              const q1 = americanToImpliedProb(b1.over)
              const q2 = americanToImpliedProb(b2.over)
              const pNaive = q1 * q2

              const pSgp = Math.min(0.99, (1 + arch.holdBook) * copulaJoint2D(q1, q2, arch.rhoBook))
              const estSgpDec = 1 / pSgp
              const estSgpAm = fairAmericanOf(pSgp)
              const evPct = (pTrue * estSgpDec - 1) * 100
              if (evPct < opts.minEvPct || evPct > 45.0) continue

              const legs: SgpLeg[] = [
                { stat: 'pass_yds', label: `${qbRow.player} o${qbRow.line} Pass`, line: qbRow.line, side: 'OVER', bookAm: b1.over, fairProb: qbRow.fairProb },
                { stat: 'rec_yds', label: `${wr.player} o${wr.line} Rec`, line: wr.line, side: 'OVER', bookAm: b2.over, fairProb: wr.fairProb },
              ]
              const deeplink = buildSgpDeeplinkTs(bName, sample.game_key, legs, 'nfl')

              raw.push({
                key: `${sample.game_key}|${qbRow.player_norm}+${wr.player_norm}|${arch.key}|${bName}`,
                archetype: arch.key,
                archetypeLabel: arch.label,
                sport: 'nfl',
                player: `${qbRow.player} + ${wr.player} (${teamCode})`,
                player_norm: `${qbRow.player_norm}+${wr.player_norm}`,
                team: teamCode,
                game_key: sample.game_key,
                commence: sample.commence,
                away: sample.away,
                home: sample.home,
                book: bName,
                legs,
                naiveSingleProductAm: fairAmericanOf(pNaive),
                estSgpDec,
                estSgpAm,
                fairJointProb: pTrue,
                fairJointAm: fairAmericanOf(pTrue),
                rhoTrue: arch.rhoTrue,
                rhoBook: arch.rhoBook,
                holdBook: arch.holdBook,
                evPct,
                deeplink,
              })
            }
          }
        }
      }
    }

    // Teammate 3: NBA Playmaker Ast + Scorer Pts Stack
    if (sp === 'nba') {
      const arch = SGP_ARCH_DEFS.nba_teammate_ast_pts_stack
      if (!opts.archetype || opts.archetype === 'all' || opts.archetype === arch.key) {
        const playmakers: { player: string; player_norm: string; fairProb: number; line: number; row: BoardRow }[] = []
        const scorers: { player: string; player_norm: string; fairProb: number; line: number; row: BoardRow }[] = []

        for (const [pn, plRows] of teamPlayers.entries()) {
          const astRow = plRows.find(r => r.stat === 'assists' && !r.is_alt && r.line >= 6.5)
          if (astRow) {
            const fAst = resolveFairOver(astRow, opts.method, refs, null, opts.useCurve)
            if (fAst != null && fAst >= 0.04 && fAst <= 0.88) {
              playmakers.push({ player: astRow.player, player_norm: pn, fairProb: fAst, line: astRow.line, row: astRow })
            }
          }
          const ptsRow = plRows.find(r => r.stat === 'points' && !r.is_alt && r.line >= 20.5)
          if (ptsRow) {
            const fPts = resolveFairOver(ptsRow, opts.method, refs, null, opts.useCurve)
            if (fPts != null && fPts >= 0.04 && fPts <= 0.88) {
              scorers.push({ player: ptsRow.player, player_norm: pn, fairProb: fPts, line: ptsRow.line, row: ptsRow })
            }
          }
        }

        playmakers.sort((a, b) => b.line - a.line)
        scorers.sort((a, b) => b.line - a.line)

        if (playmakers.length > 0 && scorers.length > 0) {
          const pg = playmakers[0]
          const scorer = scorers.find(s => s.player_norm !== pg.player_norm)
          if (scorer) {
            const b1Map = new Map(pg.row.books.filter(b => b.over != null).map(b => [b.book, b]))
            const b2Map = new Map(scorer.row.books.filter(b => b.over != null).map(b => [b.book, b]))

            for (const [bName, b1] of b1Map) {
              if (opts.bettable && !opts.bettable.has(bName)) continue
              if (opts.book && opts.book !== 'all' && opts.book !== bName) continue
              const b2 = b2Map.get(bName)
              if (!b2 || b2.over == null || b1.over == null) continue

              const pTrue = copulaJoint2D(pg.fairProb, scorer.fairProb, arch.rhoTrue)
              const q1 = americanToImpliedProb(b1.over)
              const q2 = americanToImpliedProb(b2.over)
              const pNaive = q1 * q2

              const pSgp = Math.min(0.99, (1 + arch.holdBook) * copulaJoint2D(q1, q2, arch.rhoBook))
              const estSgpDec = 1 / pSgp
              const estSgpAm = fairAmericanOf(pSgp)
              const evPct = (pTrue * estSgpDec - 1) * 100
              if (evPct < opts.minEvPct || evPct > 45.0) continue

              const legs: SgpLeg[] = [
                { stat: 'assists', label: `${pg.player} o${pg.line} Ast`, line: pg.line, side: 'OVER', bookAm: b1.over, fairProb: pg.fairProb },
                { stat: 'points', label: `${scorer.player} o${scorer.line} Pts`, line: scorer.line, side: 'OVER', bookAm: b2.over, fairProb: scorer.fairProb },
              ]
              const deeplink = buildSgpDeeplinkTs(bName, sample.game_key, legs, 'nba')

              raw.push({
                key: `${sample.game_key}|${pg.player_norm}+${scorer.player_norm}|${arch.key}|${bName}`,
                archetype: arch.key,
                archetypeLabel: arch.label,
                sport: 'nba',
                player: `${pg.player} + ${scorer.player} (${teamCode})`,
                player_norm: `${pg.player_norm}+${scorer.player_norm}`,
                team: teamCode,
                game_key: sample.game_key,
                commence: sample.commence,
                away: sample.away,
                home: sample.home,
                book: bName,
                legs,
                naiveSingleProductAm: fairAmericanOf(pNaive),
                estSgpDec,
                estSgpAm,
                fairJointProb: pTrue,
                fairJointAm: fairAmericanOf(pTrue),
                rhoTrue: arch.rhoTrue,
                rhoBook: arch.rhoBook,
                holdBook: arch.holdBook,
                evPct,
                deeplink,
              })
            }
          }
        }
      }
    }
  }

  // Deduplicate across books for each (archetype, game_key, player_norm)
  const dedupMap = new Map<string, SgpProxyRow[]>()
  for (const r of raw) {
    const k = `${r.archetype}|${r.game_key}|${r.player_norm}`
    const list = dedupMap.get(k)
    if (list) list.push(r)
    else dedupMap.set(k, [r])
  }

  const out: SgpProxyRow[] = []
  for (const group of dedupMap.values()) {
    group.sort((a, b) => b.evPct - a.evPct)
    const best = group[0]
    if (group.length > 1) {
      best.runnerUpBooks = group.slice(1).map(other => ({
        book: other.book,
        evPct: other.evPct,
        estSgpAm: other.estSgpAm,
      }))
    }
    out.push(best)
  }

  return out.sort((a, b) => b.evPct - a.evPct)
}
