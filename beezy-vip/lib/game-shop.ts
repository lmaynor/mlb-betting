/**
 * What a bettor does with a game: shop the best number, spot a real edge, see what is moving. Pure functions over the board entries
 * (lib/game-board.ts), no React, so the row cells, the popup card and the "edges now" strip share one definition of "actionable".
 *
 * Two kinds of price are worth a look, both against the MARKET fair price (never our unvalidated model), on a line with enough books to
 * have one, at a book the viewer can bet at, and only when the gap survives a median fair price (one stale book cannot invent it):
 *   EDGE     2% to 15% better than fair: a clean line-shopping or soft-line edge, any line, any price (a +5000 alt counts)
 *   OUTLIER  more than 15% better: usually a stale or erroneous quote and sometimes the best bet on the board, so it is shown (amber,
 *            "check") and never hidden or styled as a clean edge
 */
import { decimalOf, LADDER_SUSPECT_EV, LADDER_THIN_BELOW } from '@/lib/game-ladder'
import type { GameBoardEntry } from '@/lib/game-board'
import { sideLabels, type GameMarket, type GamePriceRow } from '@/lib/game-odds'
import { hasMovement, type MoveSummary } from '@/lib/line-move'
import { americanToImpliedProb } from '@/lib/odds'
import { isOffshore } from '@/lib/offshore'
import { bookDisplay } from '@/lib/tokens'

/** The smallest market-fair EV the board calls an edge. */
export const EDGE_MIN = 0.02

export interface ShopSide {
  /** 'FLA -1.5', 'O 6.5', 'DET ML' */
  label: string
  american: number | null
  book: string | null
  ev: number | null
  /** the next best prices at other books (up to three), best first */
  others: { book: string; american: number }[]
  /** a clean EDGE: EV from EDGE_MIN up to LADDER_SUSPECT_EV against the market fair price, 3+ books, survives the median */
  actionable: boolean
  /** an OUTLIER: EV above LADDER_SUSPECT_EV and it survives the median: check the price at the book before trusting it */
  suspect: boolean
}

/**
 * The market fair probability of one side as the MEDIAN of every other book's own de-vigged two-way price. The board's fair value is a
 * mean, so a single stale or fat-fingered book drags it and invents an edge on the OTHER side; an edge is only called one when it
 * survives the median too. Null with fewer than three two-way books besides `exclude`.
 */
export function medianFair(r: GamePriceRow, which: 'a' | 'b', exclude: string | null): number | null {
  const ps: number[] = []
  for (const b of r.row.books ?? []) {
    if (b.book === exclude || b.a == null || b.b == null) continue
    const pa = americanToImpliedProb(b.a), pb = americanToImpliedProb(b.b)
    const t = pa + pb
    if (t > 0) ps.push((which === 'a' ? pa : pb) / t)
  }
  if (ps.length < LADDER_THIN_BELOW) return null
  ps.sort((x, y) => x - y)
  const m = Math.floor(ps.length / 2)
  return ps.length % 2 ? ps[m] : (ps[m - 1] + ps[m]) / 2
}

export function shopSide(r: GamePriceRow, which: 'a' | 'b'): ShopSide {
  const sp = which === 'a' ? r.a : r.b
  const label = sideLabels(r.row)[which]
  const others = (r.row.books ?? [])
    .filter(b => !isOffshore(b.book) && b.book !== sp.book)
    .map(b => ({ book: b.book, american: which === 'a' ? b.a : b.b }))
    .filter((x): x is { book: string; american: number } => x.american != null)
    .sort((x, y) => decimalOf(y.american) - decimalOf(x.american) || x.book.localeCompare(y.book))
    .slice(0, 3)
  const ev = sp.ev
  const enough = (r.row.books ?? []).length >= LADDER_THIN_BELOW
  const med = sp.american != null ? medianFair(r, which, sp.book) : null
  const evMedian = med != null && sp.american != null ? med * decimalOf(sp.american) - 1 : null
  const robust = evMedian != null && evMedian >= EDGE_MIN
  const big = ev != null && ev > LADDER_SUSPECT_EV
  const suspect = big && enough && robust
  return { label, american: sp.american, book: sp.book, ev, others, suspect, actionable: ev != null && enough && robust && !big && ev >= EDGE_MIN }
}

export interface ShopMarket { market: GameMarket; row: GamePriceRow; a: ShopSide; b: ShopSide }

/** The main line of one market with both sides' best prices; null when the game has no such market. */
export function shopMarket(g: GameBoardEntry, market: GameMarket): ShopMarket | null {
  const row = g.main[market]
  return row ? { market, row, a: shopSide(row, 'a'), b: shopSide(row, 'b') } : null
}

export interface EdgeItem { game: GameBoardEntry; market: GameMarket; line: number | null; side: 'a' | 'b'; shop: ShopSide; alt: boolean; outlier: boolean }

/** Every edge and outlier of every line of one game, biggest EV first. */
export function gameEdges(g: GameBoardEntry): EdgeItem[] {
  const out: EdgeItem[] = []
  for (const market of ['ml', 'spread', 'total'] as const) {
    const main = g.main[market]
    for (const r of g.rows[market]) {
      const alt = market !== 'ml' && main != null && r !== main
      for (const side of ['a', 'b'] as const) {
        const shop = shopSide(r, side)
        if (shop.actionable || shop.suspect) out.push({ game: g, market, line: r.row.line, side, shop, alt, outlier: shop.suspect })
      }
    }
  }
  return out.sort((x, y) => (y.shop.ev ?? 0) - (x.shop.ev ?? 0))
}

/**
 * The best edges across the slate (at most `perGame` from one game), biggest first. Clean edges and outliers are ranked separately and
 * outliers get at most `maxOutliers` of the places, so a slate of stale quotes cannot crowd out the real edges, and the real
 * mispricings are never hidden either.
 */
export function edgesNow(games: readonly GameBoardEntry[], max = 5, perGame = 1, maxOutliers = 2): EdgeItem[] {
  const clean: EdgeItem[] = [], outliers: EdgeItem[] = []
  for (const g of games) {
    const all = gameEdges(g)
    clean.push(...all.filter(e => !e.outlier).slice(0, perGame))
    outliers.push(...all.filter(e => e.outlier).slice(0, perGame))
  }
  const byEv = (x: EdgeItem, y: EdgeItem) => (y.shop.ev ?? 0) - (x.shop.ev ?? 0)
  clean.sort(byEv); outliers.sort(byEv)
  const o = outliers.slice(0, Math.min(maxOutliers, max))
  return [...clean.slice(0, max - o.length), ...o].sort(byEv)
}

/** One plain sentence for a game: its best clean edge, or an honest "nothing" (an edge or outlier on an alt line is named but not headlined as the game's). */
export function gameHeadline(g: GameBoardEntry): string {
  const edges = gameEdges(g)
  const pct = (x: number | null) => `${((x ?? 0) * 100).toFixed(1)}%`
  const fmt = (e: EdgeItem) => `${e.shop.label} ${e.shop.american != null && e.shop.american > 0 ? '+' : ''}${e.shop.american} at ${bookDisplay(e.shop.book).label}, ${pct(e.shop.ev)} against the market fair price`
  const main = edges.find(e => !e.alt && !e.outlier)
  if (main) return `Best edge: ${fmt(main)}.`
  const none = `No price beats the market fair price by ${(EDGE_MIN * 100).toFixed(0)}% or more on the main lines.`
  const alt = edges.find(e => e.alt && !e.outlier)
  const big = edges.find(e => e.outlier)
  const parts = [none]
  if (alt) parts.push(`On an alt line: ${fmt(alt)}.`)
  if (big) parts.push(`Big gap to check: ${fmt(big)}. Often a stale quote, sometimes a mispriced line: confirm the price at the book.`)
  return parts.join(' ')
}

export interface AltRow { line: number | null; row: GamePriceRow; a: ShopSide; b: ShopSide; thin: boolean; main: boolean }

/** Every line of a market with both sides' best prices, in ladder order (home side hardest cover first), main flagged. */
export function altRows(g: GameBoardEntry, market: GameMarket): AltRow[] {
  const main = g.main[market]
  return g.rows[market].map(r => ({ line: r.row.line, row: r, a: shopSide(r, 'a'), b: shopSide(r, 'b'), thin: (r.row.books ?? []).length < LADDER_THIN_BELOW, main: r === main }))
}

/** The one movement a game's card or row shows: the spread's (else the total's, else the moneyline's), and the side it is named from (home / over / home). */
export function primaryMove(g: GameBoardEntry): { m: MoveSummary; market: GameMarket; side: 'a' | 'b' } | null {
  for (const [market, side] of [['spread', 'b'], ['total', 'a'], ['ml', 'b']] as const) {
    const m = g.move[market]
    if (hasMovement(m)) return { m, market, side }
  }
  return null
}
