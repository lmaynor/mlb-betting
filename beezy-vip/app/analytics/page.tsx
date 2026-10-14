import type { Metadata } from 'next'
import { apiGetAnalyticsDeepDive, apiGetStats } from '@/lib/betting-api'
import type { SystemStats } from '@/lib/types'
import { AnalyticsClient } from './analytics-client'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title: 'Analytics',
  description:
    'Bankroll simulation, edge-bucket calibration, per-book results and system health for every tracked Beezy strategy.',
}

export default async function AnalyticsPage() {
  const [data, stats] = await Promise.all([
    apiGetAnalyticsDeepDive('EV').catch(() => null),
    apiGetStats().then(s => s.bySystem).catch(() => [] as SystemStats[]),
  ])
  return <AnalyticsClient initial={data} stats={stats} />
}
