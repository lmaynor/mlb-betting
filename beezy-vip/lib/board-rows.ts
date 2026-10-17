/**
 * One row shape for the projections board of EVERY sport. The MLB board is built from today's tracked predictions
 * (lib/mlb-projections.ts) and the NFL / NHL / NBA board from the daily projection feed (ev/daily_projections.py); the
 * adapters below put both into the same columns so a single component renders them with the same density.
 */
import { getSystemByStat } from '@/lib/pick-systems'
import type { MlbProjectionRow } from '@/lib/mlb-projections'
import { probToAmerican } from '@/lib/odds'
import { matchesBook, matchesTier, type PickFilters, type PickSort } from '@/lib/picks-filters'
import { matchesSearch, searchHaystack } from '@/lib/team-directory'
import type { SportSlug } from '@/lib/sports'
import { playerSlug } from '@/lib/sport-assets'
import type { SportProjectionRow, SportProjections } from '@/lib/types'

export interface BoardRow {
  key: string
  sport: SportSlug
  /** registry key of the system (lib/pick-systems.ts): the market chip this row belongs to */
  system: string
  marketLabel: string
  player: string
  headshotId: number | string | null
  /** 'BOS @ LAL', or '' when unknown */
  matchup: string
  /** ISO game start, or null */
  start: string | null
  side: 'over' | 'under' | null
  line: number | null
  hasLine: boolean
  proj: number | null
  /** the model's probability for the listed side, 0-1 */
  pModel: number | null
  /** the market's de-vigged consensus probability for the listed side, 0-1 */
  pFair: number | null
  nBooks: number | null
  /** share of the last 5 / 10 games that cleared the listed side, 0-1 */
  l5: number | null
  l10: number | null
  odds: number | null
  book: string | null
  modelEdge: number | null
  marketEv: number | null
  confluence: boolean
  score: number
  /** MLB only: cleared the edge and Kelly gates and is tracked at a stake */
  isPick: boolean
}

/** Hit rates are stored for the OVER; for an under pick the share that stayed under is the complement. */
const forSide = (rate: number | null | undefined, side: 'over' | 'under' | null) =>
  rate == null ? null : side === 'under' ? 1 - rate : rate

export function fromSportProjections(rows: SportProjectionRow[]): BoardRow[] {
  return rows.map(r => {
    const side = r.side
    const sys = getSystemByStat(r.sport, r.stat)
    return {
      key: `${r.game_key}|${r.player_norm}|${r.stat}`,
      sport: r.sport,
      system: sys?.key ?? r.stat,
      marketLabel: r.stat_label,
      player: r.player,
      headshotId: r.headshot_id ?? null,
      matchup: r.team && r.opp ? `${r.team} v ${r.opp}` : (r.game_key.split(':')[2] ?? '').replace('@', ' @ '),
      start: r.commence,
      side,
      line: r.has_line ? r.line : null,
      hasLine: r.has_line,
      proj: r.proj_mean,
      pModel: r.has_line ? r.p_model_side : null,
      pFair: r.has_line ? r.p_mkt_side : null,
      nBooks: r.n_books,
      l5: r.has_line ? forSide(r.l5_hit_rate, side) : null,
      l10: r.has_line ? forSide(r.l10_hit_rate, side) : null,
      odds: side === 'under' ? r.best_under_odds : r.best_over_odds,
      book: side === 'under' ? r.best_under_book : r.best_over_book,
      modelEdge: r.model_edge,
      marketEv: r.market_ev,
      confluence: r.confluence,
      score: r.has_line ? r.beezy_score : 0,
      isPick: false,
    }
  })
}

export function fromMlbProjections(rows: MlbProjectionRow[]): BoardRow[] {
  return rows.map(r => ({
    key: r.key,
    sport: 'mlb' as const,
    system: r.system,
    marketLabel: r.marketLabel,
    player: r.player,
    headshotId: null,
    matchup: r.matchup,
    start: null,
    side: r.side === 'UNDER' ? 'under' as const : 'over' as const,
    line: r.line,
    hasLine: true,
    proj: r.proj,
    pModel: r.pModel,
    // the edge is model probability minus the de-vigged market, so the market's own number is the difference
    pFair: r.pModel != null && r.edge != null ? r.pModel - r.edge : null,
    nBooks: null,
    l5: null,
    l10: null,
    odds: r.odds,
    book: r.book,
    modelEdge: r.edge,
    marketEv: null,
    confluence: false,
    score: r.score,
    isPick: r.isPick,
  }))
}

export type BoardSort = 'score' | 'modelEdge' | 'marketEv' | 'l10'

export const BOARD_SORT_LABEL: Record<BoardSort, string> = {
  score: 'Beezy Score', modelEdge: 'Model Edge %', marketEv: 'Market EV %', l10: 'L10 Hit %',
}

const KEY: Record<BoardSort, (r: BoardRow) => number> = {
  score: r => r.score, modelEdge: r => r.modelEdge ?? -9, marketEv: r => r.marketEv ?? -9, l10: r => r.l10 ?? -1,
}

export interface BoardFilter {
  market: string
  side: 'ALL' | 'OVER' | 'UNDER'
  signal: 'ALL' | 'CONFLUENCE' | 'EV'
  q: string
  sort: BoardSort
}

/** Pure filter + sort so the board's behavior is testable without rendering. Ties keep the incoming (score) order. */
export function filterBoard(rows: BoardRow[], f: BoardFilter): BoardRow[] {
  const q = f.q.trim().toLowerCase()
  const out = rows.filter(r => {
    if (f.market !== 'ALL' && r.system !== f.market) return false
    if (f.side !== 'ALL' && r.side !== f.side.toLowerCase()) return false
    if (f.signal === 'CONFLUENCE' && !r.confluence) return false
    if (f.signal === 'EV' && !((r.marketEv ?? 0) > 0 || (r.modelEdge ?? 0) > 0)) return false
    if (q && !matchesSearch(searchHaystack(r.sport, r.player, r.matchup), q)) return false
    return true
  })
  const k = KEY[f.sort]
  return out.map((r, i) => ({ r, i })).sort((a, b) => k(b.r) - k(a.r) || a.i - b.i).map(x => x.r)
}

export const fairAmerican = (p: number | null) => (p == null || p <= 0 || p >= 1 ? null : probToAmerican(p))

/** Rows that carry a signal worth listing as a pick: a posted line and a positive Beezy Score. */
export const pickRows = (rows: BoardRow[]): BoardRow[] => rows.filter(r => r.hasLine && r.score > 0)

// a game with no start time sorts last: the largest valid JS date, so the subtraction in the comparator stays finite
const START_MS = (r: BoardRow) => { const t = r.start ? new Date(r.start).getTime() : NaN; return Number.isFinite(t) ? t : 8.64e15 }

// Descending order of each key. TIME is negated so the default (desc) shows the soonest game first.
const PICK_KEY: Record<PickSort, (r: BoardRow) => number> = {
  score: r => r.score,
  edge: r => r.modelEdge ?? r.marketEv ?? -9,
  prob: r => r.pModel ?? -1,
  odds: r => r.odds ?? -99999,
  date: r => -START_MS(r),
}

/** The query-string filters (lib/picks-filters.ts) applied to a board's rows, for the picks pages of the projection sports. */
export function applyPickFilters(rows: BoardRow[], f: PickFilters): BoardRow[] {
  const kept = rows.filter(r =>
    (!f.market || r.system === f.market) &&
    matchesTier(r.score, f.tier) &&
    matchesBook(r.book, f) &&
    matchesSearch(searchHaystack(r.sport, r.player, r.matchup), f.q))
  const k = PICK_KEY[f.sort]
  const mul = f.dir === 'asc' ? -1 : 1
  return kept.map((r, i) => ({ r, i })).sort((a, b) => mul * (k(b.r) - k(a.r)) || a.i - b.i).map(x => x.r)
}

/** The price the model's side would be struck at (best over / best under across books), or the best over when there is no side. */
export function sidePrice(r: SportProjectionRow): { odds: number | null; book: string | null; side: 'over' | 'under' } {
  const side = r.side ?? 'over'
  return side === 'over'
    ? { odds: r.best_over_odds, book: r.best_over_book, side }
    : { odds: r.best_under_odds, book: r.best_under_book, side }
}

/** 45 -> '45 min', 600 -> '10.0 h'. */
export const ageText = (min: number | null | undefined) => (min == null ? 'some time' : min < 90 ? `${Math.round(min)} min` : `${(min / 60).toFixed(1)} h`)

/** Plain-language freshness line: what the user is looking at and how old it is. */
export function boardStatusText(d: SportProjections): string {
  if (d.status === 'unavailable') return 'Projections are not available right now.'
  const parts: string[] = []
  if (d.status === 'live' && d.lines_stale) parts.push(`Lines are ${ageText(d.lines_age_minutes)} old (books are polled twice a day): check the price at the book before betting`)
  else if (d.status === 'live') parts.push(d.lines_age_minutes != null ? `Lines updated ${Math.round(d.lines_age_minutes)} min ago` : 'Lines live')
  else if (d.status === 'awaiting_lines') parts.push('Lines have not posted yet: projections only')
  else parts.push('No games on the schedule')
  if (d.stale_gamelog) parts.push(`game logs end ${d.gamelog_latest ?? 'last season'} (off-season carry-over)`)
  return parts.join(' \u00b7 ')
}

/** Key a market-price row and a projection row share when they are the same player, stat and side. */
export const confluenceKey = (player: string, stat: string, side: string) => `${playerSlug(player)}|${stat}|${side.toLowerCase()}`

/** What the board holds today for one system: rows, rows with a posted line, rows where model and market agree. */
export interface SystemToday { rows: number; priced: number; confluence: number }

/** Counts per registry system (by key), from the UNCUT feed rows, so the figures do not depend on the viewer's access. */
export function systemTodayCounts(rows: SportProjectionRow[]): Record<string, SystemToday> {
  const out: Record<string, SystemToday> = {}
  for (const r of rows) {
    const key = getSystemByStat(r.sport, r.stat)?.key ?? r.stat
    const c = out[key] ?? (out[key] = { rows: 0, priced: 0, confluence: 0 })
    c.rows += 1
    if (r.has_line) c.priced += 1
    if (r.confluence) c.confluence += 1
  }
  return out
}
