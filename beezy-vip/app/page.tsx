import { Suspense } from 'react'
import { Hero }             from '@/components/landing/hero'
import { EdgeCompare }      from '@/components/landing/edge-compare'
import { HowItWorks } from '@/components/landing/how-it-works'
import { ModelsGrid }       from '@/components/landing/models-grid'
import { RecentPicksTable } from '@/components/landing/recent-picks-table'
import { DiscordFollowCTA } from '@/components/landing/discord-follow-cta'

export const dynamic = 'force-dynamic'

export default function HomePage() {
  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '0 24px' }}>
      <Suspense fallback={
        <div style={{ marginTop: '36px', height: '320px', borderRadius: 'var(--radius-xl)', border: '1px solid var(--basalt)', background: 'var(--graphite)' }} className="reveal" />
      }>
        <Hero />
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
