/**
 * Soccer game lines (/ev/games, sport=soccer): EPL + UCL match markets priced from ONE Dixon-Coles scoreline
 * matrix (soccer/game/run_daily.py). A soccer moneyline is a THREE-way market (home / draw / away): the two-way
 * game-odds machinery does not apply, so the 1X2 consensus here de-vigs all three legs together with
 * devigThreeWay (lib/ev-math.ts, the twin of mlb_core.odds.utils.devig_three_way).
 *
 * Everything is UNVALIDATED: model probabilities are the Dixon-Coles engine's, stamped validated: false by the
 * backend until the Pinnacle-close backtest and forward CLV clear the promotion bar.
 */
import { americanToImpliedProb } from '@/lib/odds'
import { americanToDecimal, devigThreeWay, type DevigThreeWayMethod } from '@/lib/ev-math'
import type { GameOddsFeed, GameOddsRow } from '@/lib/game-odds'

export interface SoccerTotalLine {
  line: number
  p_over: number | null
  p_under: number | null
  p_push: number
}

export interface SoccerPricedGame {
  game_key: string
  game_date: string
  league: string
  away: string
  home: string
  commence: string
  lambda_home: number
  lambda_away: number
  rho: number
  xg_variant: string
  p_home: number
  p_draw: number
  p_away: number
  p_1x: number
  p_x2: number
  p_12: number
  p_home_dnb: number | null
  p_away_dnb: number | null
  totals: Record<string, SoccerTotalLine>
  p_btts_yes: number
  p_btts_no: number
  handicaps: Record<string, number>
  matrix: number[][]
  validated: boolean
  sanity_warn?: string
}

export interface SoccerProjectionsFeed {
  sport: string
  status: string
  engine: string | null
  xg_variant?: string
  validated: boolean
  as_of: string | null
  age_minutes: number | null
  stale: boolean
  count: number
  priced_games: SoccerPricedGame[]
  games: { game_key: string; away: string; home: string; start: string; league?: string }[]
  markets: string[]
  locked?: number
}

/** The Nutmeg metric-card attribution shown wherever the soccer model is displayed. */
export const SOCCER_MODEL_BADGE = 'Model: Dixon-Coles Bivariate Poisson \u00b7 xG: understat_shot_v1 \u00b7 UNVALIDATED'

export interface ThreeWayBook {
  book: string
  home: number
  draw: number
  away: number
  fair: [number, number, number] | null
}

export interface ThreeWayConsensus {
  books: ThreeWayBook[]
  /** mean of the books' own three-way de-vigged triples, renormalized; null when no book quotes all three legs */
  fair: [number, number, number] | null
  nBooks: number
}

/** One board ml row (three-way prices per book) -> per-book fairs + the consensus fair triple. */
export function threeWayConsensus(row: GameOddsRow | undefined, method: DevigThreeWayMethod = 'power'): ThreeWayConsensus {
  const books: ThreeWayBook[] = []
  for (const b of row?.books ?? []) {
    if (b.a == null || b.b == null || b.draw == null) continue
    const fair = devigThreeWay(
      americanToImpliedProb(b.b), americanToImpliedProb(b.draw), americanToImpliedProb(b.a), method,
    )
    books.push({ book: b.book, home: b.b, draw: b.draw, away: b.a, fair })
  }
  const usable = books.filter(b => b.fair != null)
  let fair: [number, number, number] | null = null
  if (usable.length > 0) {
    const mean: [number, number, number] = [0, 0, 0]
    for (const b of usable) for (let i = 0; i < 3; i++) mean[i] += b.fair![i] / usable.length
    const s = mean[0] + mean[1] + mean[2]
    fair = [mean[0] / s, mean[1] / s, mean[2] / s]
  }
  return { books, fair, nBooks: usable.length }
}

/** Board rows of one game by market, for the deep-dive tabs. */
export function gameRows(feed: GameOddsFeed | null, gameKey: string): { ml?: GameOddsRow; totals: GameOddsRow[]; btts?: GameOddsRow } {
  const rows = (feed?.rows ?? []).filter(r => r.game_key === gameKey)
  return {
    ml: rows.find(r => r.market === 'ml'),
    totals: rows.filter(r => r.market === 'total').sort((x, y) => (x.line ?? 0) - (y.line ?? 0)),
    btts: rows.find(r => r.market === 'btts'),
  }
}

export type ThreeWayLeg = 'home' | 'draw' | 'away'
export interface BestPrice { book: string; price: number }

/** The best (highest-payout) price per 1X2 leg across the consensus books; ties keep every tied book's price
 *  equal, so the caller highlights all books whose price matches the best for the leg. */
export function bestThreeWayPrices(consensus: ThreeWayConsensus): Record<ThreeWayLeg, BestPrice | null> {
  const best: Record<ThreeWayLeg, BestPrice | null> = { home: null, draw: null, away: null }
  for (const b of consensus.books) {
    for (const leg of ['home', 'draw', 'away'] as ThreeWayLeg[]) {
      const cur = best[leg]
      if (cur == null || americanToDecimal(b[leg]) > americanToDecimal(cur.price)) best[leg] = { book: b.book, price: b[leg] }
    }
  }
  return best
}

/** Expected value of a price against a fair probability: fair * decimal - 1 (0.032 = +3.2%). */
export function evVsFair(price: number, fairProb: number): number {
  return fairProb * americanToDecimal(price) - 1
}

/** Best OVER / UNDER book prices for one totals line out of a game's total rows (a = over, b = under). */
export function bestTotalPrices(totals: GameOddsRow[], line: number): { over: BestPrice | null; under: BestPrice | null } {
  const row = totals.find(r => r.line != null && Math.abs(r.line - line) < 1e-9)
  let over: BestPrice | null = null
  let under: BestPrice | null = null
  for (const b of row?.books ?? []) {
    if (b.a != null && (over == null || americanToDecimal(b.a) > americanToDecimal(over.price))) over = { book: b.book, price: b.a }
    if (b.b != null && (under == null || americanToDecimal(b.b) > americanToDecimal(under.price))) under = { book: b.book, price: b.b }
  }
  return { over, under }
}

export interface OddsOnlyGame {
  game_key: string
  away: string
  home: string
  start: string
  consensus: ThreeWayConsensus
}

/** Fallback board when the model has priced no fixtures yet but the odds feed has live soccer rows: one entry
 *  per odds game with a 1X2 row, carrying its three-way consensus, so /ev/games?sport=soccer still shows the
 *  live book prices instead of an empty state. */
export function oddsOnlyGames(feed: GameOddsFeed | null): OddsOnlyGame[] {
  if (!feed) return []
  const out: OddsOnlyGame[] = []
  for (const g of feed.games ?? []) {
    const rows = gameRows(feed, g.game_key)
    if (!rows.ml) continue
    out.push({ game_key: g.game_key, away: g.away, home: g.home, start: g.start, consensus: threeWayConsensus(rows.ml, 'power') })
  }
  return out.sort((x, y) => (x.start < y.start ? -1 : x.start > y.start ? 1 : 0))
}
