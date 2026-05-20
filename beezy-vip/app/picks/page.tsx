export const dynamic = 'force-dynamic'

import { Suspense } from 'react'
import { FilterBar } from '@/components/picks/filter-bar'
import { PicksTable } from '@/components/picks/picks-table'
import { apiGetPicks as getPicks } from '@/lib/betting-api'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'MLB Picks — All Systems',
  description: 'Every MLB pick from all Beezy.VIP machine learning systems.',
}

async function PicksContent({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams

  // date values are already lowercase from FilterBar (today/yesterday/last7)
  const date   = sp.date   ?? 'today'
  // status from FilterBar is uppercase (WIN/LOSS/PENDING/ALL) — API expects lowercase
  const status = sp.status && sp.status !== 'ALL' ? sp.status.toLowerCase() : undefined
  // market from FilterBar is uppercase (NRFI/HR/etc) — API expects as-is
  const market = sp.market && sp.market !== 'ALL' ? sp.market : undefined
  // book: FilterBar sends display name, API expects lowercase no-spaces
  const book   = sp.book   && sp.book   !== 'ALL' ? sp.book.toLowerCase().replace(/ /g, '') : undefined
  // sort + dir for client-side sort (passed as props to PicksTable)
  const sort   = (sp.sort  ?? 'date') as 'date' | 'edge' | 'odds'
  const dir    = (sp.dir   ?? 'desc') as 'asc' | 'desc'

  const picks = await getPicks({
    system: market,
    date,
    status,
    book,
    limit: 200,
  }).catch(() => [])

  return <PicksTable bets={picks} sort={sort} dir={dir} />
}

export default function PicksPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  return (
    <div>
      <FilterBar />
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px 20px' }}>
        <div style={{ marginBottom: '20px' }}>
          <h1 style={{ fontSize: '18px', fontWeight: 600, color: '#f5f5f7' }}>Picks</h1>
          <p className="mono" style={{ fontSize: '12px', color: '#71717a', marginTop: '4px' }}>MLB · All books · All systems</p>
        </div>
        <Suspense fallback={
          <div style={{ padding: '40px', textAlign: 'center', border: '0.5px solid #1f1f24' }}>
            <p className="mono" style={{ fontSize: '12px', color: '#71717a' }}>Loading picks…</p>
          </div>
        }>
          <PicksContent searchParams={searchParams} />
        </Suspense>
      </div>
    </div>
  )
}
