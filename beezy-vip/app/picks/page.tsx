export const dynamic = 'force-dynamic'

import { Suspense } from 'react'
import { PicksFilterBar } from '@/components/picks/filter-bar'
import { PicksTable } from '@/components/picks/picks-table'
import { apiGetPicks as getPicks } from '@/lib/betting-api'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'MLB Picks — All Systems',
  description: 'Every MLB pick from all Beezy.VIP machine learning systems.',
}

async function PicksContent({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams
  const picks = await getPicks({
    system: sp.market && sp.market !== 'All' ? sp.market : undefined,
    date:   sp.date ?? 'today',
    status: sp.status && sp.status !== 'all' ? sp.status : undefined,
    book: sp.book && sp.book !== 'All' ? sp.book.toLowerCase().replace(/ /g, '') : undefined,
    limit:  100,
  }).catch(() => [])
  return <PicksTable bets={picks} />
}

export default function PicksPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  return (
    <div>
      <PicksFilterBar />
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
