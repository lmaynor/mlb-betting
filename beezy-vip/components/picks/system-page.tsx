import Link from 'next/link'
import { AlignedBar } from '@/components/picks/aligned-bar'
import { DayToggle } from '@/components/picks/day-toggle'
import { FilterBar } from '@/components/picks/filter-bar'
import { GamesStrip } from '@/components/picks/games-strip'
import { LockedNotice } from '@/components/picks/locked-notice'
import { MarketPrices } from '@/components/picks/market-prices'
import { SportProjectionsBoard } from '@/components/picks/sport-projections-board'
import { SportTabs } from '@/components/picks/sport-tabs'
import { SystemMethodology } from '@/components/picks/system-methodology'
import { SystemStrip } from '@/components/picks/system-strip'
import { getViewer } from '@/lib/auth'
import { apiGetStats } from '@/lib/betting-api'
import { withCounts } from '@/lib/board-games'
import { applyPickFilters } from '@/lib/board-rows'
import { siteDateKey } from '@/lib/dates'
import type { PickSystem } from '@/lib/pick-systems'
import { liveAccuracy } from '@/lib/prop-model-specs'
import { parsePickFilters } from '@/lib/picks-filters'
import { dayKey, loadSportBoard } from '@/lib/sport-board-data'
import { SPORT_PILLARS, type ProjectionSport } from '@/lib/sports'
import { SURFACE_CARD, SYSTEM_COLOR } from '@/lib/tokens'

const finite = (v: unknown) => { const n = parseFloat(String(v ?? '')); return Number.isFinite(n) ? n : null }
const SURFACE = SURFACE_CARD

/**
 * One system of any sport: the same shell as the picks board (day, games, filters, table) narrowed to that market, a one-line
 * summary (the settled record for MLB; rows today and live accuracy for the others), the market prices for projection
 * sports and the methodology. A system with a data gap shows the gap instead of a board.
 */
export async function SystemPage({ system, searchParams }: { system: PickSystem; searchParams: Promise<Record<string, string>> }) {
  const sport = system.sport
  const pillar = SPORT_PILLARS[sport]
  const tracked = pillar.board === 'tracked'
  const color = SYSTEM_COLOR[system.key] ?? pillar.accent
  const f = parsePickFilters(await searchParams)
  const today = siteDateKey()
  const [board, allStats] = await Promise.all([
    system.gap ? null : getViewer().then(v => loadSportBoard(sport, v, { day: f.day, date: f.date, view: 'picks' })),
    tracked ? apiGetStats().then(s => s.bySystem).catch(() => []) : Promise.resolve([]),
  ])
  const base = board ? board.rows.filter(r => r.system === system.key) : []
  const rows = applyPickFilters(base, { ...f, market: undefined })
  const games = board ? withCounts(board.games, base).filter(g => g.count > 0 || tracked === false) : []
  const priced = base.filter(r => r.hasLine).length
  const confluence = base.filter(r => r.confluence).length
  const live = tracked ? null : liveAccuracy(sport, system.statKey)
  const stat = allStats.find(s => s.system === system.key)
  const n = finite(stat?.total_bets) ?? 0
  const roi = finite(stat?.roi)
  const winRate = finite(stat?.win_rate)
  const prices = board && !tracked && f.day === 'today' ? board.outliers.filter(o => (o.stat ?? o.market) === system.statKey && (!f.game || o.game_key === f.game)) : []

  return (
    <div>
      <SportTabs sport={sport} active="systems" />
      <SystemStrip sport={sport} active={system.key} />
      {board && (
        <>
          <AlignedBar>
            <DayToggle day={board.day} today={today} yesterday={dayKey('yesterday')} upcoming={board.days} viewDate={board.date} />
          </AlignedBar>
          <GamesStrip games={games} selected={f.game} today={today} total={base.length} />
          <FilterBar sport={sport} fixedMarket showResult={tracked && f.day === 'yesterday'} />
        </>
      )}
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap', marginBottom: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px', flexWrap: 'wrap' }}>
            <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>{pillar.shortName} {system.name}</h1>
            <span className="dell-heading" style={{ fontSize: '10px', color, border: `1px solid ${color}66`, borderRadius: '6px', padding: '2px 8px' }}>
              {system.market === 'game' ? 'GAME' : 'PROP'} / {system.gap ? 'DATA GAP' : tracked ? (pillar.markets.find(m => m.system === system.key)?.status ?? 'PAPER').toUpperCase() : 'UNVALIDATED'}
            </span>
          </div>
          <Link href={`/picks/${sport}/systems`} style={{ fontSize: '12.5px', color: 'var(--link)' }}>&larr; All {pillar.shortName} systems</Link>
        </div>

        {!system.gap && (
          <div className="mono" style={{ ...SURFACE, padding: '9px 14px', marginBottom: '16px', fontSize: '12.5px', color: 'var(--silver)', display: 'flex', gap: '6px 16px', flexWrap: 'wrap' }}>
            {tracked ? (
              <>
                <span>{n} settled</span>
                <span>{winRate != null ? `${winRate.toFixed(1)}% win` : '-- win'}</span>
                <span style={{ color: roi == null ? 'var(--fog)' : roi >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{roi == null ? '-- ROI' : `${roi >= 0 ? '+' : ''}${roi.toFixed(1)}% ROI`}</span>
                <span style={{ color: n >= 200 ? 'var(--signal)' : 'var(--fog)' }}>{n >= 200 ? '200+ bets, paper' : `${n}/200 bets, paper`}</span>
                <Link href={`/results?system=${system.key}`} style={{ color: 'var(--link)' }}>Full history &rarr;</Link>
              </>
            ) : (
              <>
                <span>{base.length} projections</span>
                <span>{priced} with a posted line</span>
                <span>{confluence} model + market</span>
                {live && <span title={live.method} style={{ color: live.dMae < 0 ? 'var(--signal)' : 'var(--silver)' }}>MAE {live.mae.toFixed(live.mae >= 10 ? 1 : 3)} vs {live.baselineMae.toFixed(live.baselineMae >= 10 ? 1 : 3)} (last-5 average)</span>}
                <span style={{ color: 'var(--fog)' }}>not tracked in the public results</span>
              </>
            )}
          </div>
        )}

        {board && (
          <>
            <SportProjectionsBoard sport={sport} rows={rows} status={board.status} statusWarn={board.statusWarn}
              emptyBody={base.length === 0
                ? (tracked ? `No ${system.shortName} picks ${f.day === 'today' ? 'yet today' : 'yesterday'}. The model card publishes after the ~11:00 AM CT run.` : `No ${system.name.toLowerCase()} projections right now: no ${pillar.shortName} game is on the schedule, or its lines have not posted.`)
                : 'No rows match these filters.'} />
            <LockedNotice locked={board.locked} shown={rows.length} what={`${pillar.shortName} ${system.shortName} rows`} plan={`The full board is on the ${pillar.shortName} Pass or All-Access Pro.`} />
            {!tracked && f.day === 'today' && (
              <>
                <h2 style={{ fontSize: '15px', fontWeight: 700, color: 'var(--chalk)', margin: '32px 0 10px' }}>Market prices</h2>
                <MarketPrices sport={sport as ProjectionSport} rows={prices} locked={0} block={board.block} confluenceKeys={board.confluenceKeys} />
              </>
            )}
          </>
        )}
        {system.gap && <div role="note" style={{ ...SURFACE, padding: '14px 16px', fontSize: '13px', color: 'var(--silver)', lineHeight: 1.55 }}><strong style={{ color: '#f59e0b' }}>DATA GAP.</strong> {system.gap}</div>}

        <h2 style={{ fontSize: '15px', fontWeight: 700, color: 'var(--chalk)', margin: '32px 0 10px' }}>Methodology</h2>
        <SystemMethodology system={system} />
        <p style={{ fontSize: '11.5px', color: 'var(--fog)', margin: '14px 2px 0', lineHeight: 1.55, maxWidth: '90ch' }}>{system.description}</p>
      </div>
    </div>
  )
}
