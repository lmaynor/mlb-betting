/**
 * Server-side loader for the cross-sport Top 10 player picks. One place, so /picks and the Daily Card can never disagree
 * about what today's list is. The Top 10 itself is the free tier, so nothing here is cut by the access policy.
 */
import { apiGetPicks, apiGetSportProjections } from '@/lib/betting-api'
import { LIVE_SPORTS, type SportSlug } from '@/lib/sports'
import { buildDailyCard, fromProjection, rankTopPicks, TOP_N, topFromBets, type TopPick } from '@/lib/top-picks'
import type { Bet, SportProjections } from '@/lib/types'

/** Every live sport whose board is a projection feed (lib/sports.ts `board`): a new sport joins the Top 10 by registry entry. */
const PROJECTION_SPORTS = LIVE_SPORTS.filter(p => p.board === 'projection').map(p => p.slug) as Exclude<SportSlug, 'mlb'>[]

export async function loadTopPlayerPicks(date: string): Promise<{ top: TopPick[]; candidates: TopPick[]; mlbToday: Bet[]; dailyCard: TopPick[] }> {
  const [mlbToday, ...boards] = await Promise.all([
    apiGetPicks({ date, limit: 200 }).catch(() => [] as Bet[]),
    ...PROJECTION_SPORTS.map(s => apiGetSportProjections(s, { limit: 600 }).catch(() => null as SportProjections | null)),
  ])
  const projected = boards.flatMap(b => (b?.rows ?? []).map(fromProjection).filter((p): p is TopPick => p != null))
  // `candidates` is every scored pick (the filters on /picks cut from it); `top` is the unfiltered ten; `dailyCard` is the 1..5 locked official plays.
  const candidates = rankTopPicks([...topFromBets(mlbToday, 1000), ...projected], 1000)
  const dailyCard = buildDailyCard(candidates)
  return { top: candidates.slice(0, TOP_N), candidates, mlbToday, dailyCard }
}
