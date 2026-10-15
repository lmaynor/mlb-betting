import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import {
  apiGetEvFeed, apiGetStats, apiGetSparklineBySystem, apiGetTodayPicks, apiGetTodaySlate,
  type SparklinePoint,
} from '@/lib/betting-api'
import { isSportSlug, SPORT_PILLARS } from '@/lib/sports'
import type { Bet, EvFeed, SystemStats, TodaySlate } from '@/lib/types'
import { SportHubClient } from './sport-hub-client'

export const dynamic = 'force-dynamic'

export async function generateMetadata({ params }: { params: Promise<{ sport: string }> }): Promise<Metadata> {
  const { sport } = await params
  if (!isSportSlug(sport)) return {}
  const p = SPORT_PILLARS[sport]
  return { title: `${p.shortName} Hub`, description: p.description }
}

export default async function SportHubPage({ params }: { params: Promise<{ sport: string }> }) {
  const { sport } = await params
  if (!isSportSlug(sport)) notFound()

  let picks: Bet[] = []
  let stats: SystemStats[] = []
  let sparklines: Record<string, SparklinePoint[]> = {}
  let slate: TodaySlate | null = null
  let feed: EvFeed | null = null

  if (sport === 'mlb') {
    const [p, s, sl, f] = await Promise.all([
      apiGetTodayPicks().catch(() => [] as Bet[]),
      apiGetStats().then(r => r.bySystem).catch(() => [] as SystemStats[]),
      apiGetTodaySlate().catch(() => null),
      apiGetEvFeed({ sport: 'mlb', minEv: 0.03, limit: 40 }).catch(() => null),
    ])
    picks = p; stats = s; slate = sl; feed = f
    const sys = s.map(x => x.system).filter(k => SPORT_PILLARS.mlb.markets.some(m => m.system === k))
    const results = await Promise.allSettled(sys.map(k => apiGetSparklineBySystem(k, 30)))
    sparklines = Object.fromEntries(sys.map((k, i) => [k, results[i].status === 'fulfilled' ? (results[i] as PromiseFulfilledResult<SparklinePoint[]>).value : []]))
  } else if (sport === 'nfl' || sport === 'nhl' || sport === 'nba') {
    feed = await apiGetEvFeed({ sport, minEv: 0.02, limit: 150 }).catch(() => null)
  }

  return <SportHubClient sport={sport} picks={picks} stats={stats} sparklines={sparklines} slate={slate} feed={feed} />
}
