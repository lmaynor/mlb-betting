import { Suspense } from 'react'
import { Hero }             from '@/components/landing/hero'
import { StatsStrip }       from '@/components/landing/stats-strip'
import { LiveTicker }       from '@/components/landing/live-ticker'
import { HowItWorks, DiscordCTA } from '@/components/landing/how-it-works'
import { ModelsGrid }       from '@/components/landing/models-grid'
import { RecentPicksTable } from '@/components/landing/recent-picks-table'
import { PricingSection }   from '@/components/landing/pricing'

export const dynamic = 'force-dynamic'

export const revalidate = 300  // ISR every 5 min

function LoadingStats() {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 divide-x divide-[var(--border)] border-b border-[var(--border)]">
      {[...Array(4)].map((_, i) => (
        <div key={i} className="p-5 animate-pulse">
          <div className="h-3 w-20 bg-[var(--border-bright)] mb-3" />
          <div className="h-8 w-24 bg-[var(--border-bright)]" />
        </div>
      ))}
    </div>
  )
}

export default function HomePage() {
  return (
    <>
      <Hero />
      <Suspense fallback={<LoadingStats />}>
        <StatsStrip />
      </Suspense>
      <Suspense fallback={null}>
        <LiveTicker />
      </Suspense>
      <HowItWorks />
      <Suspense fallback={null}>
        <ModelsGrid />
      </Suspense>
      <Suspense fallback={null}>
        <RecentPicksTable />
      </Suspense>
      <PricingSection />
      <DiscordCTA />
    </>
  )
}
