/**
 * Top picks across sports: one ranked list built from the tracked MLB card (model picks + +EV alerts) and the NFL / NHL /
 * NBA projection boards. Everything is scored on the same Beezy Score scale and labelled by where it came from, so a
 * tracked MLB pick is never presented as if it were a validated projection.
 */
import { betTier, isWinterSport, notesBookCount, parseNotesJson } from '@/lib/bet-notes'
import { isOffshore } from '@/lib/offshore'
import { beezyscore } from '@/lib/beezy-score'
import { collapseBooks } from '@/lib/dedupe'
import { betKind, type PickKind } from '@/lib/pick-systems'
import { propStatLabel, type SportSlug } from '@/lib/sports'
import { matchesBook, matchesSignal, matchesTier, type PickFilters, type PickSort } from '@/lib/picks-filters'
import { matchesSearch, searchHaystack } from '@/lib/team-directory'
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
  /** the player's own team code (projection rows only); disambiguates headshots for shared names */
  team?: string | null
  /** a player prop pick (not a game pick): the table shows an avatar for these */
  isPlayer?: boolean
  /** resolved server-side by withHeadshotIds(); null = no photo, render the silhouette */
  headshotId?: number | string | null
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
  nBooks?: number | null
  confluence?: boolean
  modelEdge?: number | null
  marketEv?: number | null
  /** a tracked NFL / NBA / NHL bet: it settles in the ledger like MLB but the model behind it has no tested edge, so it is labelled paper / unvalidated */
  unvalidated?: boolean
  /** NFL / NBA / NHL: the backend's verdict (ev/model_picks.py): 'official' = a daily play. A ledger bet reads it from its notes, a board row from `pick_tier`. */
  tier?: 'official' | 'tracked' | null
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
  // A winter bet (NFL / NBA / NHL: the model's own 'PROPS' picks and the sweep's 'EV' alerts) keeps ITS sport. It was hard-coded
  // 'mlb', which sent winter picks through the MLB official-card branch of isOfficialCardPlay (validated model, no confluence needed).
  const winter = isWinterSport(bet.sport)
  const sport: SportSlug = winter ? (bet.sport!.toLowerCase() as SportSlug) : 'mlb'
  // The backend's own gate is the tier in the notes: an 'official' model pick passed the model + market confluence gate.
  const confluence = winter ? bet.system === 'PROPS' && betTier(bet) === 'official' : bet.system !== 'EV'
  return {
    key: `${sport}|${bet.id}`,
    sport,
    who,
    what,
    isPlayer: !!bet.player,
    game: away && home ? `${away} @ ${home}` : '',
    odds: bet.odds,
    book: bet.book,
    edge: edgeFraction(bet.edge),
    score: beezyscore(bet),
    basis: bet.system === 'EV' ? 'ev' : 'model',      // 'PROPS' (the model's own winter picks) is a model basis, never 'ev'
    tracked: true,
    unvalidated: winter,
    result: bet.result,
    bet,
    nBooks: winter ? notesBookCount(bet.notes) : undefined,
    confluence,
    tier: winter && bet.system === 'PROPS' ? betTier(bet) : null,
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
  if (isOffshore(over ? r.best_over_book : r.best_under_book)) return null // never point at an offshore book
  return {
    key: `${r.sport}|${r.game_key}|${r.player_norm}|${r.stat}`,
    sport: r.sport,
    who: r.player,
    what: `${over ? 'Over' : 'Under'} ${r.line} ${r.stat_label || propStatLabel(r.stat)}`,
    team: r.team ?? null,
    isPlayer: true,
    game: r.team && r.opp ? `${r.team} v ${r.opp}` : (r.game_key.split(':')[2] ?? '').replace('@', ' @ '),
    odds,
    book: over ? r.best_over_book : r.best_under_book,
    edge: projectionEdge(r),
    score: r.beezy_score,
    basis: r.confluence ? 'confluence' : 'projection',
    tracked: false,
    result: null,
    nBooks: r.n_books ?? null,
    confluence: !!r.confluence,
    modelEdge: r.model_edge ?? null,
    marketEv: r.market_ev ?? null,
    tier: r.pick_tier ?? null,
  }
}

/**
 * A daily play (the Official Card gate).
 * - NFL / NHL / NBA: the backend's own official tier (ev/model_picks.py `qualifies`): the card gate (confluence, 4+ books, 3%-10% EV,
 *   model edge >= 1.5%, score >= 68) PLUS a stat with measured signal, a blended EV >= 2.5% and no sharp book against the side. Those
 *   last checks live only in Python, so the site reads the verdict (`tier`) rather than re-deriving a weaker copy of it. No stamp, no play.
 * - MLB: validated != false, 3%-10% edge, score >= 68.
 */
export function isOfficialCardPlay(p: TopPick): boolean {
  if (p.sport !== 'mlb') return p.tier === 'official'
  const edge = p.edge ?? 0
  if (edge < 0.03 || edge > 0.10) return false
  if (p.score < 68) return false
  if (p.bet && p.bet.validated === false) return false
  return true
}

/** Tier 2: Live +EV Outliers (market EV >= 3% without confluence or sitting outside card cap) */
export function isEvOutlier(p: TopPick): boolean {
  const edge = p.edge ?? 0
  if (edge < 0.025 || edge > 0.15) return false
  if (p.nBooks != null && p.nBooks < 3) return false
  return !isOfficialCardPlay(p)
}

/**
 * The Daily Card: every official play, no count cap (a slate with nine good plays has nine; one with none has none, and it is never padded).
 * Same concentration rules as ev/daily_card.py: one play per player (the highest score), at most two per game. Today's card and every past
 * day in Graded History run through this one function, so a day's record is exactly the card that was shown.
 */
export function buildDailyCard(picks: TopPick[]): TopPick[] {
  const qualified = picks.filter(isOfficialCardPlay)

  // Max 1 play per player (highest score)
  const byPlayer = new Map<string, TopPick>()
  for (const p of qualified) {
    const key = (p.who || p.key).trim().toLowerCase()
    const curr = byPlayer.get(key)
    if (!curr || p.score > curr.score || (p.score === curr.score && (p.edge ?? 0) > (curr.edge ?? 0))) {
      byPlayer.set(key, p)
    }
  }

  const playerDeduped = Array.from(byPlayer.values())
  playerDeduped.sort((a, b) =>
    (Number(b.confluence || b.basis === 'confluence') - Number(a.confluence || a.basis === 'confluence')) ||
    (b.score - a.score) ||
    ((b.nBooks ?? 0) - (a.nBooks ?? 0)) ||
    ((b.edge ?? 0) - (a.edge ?? 0))
  )

  // Max 2 plays per game
  const gameCounts = new Map<string, number>()
  const gameDeduped: TopPick[] = []
  for (const p of playerDeduped) {
    const g = (p.game || '').trim().toLowerCase()
    if (!g) {
      gameDeduped.push(p)
      continue
    }
    const c = gameCounts.get(g) ?? 0
    if (c < 2) {
      gameCounts.set(g, c + 1)
      gameDeduped.push(p)
    }
  }

  return gameDeduped
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

/**
 * The daily plays in a day's tracked bets: MLB model picks and the model's own NFL / NHL / NBA 'PROPS' picks, one row per prop at the best
 * book, run through the same Daily Card rules as today's card. Used for a past day's Graded History and for today's already-logged plays.
 * Book-outlier alerts ('EV') are not plays. Game picks are not part of the card.
 */
export function dailyPlaysFromBets(bets: Bet[]): TopPick[] {
  const picks = collapseBooks(bets)
    .filter(b => b.kelly_triggered && b.system !== 'EV' && betKind(b) === 'player')
    .map(fromBet)
  return buildDailyCard(picks)
}

/** The `row_key` the backend wrote into a PROPS bet's notes ('game_key|player_norm|stat'), or null: the id a board row and its logged bet share. */
export function ledgerRowKey(bet: Bet): string | null {
  const k = parseNotesJson(bet.notes)?.row_key
  return typeof k === 'string' && k ? `${(bet.sport ?? '').toLowerCase()}|${k}` : null
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

/**
 * Apply the shared picks filters to the cross-sport candidates, then re-rank and keep the top `n` of what is left, so
 * "NFL" shows the ten best NFL picks rather than the NFL rows that happened to be in the overall ten.
 */
export function filterTopPicks(picks: TopPick[], f: PickFilters, n = TOP_N): TopPick[] {
  const kept = picks.filter(p =>
    (!f.sport || p.sport === f.sport) &&
    matchesTier(p.score, f.tier) &&
    matchesSignal({ score: p.score, hasLine: p.odds != null, confluence: false, edge: p.edge }, f.signal) &&
    matchesBook(p.book, f) &&
    matchesSearch(searchHaystack(p.sport, p.who, p.what, p.game), f.q))
  const ranked = rankTopPicks(kept, n)
  // A cross-sport pick carries no model probability or start time, so those two sorts fall back to the score.
  const key: Partial<Record<PickSort, (p: TopPick) => number>> = { edge: p => p.edge ?? -9, odds: p => p.odds ?? -9999 }
  const k = key[f.sort] ?? ((p: TopPick) => p.score)
  const mul = f.dir === 'asc' ? -1 : 1
  return k === key.edge || k === key.odds || f.dir === 'asc' ? [...ranked].sort((a, b) => mul * (k(b) - k(a))) : ranked
}
