import { SportTabs } from '@/components/picks/sport-tabs'
import { SystemStrip } from '@/components/picks/system-strip'
import { SystemsTable } from '@/components/picks/systems-table'
import { SportEvidenceNote } from '@/components/picks/system-methodology'
import { apiGetSportProjections, apiGetStats } from '@/lib/betting-api'
import { slateRows } from '@/lib/board-days'
import { systemTodayCounts } from '@/lib/board-rows'
import { SPORT_PILLARS, type ProjectionSport, type SportSlug } from '@/lib/sports'

/**
 * A sport's SYSTEMS tab: every system in one table (the settled record for MLB; today's rows and live accuracy for the
 * projection sports), the strip to open any one of them, and the sport-level evidence note. Its own page, not a footer.
 */
export async function SystemsPage({ sport }: { sport: SportSlug }) {
  const pillar = SPORT_PILLARS[sport]
  const tracked = pillar.board === 'tracked'
  const [stats, today] = await Promise.all([
    tracked ? apiGetStats().then(r => r.bySystem).catch(() => []) : Promise.resolve([]),
    tracked ? Promise.resolve({}) : apiGetSportProjections(sport as ProjectionSport).then(p => systemTodayCounts(slateRows(p.rows))).catch(() => ({})),
  ])
  return (
    <div>
      <SportTabs sport={sport} active="systems" />
      <SystemStrip sport={sport} active={null} />
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px' }}>
        <SystemsTable sport={sport} stats={stats} today={today} level="h1" />
        {!tracked && <SportEvidenceNote sport={sport as ProjectionSport} />}
      </div>
    </div>
  )
}
