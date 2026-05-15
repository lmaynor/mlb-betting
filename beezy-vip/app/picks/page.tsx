import { Suspense }      from 'react'
import { PicksFilterBar } from '@/components/picks/filter-bar'
import { PicksTable }    from '@/components/picks/picks-table'
import { apiGetPicks as getPicks } from '@/lib/betting-api'
import type { Metadata } from 'next'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title:       'MLB Picks — All Systems',
  description: 'Every MLB pick from all Beezy.VIP machine learning systems. Filter by market, date, and result.',
}

export const revalidate = 60

async function PicksContent({
  searchParams,
}: {
  searchParams: Promise<Record<string, string>>
}) {
  const sp = await searchParams

  const picks = await getPicks({
    system: sp.market && sp.market !== 'All' ? sp.market : undefined,
    date:   sp.date   ?? 'today',
    status: sp.status && sp.status !== 'all' ? sp.status : undefined,
    limit:  100,
  }).catch(() => [])

  return <PicksTable picks={picks} />
}

export default function PicksPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string>>
}) {
  return (
    <div>
      <PicksFilterBar />
      <div className="max-w-7xl mx-auto px-4 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-extrabold uppercase tracking-tight">Picks</h1>
            <p className="mono text-xs text-muted mt-1">MLB · DraftKings · All systems</p>
          </div>
        </div>
        <Suspense
          fallback={
            <div className="border border-[var(--border)] p-12 text-center">
              <p className="mono text-xs text-muted">Loading picks…</p>
            </div>
          }
        >
          <PicksContent searchParams={searchParams} />
        </Suspense>
      </div>
    </div>
  )
}
