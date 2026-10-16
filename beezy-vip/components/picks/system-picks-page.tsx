import Link from 'next/link'
import { LockedNotice } from '@/components/picks/locked-notice'
import { PicksTable } from '@/components/picks/picks-table'
import { limitBets } from '@/lib/access-bets'
import { getViewer } from '@/lib/auth'
import { apiGetPicks as getPicks, apiGetStats } from '@/lib/betting-api'
import { siteDateKey } from '@/lib/dates'
import { getPickSystemByKey, kindOfSystem } from '@/lib/pick-systems'
import { SPORT_PILLARS } from '@/lib/sports'
import { SYSTEM_COLOR } from '@/lib/tokens'

const finite = (v: unknown) => { const n = parseFloat(String(v ?? '')); return Number.isFinite(n) ? n : null }
const SURFACE = { background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px' } as const

/** One MLB system: its record, today's picks (access policy applies) and recent settled picks. Reached from the systems footer. */
export async function SystemPicksPage({ system }: { system: string }) {
  const meta = getPickSystemByKey(system) ?? {
    key: system, slug: system.toLowerCase(), name: system, shortName: system, description: 'Today\'s model-qualified picks from Beezy.FYI.',
  }
  const kind = kindOfSystem(system)
  const color = SYSTEM_COLOR[system] ?? 'var(--silver)'
  const today = siteDateKey()

  const [allToday, allStats, viewer, history] = await Promise.all([
    getPicks({ system, date: today, limit: 50 }).catch(() => []),
    apiGetStats().then(s => s.bySystem).catch(() => []),
    getViewer(),
    getPicks({ system, status: 'settled', limit: 20 }).catch(() => []),
  ])
  // Today's picks are the paid surface (lib/access.ts); the record below stays public.
  const { bets: picks, locked } = limitBets(allToday, viewer, today)

  const stats = allStats.find(s => s.system === system)
  const bets = stats ? Math.round(finite(stats.total_bets) ?? 0) : 0
  const winRate = stats ? finite(stats.win_rate) : null
  const roi = stats ? finite(stats.roi) : null
  const avgEdge = stats ? finite(stats.avg_edge) : null
  const status = SPORT_PILLARS.mlb.markets.find(m => m.key === system)?.status ?? null
  const back = kind === 'player' ? { href: '/picks/mlb#systems', label: 'MLB player picks' } : { href: '/picks/games#systems', label: 'Game picks' }

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap', marginBottom: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px', flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>{meta.name}</h1>
          <span className="dell-heading" style={{ fontSize: '10px', color, border: `1px solid ${color}66`, borderRadius: '6px', padding: '2px 8px' }}>
            {kind === 'player' ? 'PLAYER' : 'GAME'}{status ? ` / ${status.toUpperCase()}` : ''}
          </span>
        </div>
        <Link href={back.href} style={{ fontSize: '12.5px', color: 'var(--link)' }}>&larr; {back.label}</Link>
      </div>

      {/* one inline metric strip, not a grid of cards */}
      <div className="mono" style={{ ...SURFACE, padding: '9px 14px', marginBottom: '16px', fontSize: '12.5px', color: 'var(--silver)', display: 'flex', gap: '6px 16px', flexWrap: 'wrap' }}>
        <span>{bets} settled</span>
        <span>{winRate != null ? `${winRate.toFixed(1)}% win` : '-- win'}</span>
        <span style={{ color: roi == null ? 'var(--fog)' : roi >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{roi == null ? '-- ROI' : `${roi >= 0 ? '+' : ''}${roi.toFixed(1)}% ROI`}</span>
        <span>{avgEdge != null ? `${avgEdge.toFixed(1)}% avg edge` : '-- avg edge'}</span>
        <span style={{ color: bets >= 200 ? 'var(--signal)' : 'var(--fog)' }}>{bets >= 200 ? '200+ bets, paper' : `${bets}/200 bets, paper`}</span>
      </div>

      <h2 style={{ fontSize: '15px', fontWeight: 700, color: 'var(--chalk)', margin: '0 0 10px' }}>Today</h2>
      {picks.length === 0 && locked === 0 ? (
        <div style={{ ...SURFACE, padding: '28px 20px', textAlign: 'center', color: 'var(--fog)', fontSize: '13px' }}>
          No qualifying {meta.shortName} picks today. The model card publishes after the ~11:00 AM CT run.
        </div>
      ) : picks.length > 0 ? (
        <PicksTable bets={picks} />
      ) : null}
      <LockedNotice locked={locked} shown={picks.length} what={`${meta.shortName} picks`} plan="The full card is on the MLB Pass or All-Access Pro." />

      {history.length > 0 && (
        <>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '12px', margin: '28px 0 10px' }}>
            <h2 style={{ fontSize: '15px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>Recent results</h2>
            <Link href={`/results?system=${system}`} style={{ fontSize: '12.5px', color: 'var(--link)' }}>Full history &rarr;</Link>
          </div>
          <PicksTable bets={history} />
        </>
      )}

      <p style={{ fontSize: '11.5px', color: 'var(--fog)', margin: '16px 2px 0', lineHeight: 1.55, maxWidth: '90ch' }}>{meta.description}</p>
    </div>
  )
}
