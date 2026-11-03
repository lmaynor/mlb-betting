/**
 * Server-side loader for the cross-sport Top 10 player picks and the Daily Card. One place, so /picks, the Daily Card and Graded History can
 * never disagree about what a day's list is. The Top 10 itself is the free tier, so nothing here is cut by the access policy.
 *
 * The Daily Card is the day's OFFICIAL plays: the backend's verdict (ev/model_picks.py, stamped on a board row as `pick_tier` and written
 * into the ledger bet's notes as `tier`), with no count cap. Today's card is the plays already logged to the ledger (they keep their result
 * after the game starts, when the board drops the row) plus any official board row not logged yet (the logging job runs hourly). A past
 * day is its ledger bets run through the same rules, which is what Graded History shows.
 */
import { apiGetPicks, apiGetSportProjections } from '@/lib/betting-api'
import { LIVE_SPORTS, type SportSlug } from '@/lib/sports'
import {
  buildDailyCard, dailyPlaysFromBets, fromProjection, ledgerRowKey, rankTopPicks, recordOf, TOP_N, topFromBets,
  type Record1u, type TopPick,
} from '@/lib/top-picks'
import { withHeadshotIds } from '@/lib/sport-assets.server'
import type { Bet, SportProjections } from '@/lib/types'

/** Every live sport whose board is a projection feed (lib/sports.ts `board`): a new sport joins the Top 10 by registry entry. */
const PROJECTION_SPORTS = LIVE_SPORTS.filter(p => p.board === 'projection').map(p => p.slug) as Exclude<SportSlug, 'mlb'>[]

/**
 * One day's tracked bets the Daily Card can be built from: the MLB card plus the model's own NFL / NHL / NBA 'PROPS' picks. Two requests
 * because the API's default sport is MLB and a busy MLB day (every +EV alert is a row) must not crowd the PROPS rows out of one page.
 */
export async function loadLedgerBets(date: string): Promise<Bet[]> {
  const [mlb, props] = await Promise.all([
    apiGetPicks({ date, limit: 200 }).catch(() => [] as Bet[]),
    apiGetPicks({ date, system: 'PROPS', sport: 'all', limit: 200 }).catch(() => [] as Bet[]),
  ])
  return [...mlb, ...props]
}

export async function loadTopPlayerPicks(date: string): Promise<{ top: TopPick[]; candidates: TopPick[]; mlbToday: Bet[]; dailyCard: TopPick[] }> {
  const [ledger, ...boards] = await Promise.all([
    loadLedgerBets(date),
    ...PROJECTION_SPORTS.map(s => apiGetSportProjections(s, { limit: 600 }).catch(() => null as SportProjections | null)),
  ])
  const mlbToday = ledger.filter(b => b.system !== 'PROPS')
  const projected = boards.flatMap(b => (b?.rows ?? []).map(fromProjection).filter((p): p is TopPick => p != null))
  // `candidates` is every scored pick (the filters on /picks cut from it); `top` is the unfiltered ten.
  const candidates = withHeadshotIds(rankTopPicks([...topFromBets(mlbToday, 1000), ...projected], 1000))
  return { top: candidates.slice(0, TOP_N), candidates, mlbToday, dailyCard: todaysDailyCard(ledger, projected) }
}

/**
 * Today's Daily Card: official plays already in the ledger (graded when their game ends) plus official board rows the ledger has not
 * caught up with yet. A board row and its logged bet are the same play when they share the `row_key`, so a play is never listed twice.
 */
export function todaysDailyCard(ledger: Bet[], projected: TopPick[]): TopPick[] {
  const logged = new Set(ledger.map(ledgerRowKey).filter((k): k is string => k != null))
  const fresh = projected.filter(p => !logged.has(p.key))
  const fromLedger = dailyPlaysFromBets(ledger)
  return withHeadshotIds(buildDailyCard([...fromLedger, ...fresh]))
}

export interface DailyPlaysDay { date: string; picks: TopPick[]; record: Record1u }

/** Each past day's daily plays, newest first, graded at a flat 1 unit; a day with no plays (an off day, or before the tracker existed) is dropped. */
export async function loadDailyPlaysHistory(dates: string[]): Promise<DailyPlaysDay[]> {
  const days = await Promise.all(dates.map(d => loadLedgerBets(d)))
  return dates
    .map((date, i) => {
      const picks = withHeadshotIds(dailyPlaysFromBets(days[i]))
      return { date, picks, record: recordOf(picks) }
    })
    .filter(d => d.picks.length > 0)
}
