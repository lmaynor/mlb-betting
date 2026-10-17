import Link from 'next/link'
import { MarketPrices } from '@/components/picks/market-prices'
import { SportProjectionsBoard } from '@/components/picks/sport-projections-board'
import { SportTabs } from '@/components/picks/sport-tabs'
import { SystemMethodology } from '@/components/picks/system-methodology'
import { LockedNotice } from '@/components/picks/locked-notice'
import { getViewer } from '@/lib/auth'
import type { PickSystem } from '@/lib/pick-systems'
import { loadSportBoard } from '@/lib/sport-board-data'
import { SPORT_PILLARS, type ProjectionSport } from '@/lib/sports'
import { SYSTEM_COLOR } from '@/lib/tokens'

/**
 * One projection system (a market of NFL / NHL / NBA / any projection sport): today's rows and market prices for that one
 * stat, then the methodology card. A system with a data gap shows the gap instead of a board.
 */
export async function ProjectionSystemPage({ sport, system }: { sport: ProjectionSport; system: PickSystem }) {
  const pillar = SPORT_PILLARS[sport]
  const color = SYSTEM_COLOR[system.key] ?? pillar.accent
  const board = await loadSportBoard(sport, await getViewer())
  const rows = board.rows.filter(r => r.system === system.key)
  const outliers = board.outliers.filter(o => (o.stat ?? o.market) === system.statKey)
  const confluence = rows.filter(r => r.confluence).length
  const priced = rows.filter(r => r.hasLine).length
  return (
    <div>
      <SportTabs sport={sport} active={null} />
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap', marginBottom: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px', flexWrap: 'wrap' }}>
            <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>{pillar.shortName} {system.name}</h1>
            <span className="dell-heading" style={{ fontSize: '10px', color, border: `1px solid ${color}66`, borderRadius: '6px', padding: '2px 8px' }}>
              {system.market === 'game' ? 'GAME' : 'PROP'} / {system.gap ? 'DATA GAP' : 'UNVALIDATED'}
            </span>
          </div>
          <Link href={`/picks/${sport}#systems`} style={{ fontSize: '12.5px', color: 'var(--link)' }}>&larr; {pillar.shortName} systems</Link>
        </div>

        {!system.gap && (
          <div className="mono" style={{ background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', padding: '9px 14px', marginBottom: '16px', fontSize: '12.5px', color: 'var(--silver)', display: 'flex', gap: '6px 16px', flexWrap: 'wrap' }}>
            <span>{rows.length} projections</span>
            <span>{priced} with a posted line</span>
            <span>{confluence} model + market</span>
            <span style={{ color: 'var(--fog)' }}>not tracked in the public results</span>
          </div>
        )}

        {system.gap ? null : (
          <>
            <h2 style={{ fontSize: '15px', fontWeight: 700, color: 'var(--chalk)', margin: '0 0 10px' }}>Today</h2>
            <SportProjectionsBoard sport={sport} rows={rows} status={board.status} marketChips={false}
              emptyBody={`No ${system.name.toLowerCase()} projections right now: no ${pillar.shortName} game is on the slate, or its lines have not posted.`} />
            <LockedNotice locked={board.locked} shown={rows.length} what={`${pillar.shortName} ${system.shortName} projections`} plan={`The full board is on the ${pillar.shortName} Pass or All-Access Pro.`} />
            <h2 style={{ fontSize: '15px', fontWeight: 700, color: 'var(--chalk)', margin: '32px 0 10px' }}>Market prices</h2>
            <MarketPrices sport={sport} rows={outliers} locked={0} block={board.block} confluenceKeys={board.confluenceKeys} />
          </>
        )}

        <h2 style={{ fontSize: '15px', fontWeight: 700, color: 'var(--chalk)', margin: '32px 0 10px' }}>Methodology</h2>
        <SystemMethodology system={system} />
        <p style={{ fontSize: '11.5px', color: 'var(--fog)', margin: '14px 2px 0', lineHeight: 1.55, maxWidth: '90ch' }}>{system.description}</p>
      </div>
    </div>
  )
}
