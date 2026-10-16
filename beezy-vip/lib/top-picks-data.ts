/**
 * Server-side loader for the cross-sport Top 10 player picks. One place, so /picks and the Daily Card can never disagree
 * about what today's list is. The Top 10 itself is the free tier, so nothing here is cut by the access policy.
 */
import { apiGetPicks, apiGetSportProjections } from '@/lib/betting-api'
import { fromProjection, rankTopPicks, TOP_N, topFromBets, type TopPick } from '@/lib/top-picks'
import type { Bet, SportProjections } from '@/lib/types'

const OTHER = ['nfl', 'nhl', 'nba'] as const

export async function loadTopPlayerPicks(date: string): Promise<{ top: TopPick[]; mlbToday: Bet[] }> {
  const [mlbToday, ...boards] = await Promise.all([
    apiGetPicks({ date, limit: 200 }).catch(() => [] as Bet[]),
    ...OTHER.map(s => apiGetSportProjections(s).catch(() => null as SportProjections | null)),
  ])
  const projected = boards.flatMap(b => (b?.rows ?? []).map(fromProjection).filter((p): p is TopPick => p != null))
  return { top: rankTopPicks([...topFromBets(mlbToday, TOP_N), ...projected], TOP_N), mlbToday }
}
