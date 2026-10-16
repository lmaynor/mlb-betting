/**
 * Top picks across sports: one ranked list built from the tracked MLB card (model picks + +EV alerts) and the NFL / NHL /
 * NBA projection boards. Everything is scored on the same Beezy Score scale and labelled by where it came from, so a
 * tracked MLB pick is never presented as if it were a validated projection.
 */
import { beezyscore } from '@/lib/beezy-score'
import { collapseBooks } from '@/lib/dedupe'
import { betKind, type PickKind } from '@/lib/pick-systems'
import { propStatLabel, type SportSlug } from '@/lib/sports'
import { pickLabel } from '@/lib/tokens'
import type { Bet, SportProjectionRow } from '@/lib/types'

export const TOP_N = 10

/** tracked = MLB rows that are logged to the public ledger and settle; projection = unvalidated board rows. */
export type PickBasis = 'model' | 'ev' | 'projection' | 'confluence'

export interface TopPick {
  key: string
  sport: SportSlug
  /** player (or team) first, then the market and side: "Aaron Judge", "Over 1.5 Total Bases" */
  who: string
  what: string
  game: string
  odds: number | null
  book: string | null
  /** fraction (0.08), not percent */
  edge: number | null
  score: number
  basis: PickBasis
  tracked: boolean
  /** result of the pick when it is already graded (history) */
  result: Bet['result']
  /** the underlying bet, for tracked MLB rows */
  bet?: Bet
}

const edgeFraction = (e: number | null | undefined): number | null => {
  if (e == null) return null
  return Math.abs(e) < 2 ? e : e / 100
}

export function fromBet(bet: Bet): TopPick {
  const label = pickLabel(bet)
  const player = bet.player ?? ''
  // pickLabel leads with the player for props; split it so the table can show who and what separately.
  const idx = player ? label.indexOf(player) : -1
  const rest = idx >= 0 ? (label.slice(0, idx) + label.slice(idx + player.length)).replace(/^\s*[-:]\s*/, '').replace(/\s*\([^)]*\)\s*/, ' ').trim() : ''
  const who = player || label
  const what = player ? (rest || label) : ''
  const away = bet.away_team ?? ''
  const home = bet.home_team ?? ''
  return {
    key: `mlb|${bet.id}`,
    sport: 'mlb',
    who,
    what,
    game: away && home ? `${away} @ ${home}` : '',
    odds: bet.odds,
    book: bet.book,
    edge: edgeFraction(bet.edge),
    score: beezyscore(bet),
    basis: bet.system === 'EV' ? 'ev' : 'model',
    tracked: true,
    result: bet.result,
    bet,
  }
}

/** The edge the Beezy Score was built on: the smaller of the two when model and market agree, else the one that exists. */
function projectionEdge(r: SportProjectionRow): number | null {
  const m = r.model_edge, e = r.market_ev
  if (r.score_basis === 'confluence' && m != null && e != null) return Math.min(m, e)
  if (r.score_basis === 'market') return e ?? m
  return m ?? e
}

export function fromProjection(r: SportProjectionRow): TopPick | null {
  if (!r.has_line || r.side == null || r.line == null || r.beezy_score <= 0) return null
  if (r.mins_to_start != null && r.mins_to_start <= 0) return null // already started
  const over = r.side === 'over'
  const odds = over ? r.best_over_odds : r.best_under_odds
  if (odds == null) return null
  return {
    key: `${r.sport}|${r.game_key}|${r.player_norm}|${r.stat}`,
    sport: r.sport,
    who: r.player,
    what: `${over ? 'Over' : 'Under'} ${r.line} ${r.stat_label || propStatLabel(r.stat)}`,
    game: r.team && r.opp ? `${r.team} v ${r.opp}` : (r.game_key.split(':')[2] ?? '').replace('@', ' @ '),
    odds,
    book: over ? r.best_over_book : r.best_under_book,
    edge: projectionEdge(r),
    score: r.beezy_score,
    basis: r.confluence ? 'confluence' : 'projection',
    tracked: false,
    result: null,
  }
}

/**
 * Highest score first. Ties break on larger edge, then tracked picks ahead of projections (they settle in public),
 * then key, so the order is stable between renders.
 */
export function rankTopPicks(picks: TopPick[], n = TOP_N): TopPick[] {
  return [...picks]
    .sort((a, b) =>
      b.score - a.score ||
      (b.edge ?? -1) - (a.edge ?? -1) ||
      Number(b.tracked) - Number(a.tracked) ||
      a.key.localeCompare(b.key))
    .slice(0, n)
}

/** Tracked MLB bets (one row per prop, best book) -> ranked top picks. The Top 10 is player picks; game picks have their own page. */
export function topFromBets(bets: Bet[], n = TOP_N, kind: PickKind = 'player'): TopPick[] {
  return rankTopPicks(collapseBooks(bets).filter(b => b.kelly_triggered && betKind(b) === kind).map(fromBet), n)
}

/** Flat 1-unit profit from the American odds: a win pays the price, a loss is -1, push / void / pending are null. */
export function flatUnits(odds: number, result: Bet['result']): number | null {
  if (result === 'loss') return -1
  if (result !== 'win') return null
  return odds > 0 ? odds / 100 : 100 / -odds
}

export interface Record1u { wins: number; losses: number; pushes: number; graded: number; pending: number; units: number }

export function recordOf(picks: Pick<TopPick, 'odds' | 'result'>[]): Record1u {
  const r: Record1u = { wins: 0, losses: 0, pushes: 0, graded: 0, pending: 0, units: 0 }
  for (const p of picks) {
    if (p.result === 'win') r.wins++
    else if (p.result === 'loss') r.losses++
    else if (p.result === 'push') r.pushes++
    else if (p.result == null) { r.pending++; continue }
    else continue // void
    r.graded++
    const u = p.odds == null ? null : flatUnits(p.odds, p.result)
    if (u != null) r.units += u
  }
  return r
}
