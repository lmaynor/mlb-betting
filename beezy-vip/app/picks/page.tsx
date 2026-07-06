export const dynamic = 'force-dynamic'

import { Suspense } from 'react'
import type { Metadata } from 'next'
import { DateBar } from '@/components/picks/date-bar'
import { FilterBar } from '@/components/picks/filter-bar'
import { PicksTable } from '@/components/picks/picks-table'
import { apiGetPicks as getPicks, apiGetTodayAlerts } from '@/lib/betting-api'
import { addDaysToDateKey, formatDateKey, siteDateKey } from '@/lib/dates'
import { PICK_SYSTEMS } from '@/lib/pick-systems'
import { LiveEvBoard } from '@/components/picks/live-ev-board'

export const metadata: Metadata = {
  title: 'MLB Picks - All Systems',
  description: 'Every MLB pick from all Beezy.FYI machine learning systems.',
}

async function PicksContent({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams

  const date = sp.date ?? siteDateKey()
  const status = sp.status && sp.status !== 'ALL' ? sp.status.toLowerCase() : undefined
  const market = sp.market && sp.market !== 'ALL' ? sp.market : undefined
  const book = sp.book && sp.book !== 'ALL' ? sp.book.toLowerCase().replace(/ /g, '') : undefined
  const sort = (sp.sort ?? 'score') as 'date' | 'score' | 'edge' | 'odds'
  const dir = (sp.dir ?? 'desc') as 'asc' | 'desc'

  let picks = await getPicks({
    system: market,
    date,
    status,
    book,
    limit: 200,
  }).catch(() => [])

  // Overnight gap: the site date rolls at midnight CT but today's card only
  // publishes after the ~11:00 CT morning run. An empty "today" with no
  // explicit date filter falls back to the latest card instead of a blank
  // page, clearly labeled.
  let shownDate = date
  if (!sp.date && picks.length === 0) {
    const yesterday = addDaysToDateKey(date, -1)
    const prior = await getPicks({ system: market, date: yesterday, status, book, limit: 200 }).catch(() => [])
    if (prior.length > 0) {
      picks = prior
      shownDate = yesterday
    }
  }

  return (
    <>
      {shownDate !== date && (
        <div style={{ marginBottom: '16px', padding: '10px 14px', border: '1px solid var(--iron)', borderRadius: 'var(--radius)', background: 'var(--obsidian)' }}>
          <p className="mono" style={{ fontSize: '11px', color: 'var(--warn)', margin: 0 }}>
            Showing {formatDateKey(shownDate, { weekday: 'short', month: 'short', day: 'numeric' })} &mdash; today&rsquo;s card publishes after the 11:00 AM CT model run.
          </p>
        </div>
      )}
      <PicksTable bets={picks} sort={sort} dir={dir} />
    </>
  )
}

async function AlertsStrip() {
  const alerts = await apiGetTodayAlerts(siteDateKey()).catch(() => [])
  return <LiveEvBoard alerts={alerts} />
}

export default function PicksPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  return (
    <div>
      <DateBar />
      <FilterBar />
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '32px 24px' }}>
        <div style={{ marginBottom: '24px', display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: '16px', flexWrap: 'wrap' }}>
          <div>
            <h1 className="dell-display" style={{ fontSize: '30px', color: 'var(--chalk)' }}>Today&rsquo;s picks</h1>
            <p className="times" style={{ fontSize: '15px', color: 'var(--fog)', marginTop: '8px', maxWidth: '60ch', lineHeight: 1.55 }}>
              Every model-qualified MLB play across {PICK_SYSTEMS.length} systems, ranked by Beezy Score with market, book, edge, and result context.
            </p>
          </div>
          <a href="/cheat-sheet" className="btn btn-primary">
            Daily Card &rarr;
          </a>
        </div>
        <Suspense fallback={null}>
          <AlertsStrip />
        </Suspense>
        <Suspense fallback={
          <div style={{ padding: '48px', textAlign: 'center', border: '1px solid var(--basalt)', borderRadius: 'var(--radius-lg)', background: 'var(--graphite)' }}>
            <p className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>Loading picks...</p>
          </div>
        }>
          <PicksContent searchParams={searchParams} />
        </Suspense>
      </div>
    </div>
  )
}
