/**
 * What a bettor does with a game: shop the best number, spot a real edge, see what is moving. Pure functions over the board entries
 * (lib/game-board.ts), no React, so the row cells, the popup card and the "edges now" strip share one definition of "actionable".
 *
 * An edge is only ever called actionable when it is against the MARKET fair price (never our unvalidated model), on a line with enough
 * books to have one, at a book the viewer can bet at, and not so large that it is more likely a stale quote.
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
/** An alternate line needs this many books before its fair price is trusted (the main line needs LADDER_THIN_BELOW). */
export const ALT_MIN_BOOKS = 5
/**
 * No price longer than this is ever called an edge. De-vigging in proportion to the odds inflates the fair chance of a longshot, so
 * long alternate lines (+500, +900) look +EV almost everywhere; real edges at those prices cannot be told from that artifact.
 * The price is still shown, just never headlined.
 */
export const LONGSHOT_MAX_AMERICAN = 300

export interface ShopSide {
  /** 'FLA -1.5', 'O 6.5', 'DET ML' */
  label: string
  american: number | null
  book: string | null
  ev: number | null
  /** the next best prices at other books (up to three), best first */
  others: { book: string; american: number }[]
  /** EV >= EDGE_MIN against the market fair price on a line with enough books, and not suspiciously large */
  actionable: boolean
  /** EV above LADDER_SUSPECT_EV: more likely a stale quote than an edge */
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

export function shopSide(r: GamePriceRow, which: 'a' | 'b', opts: { alt?: boolean } = {}): ShopSide {
  const sp = which === 'a' ? r.a : r.b
  const label = sideLabels(r.row)[which]
  const others = (r.row.books ?? [])
    .filter(b => !isOffshore(b.book) && b.book !== sp.book)
    .map(b => ({ book: b.book, american: which === 'a' ? b.a : b.b }))
    .filter((x): x is { book: string; american: number } => x.american != null)
    .sort((x, y) => decimalOf(y.american) - decimalOf(x.american) || x.book.localeCompare(y.book))
    .slice(0, 3)
  const ev = sp.ev
  const enough = (r.row.books ?? []).length >= (opts.alt ? ALT_MIN_BOOKS : LADDER_THIN_BELOW)
  const suspect = ev != null && ev > LADDER_SUSPECT_EV
  const short = sp.american != null && sp.american <= LONGSHOT_MAX_AMERICAN
  const med = sp.american != null ? medianFair(r, which, sp.book) : null
  const evMedian = med != null && sp.american != null ? med * decimalOf(sp.american) - 1 : null
  const robust = evMedian != null && evMedian >= EDGE_MIN
  return { label, american: sp.american, book: sp.book, ev, others, suspect, actionable: ev != null && enough && short && robust && !suspect && ev >= EDGE_MIN }
}

export interface ShopMarket { market: GameMarket; row: GamePriceRow; a: ShopSide; b: ShopSide }

/** The main line of one market with both sides' best prices; null when the game has no such market. */
export function shopMarket(g: GameBoardEntry, market: GameMarket): ShopMarket | null {
  const row = g.main[market]
  return row ? { market, row, a: shopSide(row, 'a'), b: shopSide(row, 'b') } : null
}

export interface EdgeItem { game: GameBoardEntry; market: GameMarket; line: number | null; side: 'a' | 'b'; shop: ShopSide; alt: boolean }

/** Every actionable side of every line of one game, biggest EV first. */
export function gameEdges(g: GameBoardEntry): EdgeItem[] {
  const out: EdgeItem[] = []
  for (const market of ['ml', 'spread', 'total'] as const) {
    const main = g.main[market]
    for (const r of g.rows[market]) {
      const alt = market !== 'ml' && main != null && r !== main
      for (const side of ['a', 'b'] as const) {
        const shop = shopSide(r, side, { alt })
        if (shop.actionable) out.push({ game: g, market, line: r.row.line, side, shop, alt })
      }
    }
  }
  return out.sort((x, y) => (y.shop.ev ?? 0) - (x.shop.ev ?? 0))
}

/** The best actionable edges across the slate (at most `perGame` from one game), biggest first. */
export function edgesNow(games: readonly GameBoardEntry[], max = 5, perGame = 1): EdgeItem[] {
  const all: EdgeItem[] = []
  for (const g of games) all.push(...gameEdges(g).slice(0, perGame))
  return all.sort((x, y) => (y.shop.ev ?? 0) - (x.shop.ev ?? 0)).slice(0, max)
}

/** One plain sentence for a game: its best edge, or an honest "nothing" (an alt-line edge is named but not headlined as the game's). */
export function gameHeadline(g: GameBoardEntry): string {
  const edges = gameEdges(g)
  const main = edges.find(e => !e.alt)
  const pct = (x: number | null) => `${((x ?? 0) * 100).toFixed(1)}%`
  const fmt = (e: EdgeItem) => `${e.shop.label} ${e.shop.american != null && e.shop.american > 0 ? '+' : ''}${e.shop.american} at ${bookDisplay(e.shop.book).label}, ${pct(e.shop.ev)} against the market fair price`
  if (main) return `Best edge: ${fmt(main)}.`
  const alt = edges.find(e => e.alt)
  const none = `No price beats the market fair price by ${(EDGE_MIN * 100).toFixed(0)}% or more on the main lines.`
  return alt ? `${none} On an alt line: ${fmt(alt)}.` : none
}

export interface AltRow { line: number | null; row: GamePriceRow; a: ShopSide; b: ShopSide; thin: boolean; main: boolean }

/** Every line of a market with both sides' best prices, in ladder order (home side hardest cover first), main flagged. */
export function altRows(g: GameBoardEntry, market: GameMarket): AltRow[] {
  const main = g.main[market]
  return g.rows[market].map(r => {
    const alt = market !== 'ml' && r !== main
    return { line: r.row.line, row: r, a: shopSide(r, 'a', { alt }), b: shopSide(r, 'b', { alt }), thin: (r.row.books ?? []).length < LADDER_THIN_BELOW, main: r === main }
  })
}

/** The one movement a game's card or row shows: the spread's (else the total's, else the moneyline's), and the side it is named from (home / over / home). */
export function primaryMove(g: GameBoardEntry): { m: MoveSummary; market: GameMarket; side: 'a' | 'b' } | null {
  for (const [market, side] of [['spread', 'b'], ['total', 'a'], ['ml', 'b']] as const) {
    const m = g.move[market]
    if (hasMovement(m)) return { m, market, side }
  }
  return null
}
