import Link from 'next/link'
import { AlignedBar } from '@/components/picks/aligned-bar'
import { DayToggle } from '@/components/picks/day-toggle'
import { FilterBar } from '@/components/picks/filter-bar'
import { GamesStrip } from '@/components/picks/games-strip'
import { LockedNotice } from '@/components/picks/locked-notice'
import { MarketPrices } from '@/components/picks/market-prices'
import { SportProjectionsBoard } from '@/components/picks/sport-projections-board'
import { SportTabs, type SportView } from '@/components/picks/sport-tabs'
import { ScoreLegend } from '@/components/ui/score-legend'
import { EdgeEvLegend } from '@/components/ui/edge-ev-legend'
import { connection } from 'next/server'
import { gatingOn } from '@/lib/access'
import { withCounts } from '@/lib/board-games'
import { applyPickFilters, isOfficialRow, isOutlierRow, pickRows } from '@/lib/board-rows'
import { siteDateKey } from '@/lib/dates'
import type { PickKind } from '@/lib/pick-systems'
import { activeFilterCount, parsePickFilters } from '@/lib/picks-filters'
import { loadSportBoardForPage } from '@/lib/board-page-loader'
import { type BoardView } from '@/lib/sport-board-data'
import { SPORT_PILLARS, STATUS_LABEL, type ProjectionSport, type SportSlug } from '@/lib/sports'

const COPY: Record<BoardView, { h1: (s: string) => string; sub: string; what: string }> = {
  picks: { h1: s => `${s} picks`, sub: 'Priced plays, ranked by Beezy Score', what: 'picks' },
  projections: { h1: s => `${s} projections`, sub: 'The model number for every player prop, beside the line', what: 'projections' },
}

/**
 * The page every sport's PICKS and PROJECTIONS tab renders (MLB, NFL, NHL, NBA, and any sport added to lib/sports.ts):
 * tabs, then ONE control stack (day, games, filters), then the table. Picks is the priced plays; projections is every row.
 * MLB reaches the same shell: its tracked bets are adapted to the same rows (lib/mlb-board.ts).
 */
export async function SportBoardPage({ sport, view, searchParams, kind }: {
  sport: SportSlug; view: BoardView; searchParams: Promise<Record<string, string>>; kind?: PickKind
}) {
  const pillar = SPORT_PILLARS[sport]
  const tracked = pillar.board === 'tracked'
  const rawParams = await searchParams
  const f = parsePickFilters(rawParams)
  const tierParam = rawParams.tier || 'all'
  // Tier gating on = the board is cut per viewer: connection() guarantees per-request rendering so the cut
  // can never be cached or shared (the loader also branches on gatingOn). Gating off, the loader serves a
  // 60s result cache and never resolves the Clerk session.
  if (gatingOn()) await connection()
  const board = await loadSportBoardForPage(sport, { day: f.day, date: f.date, view, kind })
  const copy = COPY[view]
  const today = siteDateKey()

  // In picks view, default to actionable rows (Official + EV Outliers) sorted with Official first
  let base = view === 'picks' && !tracked ? pickRows(board.rows) : board.rows
  if (view === 'picks') {
    const actionable = base.filter(r => isOfficialRow(r) || isOutlierRow(r))
    if (tierParam === 'official') {
      base = base.filter(isOfficialRow)
    } else if (tierParam === 'outliers') {
      base = base.filter(isOutlierRow)
    } else if (actionable.length > 0) {
      base = actionable
    }
    base = [...base].sort((a, b) => (Number(isOfficialRow(b)) - Number(isOfficialRow(a))) || (b.score - a.score))
  }

  const rows = applyPickFilters(base, f)
  const games = withCounts(board.games, base)
  const filtered = activeFilterCount(f) > 0
  const prices = f.day === 'today' && view === 'picks' && !tracked ? board.outliers.filter(o => !f.game || o.game_key === f.game) : []

  const emptyBody = base.length === 0
    ? (view === 'picks' && !tracked && board.rows.length > 0
      ? `No ${pillar.shortName} prop lines are banked for these games yet, so there is nothing to price a pick against. Every projection is in the Odds Terminal's Projections tab; picks appear here once lines post.`
      : board.emptyBody)
    : 'No rows match these filters. Clear a filter, or pick another game.'

  const tab: SportView = view
  return (
    <div>
      <SportTabs sport={sport} active={tab} />
      {(board.yesterday || board.days.length > 0) && (
        <AlignedBar style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap' }}>
          <DayToggle today={today} yesterday={board.yesterday} days={board.days} selected={board.date} pinDates={!tracked} />
        </AlignedBar>
      )}
      <GamesStrip games={games} selected={f.game} today={today} total={base.length} />
      <FilterBar sport={sport} kind={kind} showResult={tracked && f.day === 'yesterday'} />
      <div className="gutter-m max-w-page mx-auto p-6">
        <div className="mb-3.5 flex items-baseline justify-between gap-3 flex-wrap">
          <div className="flex items-baseline gap-3 flex-wrap">
            <h1 className="text-[20px] font-bold text-text-primary m-0">{kind === 'game' ? `${pillar.shortName} game picks` : copy.h1(pillar.shortName)}</h1>
            <span className="ui-heading text-micro py-[2px] px-2" title={pillar.description} style={{ color: pillar.accent, border: `1px solid ${pillar.accent}66`, borderRadius: '6px' }}>{STATUS_LABEL[pillar.status]}</span>
            {!tracked && (
              <span className="ui-heading text-micro text-warn border border-[color:rgba(245,158,11,0.45)] rounded-[6px] py-0.5 px-2"
               >Unvalidated</span>
            )}
          </div>
          <span className="mono text-compact text-text-secondary">{rows.length} {rows.length === 1 ? 'row' : 'rows'}{filtered ? ' after filters' : ''} &middot; {copy.sub}</span>
        </div>
        {view === 'picks' && (
          <div className="flex items-center gap-1.5 mb-3.5 flex-wrap">
            <span className="mono text-mini text-text-secondary">Tier</span>
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
                  href={`/picks/${sport}?${q}`}
                  className="mono text-mini min-h-11 inline-flex items-center py-2 px-3 no-underline box-border"
                  style={{
                    borderRadius: '4px',
                    background: active ? 'var(--color-separator-border)' : 'transparent',
                    border: `1px solid ${active ? pillar.accent : 'var(--color-separator-border)'}`,
                    color: active ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
                  }}
                >
                  {t.label}
                </Link>
              )
            })}
          </div>
        )}
        <SportProjectionsBoard sport={sport} rows={rows} status={board.status} statusWarn={board.statusWarn} emptyBody={emptyBody} />
        <LockedNotice locked={board.locked} shown={rows.length} what={`${pillar.shortName} ${copy.what}`} plan={`The full board is on the ${pillar.shortName} Pass or All-Access Pro.`} />
        {!tracked && view === 'picks' && f.day === 'today' && (
          <>
            <h2 className="text-[15px] font-semibold text-text-primary" style={{ margin: '32px 0 10px' }}>Market prices</h2>
            <MarketPrices sport={sport as ProjectionSport} rows={prices} locked={board.lockedPrices} block={board.block} confluenceKeys={board.confluenceKeys} />
          </>
        )}
        <EdgeEvLegend />
        <ScoreLegend />
      </div>
    </div>
  )
}
