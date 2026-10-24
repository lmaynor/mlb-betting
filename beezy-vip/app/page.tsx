import type { Metadata } from 'next'
import { Suspense } from 'react'
import { Hero }             from '@/components/landing/hero'
import { CommandCenter }     from '@/components/landing/command-center'
import { EdgeCompare }      from '@/components/landing/edge-compare'
import { HowItWorks } from '@/components/landing/how-it-works'
import { ModelsGrid }       from '@/components/landing/models-grid'
import { RecentPicksTable } from '@/components/landing/recent-picks-table'
import { DiscordFollowCTA } from '@/components/landing/discord-follow-cta'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title: { absolute: 'beezy.fyi \u2014 Quantitative Sports Betting Analytics & +EV Scanner' },
  description:
    'Find mispriced sportsbook lines with beezy.fyi. Market-derived +EV alerts, player prop projections and a public paper-tracked results ledger for NFL, NBA, NHL and MLB.',
}

export default function HomePage() {
  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '0 24px' }}>
      <Suspense fallback={
        <div style={{ marginTop: '36px', height: '320px', borderRadius: '8px', border: '1px solid #1e2230', background: '#11131a' }} className="reveal" />
      }>
        <Hero />
      </Suspense>
      <Suspense fallback={null}>
        <CommandCenter />
      </Suspense>
      <Suspense fallback={null}>
        <EdgeCompare />
      </Suspense>
      <Suspense fallback={null}>
        <RecentPicksTable />
      </Suspense>
      <HowItWorks />
      <Suspense fallback={null}>
        <ModelsGrid />
      </Suspense>
      <DiscordFollowCTA />
    </div>
  )
}
