import type { Metadata } from 'next'
import { apiGetEvFeed, apiGetAnalyticsDeepDive } from '@/lib/betting-api'
import { EvClient } from './ev-client'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title: '+EV Terminal',
  description:
    'Soft-line +EV alerts priced against a sharp consensus, with a public paper track record. Sizing at your bankroll, filtered by book and edge.',
}

export default async function EvPage() {
  const [feed, analytics] = await Promise.all([
    apiGetEvFeed({ minEv: 0.02, limit: 150 }).catch(() => null),
    apiGetAnalyticsDeepDive().catch(() => null),
  ])
  return <EvClient initialFeed={feed} headline={analytics?.ev_headline ?? null} />
}
