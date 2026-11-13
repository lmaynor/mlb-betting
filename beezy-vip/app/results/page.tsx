export const revalidate = 60

import { apiGetPicks as getPicks, apiGetStats, apiGetAnalyticsDeepDive } from '@/lib/betting-api'
import { dailyBetsFromLedger } from '@/lib/top-picks-data'
import { ResultsClient } from './results-client'
import { Suspense } from 'react'
import type { Metadata } from 'next'
import { asOfLabel } from '@/lib/seo'

export const metadata: Metadata = {
  title:       'Results \u2014 All Settled Bets',
  description: 'Every settled paper bet from the beezy models and the +EV scanner, filterable to the Daily Card plays. Full P&L history, win rates, and edge vs ROI.',
}

export default async function ResultsPage() {
  const [picks, bySystem, curve, propsSettled] = await Promise.all([
    getPicks({ status: 'settled', limit: 500 }).catch(() => []),
    apiGetStats().then(s => s.bySystem).catch(() => []),
    // Full-season cumulative curve (models vs +EV alerts); the 500 most recent rows alone only span about a week.
    apiGetAnalyticsDeepDive().then(d => d.bankroll_curve).catch(() => []),
    getPicks({ status: 'settled', system: 'PROPS', sport: 'all', limit: 500 }).catch(() => []),
  ])

  const dailyPicks = dailyBetsFromLedger([...picks, ...propsSettled])

  return (
    <Suspense fallback={null}>
      <ResultsClient initialPicks={picks} initialStats={bySystem} curve={curve} dailyPicks={dailyPicks} asOf={bySystem.length > 0 ? asOfLabel() : undefined} />
    </Suspense>
  )
}
