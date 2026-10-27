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
import { getViewer } from '@/lib/auth'
import { withCounts } from '@/lib/board-games'
import { applyPickFilters, pickRows } from '@/lib/board-rows'
import { siteDateKey } from '@/lib/dates'
import type { PickKind } from '@/lib/pick-systems'
import { activeFilterCount, parsePickFilters } from '@/lib/picks-filters'
import { loadSportBoard, type BoardView } from '@/lib/sport-board-data'
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
  const board = await loadSportBoard(sport, await getViewer(), { day: f.day, date: f.date, view, kind })
  const copy = COPY[view]
  const today = siteDateKey()

  const isRowOfficial = (r: import('@/lib/board-rows').BoardRow) =>
    (r.marketEv ?? 0) >= 0.03 && (r.marketEv ?? 0) <= 0.10 && r.score >= 68 && (r.sport === 'mlb' ? r.isPick !== false : (!!r.confluence && (r.nBooks == null || r.nBooks >= 4) && (r.modelEdge == null || r.modelEdge >= 0.015)))
  const isRowOutlier = (r: import('@/lib/board-rows').BoardRow) =>
    (r.marketEv ?? 0) >= 0.025 && (r.marketEv ?? 0) <= 0.15 && (r.nBooks == null || r.nBooks >= 3) && !isRowOfficial(r)

  // In picks view, default to actionable rows (Official + EV Outliers) sorted with Official first
  let base = view === 'picks' && !tracked ? pickRows(board.rows) : board.rows
  if (view === 'picks') {
    const actionable = base.filter(r => isRowOfficial(r) || isRowOutlier(r))
    if (tierParam === 'official') {
      base = base.filter(isRowOfficial)
    } else if (tierParam === 'outliers') {
      base = base.filter(isRowOutlier)
    } else if (actionable.length > 0) {
      base = actionable
    }
    // Sort Official Plays first
    base = [...base].sort((a, b) => (Number(isRowOfficial(b)) - Number(isRowOfficial(a))) || (b.score - a.score))
  }

  const rows = applyPickFilters(base, f)
  const games = withCounts(board.games, base)
  const filtered = activeFilterCount(f) > 0
  const prices = f.day === 'today' && view === 'picks' && !tracked ? board.outliers.filter(o => !f.game || o.game_key === f.game) : []

  const emptyBody = base.length === 0
    ? (view === 'picks' && !tracked && board.rows.length > 0
      ? `No ${pillar.shortName} prop lines are banked for these games yet, so there is nothing to price a pick against. Every projection is on the Projections tab; picks appear here once lines post.`
      : board.emptyBody)
    : 'No rows match these filters. Clear a filter, or pick another game.'

  const tab: SportView = view
  return (
    <div>
      <SportTabs sport={sport} active={tab} />
      <AlignedBar style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap' }}>
        <DayToggle today={today} yesterday={board.yesterday} days={board.days} selected={board.date} pinDates={!tracked} />
        {tracked && view === 'picks' && (
          <nav aria-label="Pick type" style={{ display: 'flex', gap: '14px', fontSize: '12.5px' }}>
            <Link href="/picks/mlb" aria-current={kind !== 'game' ? 'page' : undefined} style={{ color: kind !== 'game' ? 'var(--chalk)' : 'var(--link)', fontWeight: kind !== 'game' ? 600 : 400, textDecoration: 'none' }}>Player picks</Link>
            <Link href="/picks/games" aria-current={kind === 'game' ? 'page' : undefined} style={{ color: kind === 'game' ? 'var(--chalk)' : 'var(--link)', fontWeight: kind === 'game' ? 600 : 400, textDecoration: 'none' }}>Game picks</Link>
          </nav>
        )}
      </AlignedBar>
      <GamesStrip games={games} selected={f.game} today={today} total={base.length} />
      <FilterBar sport={sport} kind={kind} showResult={tracked && f.day === 'yesterday'} />
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px' }}>
        <div style={{ marginBottom: '14px', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px', flexWrap: 'wrap' }}>
            <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>{kind === 'game' ? 'MLB game picks' : copy.h1(pillar.shortName)}</h1>
            <span className="dell-heading" title={pillar.description} style={{ fontSize: '10px', color: pillar.accent, border: `1px solid ${pillar.accent}66`, borderRadius: '6px', padding: '2px 8px' }}>{STATUS_LABEL[pillar.status]}</span>
          </div>
          <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>{rows.length} {rows.length === 1 ? 'row' : 'rows'}{filtered ? ' after filters' : ''} &middot; {copy.sub}</span>
        </div>
        {view === 'picks' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '14px', flexWrap: 'wrap' }}>
            <span className="mono" style={{ fontSize: '11px', color: 'var(--fog)', letterSpacing: '0.06em' }}>TIER:</span>
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
                  className="mono"
                  style={{
                    fontSize: '11px',
                    padding: '4px 8px',
                    borderRadius: '4px',
                    textDecoration: 'none',
                    background: active ? '#1e2230' : 'transparent',
                    border: `1px solid ${active ? pillar.accent : '#1e2230'}`,
                    color: active ? 'var(--chalk)' : 'var(--fog)',
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
            <h2 style={{ fontSize: '15px', fontWeight: 700, color: 'var(--chalk)', margin: '32px 0 10px' }}>Market prices</h2>
            <MarketPrices sport={sport as ProjectionSport} rows={prices} locked={board.lockedPrices} block={board.block} confluenceKeys={board.confluenceKeys} />
          </>
        )}
        <EdgeEvLegend />
        <ScoreLegend />
      </div>
    </div>
  )
}
