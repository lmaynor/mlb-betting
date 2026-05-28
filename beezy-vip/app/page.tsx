import { Suspense } from 'react'
import { Hero }             from '@/components/landing/hero'
import { HowItWorks } from '@/components/landing/how-it-works'
import { ModelsGrid }       from '@/components/landing/models-grid'
import { RecentPicksTable } from '@/components/landing/recent-picks-table'
import { DiscordFollowCTA } from '@/components/landing/discord-follow-cta'

export const dynamic = 'force-dynamic'

export default function HomePage() {
  return (
    <>
      <Suspense fallback={
        <div className="border-b px-5 py-10" style={{ borderColor: 'var(--border)' }}>
          <div className="h-8 w-64 animate-pulse rounded" style={{ background: 'var(--surface)' }} />
        </div>
      }>
        <Hero />
      </Suspense>
      <Suspense fallback={null}>
        <RecentPicksTable />
      </Suspense>
      <DiscordFollowCTA />
      <HowItWorks />
      <Suspense fallback={null}>
        <ModelsGrid />
      </Suspense>
    </>
  )
}
