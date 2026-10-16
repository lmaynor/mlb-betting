import type { Metadata } from 'next'
import { apiGetEvFeed, apiGetAnalyticsDeepDive } from '@/lib/betting-api'
import { limitEvFeed } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import { siteDateKey } from '@/lib/dates'
import { EvClient } from './ev-client'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title: '+EV Terminal',
  description:
    'Soft-line +EV alerts priced against a sharp consensus, with a public paper track record. Sizing at your bankroll, filtered by book and edge.',
}

export default async function EvPage() {
  const [raw, analytics, viewer] = await Promise.all([
    apiGetEvFeed({ minEv: 0.02, limit: 150 }).catch(() => null),
    apiGetAnalyticsDeepDive().catch(() => null),
    getViewer(),
  ])
  // Live rows are the Pro surface (lib/access.ts), cut here so they are never in the payload; the settled record stays public.
  const cut = raw ? limitEvFeed(raw, viewer, siteDateKey()) : null
  const feed = cut ? { ...cut.feed, locked: cut.locked } : null
  return <EvClient initialFeed={feed} headline={analytics?.ev_headline ?? null} />
}
