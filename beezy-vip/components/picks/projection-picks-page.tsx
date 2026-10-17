import { FilterBar } from '@/components/picks/filter-bar'
import { LockedNotice } from '@/components/picks/locked-notice'
import { MarketPrices } from '@/components/picks/market-prices'
import { SportProjectionsBoard } from '@/components/picks/sport-projections-board'
import { SportTabs } from '@/components/picks/sport-tabs'
import { SportEvidenceNote } from '@/components/picks/system-methodology'
import { SystemsFooter } from '@/components/picks/systems-footer'
import { ScoreLegend } from '@/components/ui/score-legend'
import { getViewer } from '@/lib/auth'
import { applyPickFilters, pickRows } from '@/lib/board-rows'
import { formatDateKey, siteDateKey } from '@/lib/dates'
import { parsePickFilters } from '@/lib/picks-filters'
import { loadSportBoard } from '@/lib/sport-board-data'
import { SPORT_PILLARS, STATUS_LABEL, type ProjectionSport } from '@/lib/sports'

/**
 * A projection sport's picks board (NFL, NHL, NBA and any sport whose registry `board` is `projection`): the plays where
 * the projection and the market agree or a price beats the consensus, then the live market prices, then the systems.
 * Same header, filter bar and footer as the MLB card.
 */
export async function ProjectionPicksPage({ sport, searchParams }: { sport: ProjectionSport; searchParams: Promise<Record<string, string>> }) {
  const pillar = SPORT_PILLARS[sport]
  const f = parsePickFilters(await searchParams)
  const board = await loadSportBoard(sport, await getViewer())
  const picks = applyPickFilters(pickRows(board.rows), f)
  const date = siteDateKey()
  return (
    <div>
      <SportTabs sport={sport} active="picks" />
      <FilterBar sport={sport} />
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '32px 24px' }}>
        <div style={{ marginBottom: '14px', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px', flexWrap: 'wrap' }}>
            <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>{pillar.shortName} picks</h1>
            <span className="dell-heading" title={pillar.description} style={{ fontSize: '10px', color: pillar.accent, border: `1px solid ${pillar.accent}66`, borderRadius: '6px', padding: '2px 8px' }}>{STATUS_LABEL[pillar.status]}</span>
          </div>
          <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>
            {formatDateKey(date, { weekday: 'short', month: 'short', day: 'numeric' })} &middot; where the projection and the market agree, ranked by Beezy Score
          </span>
        </div>
        <SportProjectionsBoard
          sport={sport} rows={picks} controls={false} status={board.status}
          emptyBody={board.rows.length > 0
            ? 'No pick clears these filters. Rows without a posted line, or without a positive score, are on the Projections tab.'
            : board.emptyBody}
        />
        <LockedNotice locked={board.locked} shown={picks.length} what={`${pillar.shortName} picks`} plan={`The full board is on the ${pillar.shortName} Pass or All-Access Pro.`} />

        <h2 style={{ fontSize: '15px', fontWeight: 700, color: 'var(--chalk)', margin: '32px 0 10px' }}>Market prices</h2>
        <MarketPrices sport={sport} rows={board.outliers} locked={board.lockedPrices} block={board.block} confluenceKeys={board.confluenceKeys} />

        <ScoreLegend />
        <SystemsFooter sport={sport} />
        <SportEvidenceNote sport={sport} />
      </div>
    </div>
  )
}
