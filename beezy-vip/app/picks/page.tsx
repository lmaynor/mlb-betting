export const dynamic = 'force-dynamic'

import { Suspense } from 'react'
import type { Metadata } from 'next'
import { DateBar } from '@/components/picks/date-bar'
import { FilterBar } from '@/components/picks/filter-bar'
import { PicksTable } from '@/components/picks/picks-table'
import { apiGetPicks as getPicks } from '@/lib/betting-api'
import { siteDateKey } from '@/lib/dates'

export const metadata: Metadata = {
  title: 'MLB Picks - All Systems',
  description: 'Every MLB pick from all Beezy.VIP machine learning systems.',
}

async function PicksContent({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams

  const date = sp.date ?? siteDateKey()
  const status = sp.status && sp.status !== 'ALL' ? sp.status.toLowerCase() : undefined
  const market = sp.market && sp.market !== 'ALL' ? sp.market : undefined
  const book = sp.book && sp.book !== 'ALL' ? sp.book.toLowerCase().replace(/ /g, '') : undefined
  const sort = (sp.sort ?? 'score') as 'date' | 'score' | 'edge' | 'odds'
  const dir = (sp.dir ?? 'desc') as 'asc' | 'desc'

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
      <DateBar />
      <FilterBar />
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px 20px' }}>
        <div style={{ marginBottom: '20px', display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: '16px', flexWrap: 'wrap' }}>
          <div>
            <h1 style={{ fontSize: '18px', fontWeight: 700, color: '#f5f5f7' }}>Picks</h1>
            <p className="mono" style={{ fontSize: '12px', color: '#71717a', marginTop: '4px' }}>
              MLB / all books / ranked by Beezy Score
            </p>
          </div>
          <a
            href="/cheat-sheet"
            className="mono"
            style={{
              fontSize: '11px',
              fontWeight: 800,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              color: '#0a0a0c',
              background: '#10b981',
              padding: '8px 12px',
              borderRadius: 'var(--radius-sm)',
              textDecoration: 'none',
            }}
          >
            Daily Card
          </a>
        </div>
        <Suspense fallback={
          <div style={{ padding: '40px', textAlign: 'center', border: '0.5px solid #1f1f24', borderRadius: 'var(--radius)' }}>
            <p className="mono" style={{ fontSize: '12px', color: '#71717a' }}>Loading picks...</p>
          </div>
        }>
          <PicksContent searchParams={searchParams} />
        </Suspense>
      </div>
    </div>
  )
}
