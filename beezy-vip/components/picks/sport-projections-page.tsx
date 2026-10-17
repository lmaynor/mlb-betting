import { DayScroller } from '@/components/picks/day-scroller'
import { LockedNotice } from '@/components/picks/locked-notice'
import { SportProjectionsBoard } from '@/components/picks/sport-projections-board'
import { SportTabs } from '@/components/picks/sport-tabs'
import { SystemsFooter } from '@/components/picks/systems-footer'
import { getViewer } from '@/lib/auth'
import { apiGetStats } from '@/lib/betting-api'
import { sliceByDay } from '@/lib/board-days'
import { formatDateKey, siteDateKey } from '@/lib/dates'
import { loadSportBoard } from '@/lib/sport-board-data'
import { SPORT_PILLARS, type SportSlug } from '@/lib/sports'

/** The full model projection board for any sport: every row, with the board's own filters, then the systems footer. */
export async function SportProjectionsPage({ sport, searchParams }: { sport: SportSlug; searchParams: Promise<Record<string, string>> }) {
  const pillar = SPORT_PILLARS[sport]
  const [board, stats] = await Promise.all([
    getViewer().then(v => loadSportBoard(sport, v)),
    pillar.board === 'tracked' ? apiGetStats().then(r => r.bySystem).catch(() => []) : Promise.resolve([]),
  ])
  const date = siteDateKey()
  // MLB's board is today's model run (its own DateBar is on the picks card); the projection sports get the day strip here.
  const day = sliceByDay(board.rows, r => r.start, pillar.board === 'tracked' ? 'all' : (await searchParams).date)
  return (
    <div>
      <SportTabs sport={sport} active="projections" />
      {pillar.board !== 'tracked' && <DayScroller days={day.days} selected={day.selected} today={date} />}
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap', marginBottom: '14px' }}>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>{pillar.shortName} projections</h1>
          <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>
            {formatDateKey(date, { weekday: 'short', month: 'short', day: 'numeric' })} &middot; player props, the expected value beside the line
          </span>
        </div>
        <SportProjectionsBoard
          sport={sport} rows={day.rows} status={board.status} statusWarn={board.statusWarn}
          emptyBody={pillar.board === 'tracked' ? 'Today\u2019s projections publish with the ~11:00 AM CT model run, once lineups and prices are in.' : board.emptyBody}
        />
        <LockedNotice locked={board.locked} shown={day.rows.length} what={`${pillar.shortName} projections`} plan={`The full board is on the ${pillar.shortName} Pass or All-Access Pro.`} />
        <SystemsFooter sport={sport} stats={stats} today={board.today} />
      </div>
    </div>
  )
}
