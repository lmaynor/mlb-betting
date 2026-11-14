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
  title: 'Live Candidates',
  description: 'Live candidate ranking across MLB, NFL, NHL and NBA, ranked by Beezy Score. Ungraded: only plays promoted to Today\'s Card are graded in Results.',
}

export default async function LiveCandidatesPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const date = siteDateKey()
  const rawParams = await searchParams
  const filters = parsePickFilters(rawParams)
  const typeParam = filters.type ?? 'all'
  const { candidates } = await loadTopPlayerPicks(date)
  // The ten best of what the filters leave: "NFL" is the ten best NFL picks, not the NFL rows inside the overall ten.
  const top = filterTopPicks(candidates, filters)
  const filtered = activeFilterCount(filters) > 0

  // filterTopPicks has already applied ?type= (and the ?tier= shim); with no type chosen, the board
  // defaults to the actionable cut, official plays and +EV outliers.
  const actionable = top.filter(p => isOfficialCardPlay(p) || isEvOutlier(p))
  const displayPicks = filters.type ? top : (actionable.length > 0 ? actionable : top)

  const showResult = displayPicks.some(p => p.result != null)

  return (
    <div>
      <FilterBar sport="all" />
      <div className="mx-auto max-w-page p-6">
        <div className="mb-3.5 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
          <div>
            <h1 className="text-title-2-bold text-text-primary">Live Candidates</h1>
            <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
              <span className="mono text-caption-2-medium text-text-secondary">Signal Type</span>
              {[
                { id: 'all', label: 'All Actionable' },
                { id: 'official', label: 'Official Plays' },
                { id: 'confluence', label: 'Confluence' },
                { id: 'outlier', label: '+EV Outliers' },
              ].map(t => {
                const active = typeParam === t.id
                const rest = { ...rawParams }
                delete rest.tier
                delete rest.type
                const q = new URLSearchParams(t.id === 'all' ? rest : { ...rest, type: t.id }).toString()
                return (
                  <Link
                    key={t.id}
                    href={q ? `/ev/candidates?${q}` : '/ev/candidates'}
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
            {formatDateKey(date, { weekday: 'short', month: 'short', day: 'numeric' })}{' '}&middot; all sports, ranked by Beezy Score &middot; free
          </span>
        </div>

        <p className="mb-3 max-w-[90ch] text-body-2-semibold text-text-primary">
          Live candidate ranking, ungraded. Only plays promoted to Today&apos;s Card are graded in Results.
        </p>

        <p className="mb-3 max-w-[90ch] text-body-2-regular text-text-secondary">
          {typeParam === 'official'
            ? 'Official plays inside today\'s top ten. The full card, with no ten-row window, is Today\'s Card.'
            : typeParam === 'outlier'
            ? '+EV outliers inside today\'s top ten: one book\'s price out of line with the pack, no model agreement needed. Not official plays.'
            : typeParam === 'confluence'
            ? 'Confluence candidates inside today\'s top ten: the projection model and the market independently favor the same side. Not official plays.'
            : 'The ten highest-scored plays across all sports right now, cut to the actionable rows: official plays and +EV outliers. Rows leave this board when their game starts; Today\'s Card keeps its plays.'}
        </p>

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
          <Link href="/picks" className="text-accent-400 hover:underline">Today&apos;s Card &rarr;</Link>
        </div>

        <p className="mx-0.5 mt-3.5 max-w-[90ch] text-caption-1-regular text-text-secondary">
          MLB rows are tracked ledger bets (model systems and +EV alerts), settled in Results. NFL, NHL and NBA rows are projection-board rows: a row stamped Official is a Today&apos;s Card play, logged at this price and graded in Past Cards; a row stamped Tracked is logged on paper and settled in the ledger; an unstamped row is not tracked. One row per prop, at the best available book. Odds can move before you bet.
        </p>
        <ScoreLegend />
      </div>
    </div>
  )
}
