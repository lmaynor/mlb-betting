export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import Link from 'next/link'
import { FilterBar } from '@/components/picks/filter-bar'
import { AlignedBar } from '@/components/picks/aligned-bar'
import { DayToggle } from '@/components/picks/day-toggle'
import { GamesStrip } from '@/components/picks/games-strip'
import { LockedNotice } from '@/components/picks/locked-notice'
import { ResearchTabs } from '@/components/picks/research-tabs'
import { SportProjectionsBoard } from '@/components/picks/sport-projections-board'
import { EdgeEvLegend } from '@/components/ui/edge-ev-legend'
import { getViewer } from '@/lib/auth'
import { withCounts } from '@/lib/board-games'
import { applyPickFilters } from '@/lib/board-rows'
import { siteDateKey } from '@/lib/dates'
import { activeFilterCount, parsePickFilters } from '@/lib/picks-filters'
import { loadSportBoard } from '@/lib/sport-board-data'
import { isSportSlug, LIVE_SPORTS, SPORT_PILLARS, type SportSlug } from '@/lib/sports'

export const metadata: Metadata = {
  title: 'Prop Research: All Projections',
  description: 'The model projection for every player prop on the slate in MLB, NFL, NHL and NBA, beside the line, the market consensus and the best price. Filter, search and sort to find your own spots.',
}

type Props = { searchParams: Promise<Record<string, string>> }

/**
 * Prop Research, all projections: every model expectation (the expected value / lambda behind each player prop) for one league,
 * not only the lines that were flagged. Same rows, filters and games strip as the picks pages, so a spot found here can be
 * opened on its system page. `?league=` picks the sport (the shared filter bar owns `sport`, so it gets its own name).
 */
export default async function EdgeProjectionsPage({ searchParams }: Props) {
  const sp = await searchParams
  const league: SportSlug = sp.league && isSportSlug(sp.league) ? sp.league : 'mlb'
  const pillar = SPORT_PILLARS[league]
  const f = parsePickFilters(sp)
  const board = await loadSportBoard(league, await getViewer(), { day: f.day, date: f.date, view: 'projections', kind: league === 'mlb' ? 'player' : undefined })
  const rows = applyPickFilters(board.rows, f)
  const games = withCounts(board.games, board.rows)
  const filtered = activeFilterCount(f) > 0

  return (
    <main>
      <header className="max-w-[1200px] mx-auto pt-5 px-6 pb-3.5">
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <h1 className="text-[20px] font-bold text-text-primary m-0 tracking-[-0.01em]">Prop Research</h1>
          <span className="mono text-[12px] text-text-secondary">{rows.length} {rows.length === 1 ? 'projection' : 'projections'}{filtered ? ' after filters' : ''}</span>
        </div>
        <div className="flex gap-3 flex-wrap items-center mt-3.5">
          <ResearchTabs active="projections" />
          <nav aria-label="League" className="inline-flex gap-0.5 p-[3px] bg-[var(--color-background-secondary-default)] border border-[color:var(--color-separator-border)] rounded-[8px]">
            {LIVE_SPORTS.map(p => (
              <Link key={p.slug} href={`/edge/projections?league=${p.slug}`} aria-current={p.slug === league ? 'page' : undefined}
                style={{ fontSize: '12px', fontWeight: 700, letterSpacing: '0.05em', padding: '7px 13px', borderRadius: '6px', textDecoration: 'none', whiteSpace: 'nowrap',
                  color: p.slug === league ? '#ffffff' : 'var(--color-neutral-300)', background: p.slug === league ? 'var(--color-background-tertiary-default)' : 'transparent', border: `1px solid ${p.slug === league ? 'var(--color-border-button-hover)' : 'transparent'}` }}>
                {p.shortName}
              </Link>
            ))}
          </nav>
        </div>
      </header>
      <AlignedBar>
        <DayToggle today={siteDateKey()} yesterday={board.yesterday} days={board.days} selected={board.date} pinDates={league !== 'mlb'} />
      </AlignedBar>
      <GamesStrip games={games} selected={f.game} today={siteDateKey()} total={board.rows.length} />
      <FilterBar sport={league} kind={league === 'mlb' ? 'player' : undefined} />
      <div className="max-w-[1200px] mx-auto p-6">
        <SportProjectionsBoard sport={league} rows={rows} status={board.status} statusWarn={board.statusWarn}
          emptyBody={board.rows.length === 0 ? board.emptyBody : 'No rows match these filters. Clear a filter, or pick another game.'} />
        <LockedNotice locked={board.locked} shown={rows.length} what={`${pillar.shortName} projections`} plan={`The full board is on the ${pillar.shortName} Pass or All-Access Pro.`} />
        <EdgeEvLegend />
        <p style={{ fontSize: '11.5px', color: 'var(--color-text-secondary)', margin: '14px 2px 0', lineHeight: 1.55, maxWidth: '90ch' }}>
          MODEL is the projected value for the stat and the probability it goes over the line. Rows with no line yet are projections only;
          nothing here is a recommendation, and {league === 'mlb' ? 'only the highest-edge MLB plays are tracked in the public results' : `${pillar.shortName} projections are unvalidated and not tracked in the public results`}.
        </p>
      </div>
    </main>
  )
}
