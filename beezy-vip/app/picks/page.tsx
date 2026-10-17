export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import Link from 'next/link'
import { FilterBar } from '@/components/picks/filter-bar'
import { TopPicksTable } from '@/components/picks/top-picks-table'
import { ScoreLegend } from '@/components/ui/score-legend'
import { siteDateKey, formatDateKey } from '@/lib/dates'
import { sportHref } from '@/lib/ia'
import { parsePickFilters } from '@/lib/picks-filters'
import { LIVE_SPORTS } from '@/lib/sports'
import { filterTopPicks } from '@/lib/top-picks'
import { loadTopPlayerPicks } from '@/lib/top-picks-data'

export const metadata: Metadata = {
  title: 'Top 10 Player Picks',
  description: 'The ten highest-scoring player picks across MLB, NFL, NHL and NBA right now, ranked by Beezy Score. Free.',
}

export default async function TopPicksPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const date = siteDateKey()
  const filters = parsePickFilters(await searchParams)
  const { candidates } = await loadTopPlayerPicks(date)
  // The ten best of what the filters leave: "NFL" is the ten best NFL picks, not the NFL rows inside the overall ten.
  const top = filterTopPicks(candidates, filters)
  const filtered = filters.sport != null || filters.tier != null || filters.book != null || filters.books.length > 0 || filters.q !== ''

  const showResult = top.some(p => p.result != null)

  return (
    <div>
      <FilterBar sport="all" defaultOpen />
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap', marginBottom: '14px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>Top 10 player picks</h1>
        <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>
          {formatDateKey(date, { weekday: 'short', month: 'short', day: 'numeric' })} &middot; all sports, ranked by Beezy Score &middot; free
        </span>
      </div>

      {top.length === 0 ? (
        <div style={{ background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', padding: '36px 20px', textAlign: 'center' }}>
          <div style={{ color: 'var(--chalk)', fontWeight: 600, marginBottom: '6px' }}>{filtered ? 'No picks match these filters' : 'Nothing scored yet'}</div>
          <div style={{ color: 'var(--fog)', fontSize: '13px', maxWidth: '62ch', margin: '0 auto', lineHeight: 1.55 }}>
            {filtered ? 'Clear a filter to widen the list. ' : 'The MLB card publishes after the ~11:00 AM CT model run, and the other sports fill in once sportsbook lines post. '}
            See <Link href="/picks/history" style={{ color: 'var(--link)' }}>past days</Link> in the meantime.
          </div>
        </div>
      ) : (
        <TopPicksTable picks={top} showResult={showResult} />
      )}

      <div style={{ display: 'flex', gap: '8px 20px', flexWrap: 'wrap', alignItems: 'baseline', margin: '12px 2px 0', fontSize: '12.5px' }}>
        <span style={{ color: 'var(--fog)' }}>Full boards</span>
        {LIVE_SPORTS.map(p => (
          <Link key={p.slug} href={sportHref(p.slug)} style={{ color: 'var(--link)' }}>{p.shortName} &rarr;</Link>
        ))}
        <Link href="/picks/games" style={{ color: 'var(--link)' }}>Game picks &rarr;</Link>
        <Link href="/picks/history" style={{ color: 'var(--link)' }}>Picks history &rarr;</Link>
      </div>

      <p style={{ fontSize: '11.5px', color: 'var(--fog)', margin: '14px 2px 0', lineHeight: 1.55, maxWidth: '90ch' }}>
        MLB rows are the tracked model card and +EV alerts: every one is logged at the flagged price and settled on the Results ledger.
        NFL, NHL and NBA rows come from the daily projection boards and are unvalidated: they are not tracked in the public results yet.
        One row per prop, at the best available book. Odds can move before you bet.
      </p>
      <ScoreLegend />
    </div>
    </div>
  )
}
