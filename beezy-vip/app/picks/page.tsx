export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import Link from 'next/link'
import { FilterBar } from '@/components/picks/filter-bar'
import { TopPicksTable } from '@/components/picks/top-picks-table'
import { ScoreLegend } from '@/components/ui/score-legend'
import { siteDateKey, formatDateKey } from '@/lib/dates'
import { sportHref } from '@/lib/ia'
import { activeFilterCount, parsePickFilters } from '@/lib/picks-filters'
import { sportsInSeason } from '@/lib/sports'
import { filterTopPicks, isOfficialCardPlay, isEvOutlier } from '@/lib/top-picks'
import { loadTopPlayerPicks } from '@/lib/top-picks-data'
import { cx } from '@/utils/cx'

export const metadata: Metadata = {
  title: 'Top Signals',
  description: 'Top +EV Outliers and Confluence Plays across MLB, NFL, NHL and NBA right now, ranked by Beezy Score. Free.',
}

export default async function TopPicksPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const date = siteDateKey()
  const rawParams = await searchParams
  const filters = parsePickFilters(rawParams)
  const tierParam = rawParams.tier || 'all'
  const { candidates } = await loadTopPlayerPicks(date)
  // The ten best of what the filters leave: "NFL" is the ten best NFL picks, not the NFL rows inside the overall ten.
  const top = filterTopPicks(candidates, filters)
  const filtered = activeFilterCount(filters) > 0

  const actionable = top.filter(p => isOfficialCardPlay(p) || isEvOutlier(p))
  const displayPicks = tierParam === 'official'
    ? top.filter(isOfficialCardPlay)
    : tierParam === 'outliers'
    ? top.filter(isEvOutlier)
    : (actionable.length > 0 ? actionable : top)

  const showResult = displayPicks.some(p => p.result != null)

  return (
    <div>
      <FilterBar sport="all" />
      <div className="mx-auto max-w-page p-6">
        <div className="mb-3.5 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
          <div>
            <h1 className="text-title-2-bold text-text-primary">Top Signals</h1>
            <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
              <span className="mono text-caption-2-medium uppercase tracking-wider text-text-secondary">Tier</span>
              {[
                { id: 'all', label: 'All Actionable' },
                { id: 'official', label: 'Official Plays Only' },
                { id: 'outliers', label: '+EV Outliers' },
              ].map(t => {
                const active = tierParam === t.id
                const q = new URLSearchParams({ ...rawParams, tier: t.id }).toString()
                return (
                  <Link
                    key={t.id}
                    href={`/picks?${q}`}
                    className={cx(
                      'button-press-motion inline-flex min-h-11 items-center rounded-2lg border px-3 text-body-2-medium no-underline',
                      active
                        ? 'border-accent-500 bg-button-ghost-background text-button-ghost-foreground'
                        : 'border-border-button-default bg-transparent text-text-secondary hover:border-border-button-hover hover:bg-background-primary-hover',
                    )}
                  >
                    {t.label}
                  </Link>
                )
              })}
            </div>
          </div>
          <span className="mono text-caption-1-regular text-text-secondary">
            {formatDateKey(date, { weekday: 'short', month: 'short', day: 'numeric' })} &middot; all sports, ranked by Beezy Score &middot; free
          </span>
        </div>

        {displayPicks.length === 0 ? (
          <div className="rounded-xl border border-separator-border bg-background-primary-default px-5 py-9 text-center shadow-card">
            <div className="mb-1.5 text-body-semibold text-text-primary">{filtered ? 'No picks match these filters' : 'Nothing scored yet'}</div>
            <div className="mx-auto max-w-[62ch] text-body-2-regular text-text-secondary">
              {filtered ? 'Clear a filter to widen the list. ' : 'Daily boards publish once sportsbook lines post for NFL, NBA, and NHL (MLB resumes in March). '}
              See <Link href="/picks/history" className="text-accent-400 hover:underline">past days</Link> in the meantime.
            </div>
          </div>
        ) : (
          <TopPicksTable picks={displayPicks} showResult={showResult} />
        )}

        <div className="mx-0.5 mt-3 flex flex-wrap items-baseline gap-x-5 gap-y-2 text-body-2-regular">
          <span className="text-text-secondary">Full boards</span>
          {sportsInSeason().map(p => (
            <Link key={p.slug} href={sportHref(p.slug)} className="text-accent-400 hover:underline">{p.shortName} &rarr;</Link>
          ))}
          <Link href="/picks/history" className="text-accent-400 hover:underline">Graded history &rarr;</Link>
        </div>

        <p className="mx-0.5 mt-3.5 max-w-[90ch] text-caption-1-regular text-text-secondary">
          MLB rows are the tracked model card and +EV alerts: every one is logged at the flagged price and settled on the Results ledger.
          NFL, NHL and NBA rows come from the daily projection boards and are unvalidated: they are not tracked in the public results yet.
          One row per prop, at the best available book. Odds can move before you bet.
        </p>
        <ScoreLegend />
      </div>
    </div>
  )
}
