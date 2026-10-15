export const dynamic = 'force-dynamic'

import { Suspense } from 'react'
import type { Metadata } from 'next'
import { DateBar } from '@/components/picks/date-bar'
import { FilterBar } from '@/components/picks/filter-bar'
import { PicksTable } from '@/components/picks/picks-table'
import { apiGetPicks as getPicks } from '@/lib/betting-api'
import { addDaysToDateKey, formatDateKey, siteDateKey } from '@/lib/dates'
import { collapseBooks } from '@/lib/dedupe'

export const metadata: Metadata = {
  title: 'MLB Picks',
  description: 'Model picks and +EV alerts for today\'s MLB slate, ranked by Beezy Score.',
}

async function PicksContent({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams

  const explicitDate = sp.date && /^\d{4}-\d{2}-\d{2}$/.test(sp.date) ? sp.date : undefined
  const date = explicitDate ?? siteDateKey()
  // The API knows pending|settled|won|lost; the chips say WIN/LOSS.
  const STATUS_MAP: Record<string, string> = { win: 'won', loss: 'lost', pending: 'pending' }
  const status = sp.status && sp.status !== 'ALL' ? STATUS_MAP[sp.status.toLowerCase()] : undefined
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
  if (!explicitDate && picks.length === 0) {
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
            Showing {formatDateKey(shownDate, { weekday: 'short', month: 'short', day: 'numeric' })}. Today&rsquo;s model card publishes after the ~11:00 AM CT run; +EV alerts post through the day.
          </p>
        </div>
      )}
      {/* keyed on the query so the table's local sort/page state resets whenever a filter changes */}
      <PicksTable key={[date, market, status, book, sort, dir].join('|')} bets={book ? picks : collapseBooks(picks)} sort={sort} dir={dir} />
    </>
  )
}

export default function PicksPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  return (
    <div>
      <DateBar />
      <FilterBar />
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '32px 24px' }}>
        <div style={{ marginBottom: '14px', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>Today&rsquo;s picks</h1>
          <span style={{ display: 'flex', gap: '16px', alignItems: 'baseline', flexWrap: 'wrap' }}>
            <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>Model picks + +EV alerts &middot; one row per prop, ranked by Beezy Score</span>
            <a href="/ev" style={{ fontSize: '12.5px', color: 'var(--link)' }}>+EV Terminal &rarr;</a>
            <a href="/cheat-sheet" style={{ fontSize: '12.5px', color: 'var(--link)' }}>Share card &rarr;</a>
          </span>
        </div>
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
