export const revalidate = 300

import { apiGetPicks as getPicks, apiGetStats, apiGetAnalyticsDeepDive } from '@/lib/betting-api'
import { dailyBetsFromLedger } from '@/lib/top-picks-data'
import { ResultsClient } from './results-client'
import { Suspense } from 'react'
import type { Metadata } from 'next'
import { asOfLabel } from '@/lib/seo'
import { getLedgerSummary } from '@/lib/ledger-summary'
import { LedgerSummaryBlock } from './ledger-summary-block'

export const metadata: Metadata = {
  title:       'Results',
  description: 'Every settled paper bet from the beezy models and the +EV scanner, filterable to the Daily Card plays. Full P&L history, win rates, and edge vs ROI.',
}

export default async function ResultsPage() {
  const [picks, bySystem, curve, propsSettled, summary] = await Promise.all([
    getPicks({ status: 'settled', limit: 500 }).catch(() => []),
    apiGetStats().then(s => s.bySystem).catch(() => []),
    // Full-season cumulative curve (models vs +EV alerts); the 500 most recent rows alone only span about a week.
    apiGetAnalyticsDeepDive().then(d => d.bankroll_curve).catch(() => []),
    getPicks({ status: 'settled', system: 'PROPS', sport: 'all', limit: 500 }).catch(() => []),
    getLedgerSummary().catch(() => null),
  ])

  const dailyPicks = dailyBetsFromLedger([...picks, ...propsSettled])

  return (
    <>
      <div className="mx-auto max-w-page px-6 pt-6">
        <LedgerSummaryBlock summary={summary} />
      </div>
      <Suspense fallback={null}>
        <ResultsClient
          initialPicks={picks}
          initialStats={bySystem}
          curve={curve}
          dailyPicks={dailyPicks}
          asOf={bySystem.length > 0 ? asOfLabel() : undefined}
          summary={summary}
        />
      </Suspense>
    </>
  )
}
