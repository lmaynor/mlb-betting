export const dynamic = 'force-dynamic'

import { apiGetPicks as getPicks, apiGetStats } from '@/lib/betting-api'
import { ResultsClient } from './results-client'
import { Suspense } from 'react'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'Results -- All Settled Bets',
  description: 'Every settled bet from all Beezy.VIP MLB machine learning systems. Full P&L history, win rates, and edge vs ROI analysis.',
}

export default async function ResultsPage() {
  const [picks, bySystem] = await Promise.all([
    getPicks({ status: 'settled', limit: 500 }).catch(() => []),
    apiGetStats().then(s => s.bySystem).catch(() => []),
  ])

  return (
    <Suspense fallback={null}>
      <ResultsClient initialPicks={picks} initialStats={bySystem} />
    </Suspense>
  )
}
