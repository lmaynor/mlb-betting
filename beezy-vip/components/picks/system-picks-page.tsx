import { apiGetPicks as getPicks, apiGetStats } from '@/lib/betting-api'
import { PicksTable }   from '@/components/picks/picks-table'
import { SystemBadge }  from '@/components/ui/primitives'
import { siteDateKey }  from '@/lib/dates'
import { getPickSystemByKey } from '@/lib/pick-systems'
import { SYSTEM_COLOR } from '@/lib/tokens'
import Link             from 'next/link'

const B = '0.5px solid #1f1f24'

// isLast removes the right border on the final cell so it doesn't bleed into the container border
function StatBlock({ label, value, sub, accent, isLast }: { label: string; value: string; sub?: string; accent?: boolean; isLast?: boolean }) {
  return (
    <div style={{ padding: '18px', borderRight: isLast ? undefined : B }}>
      <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: '#71717a', marginBottom: '6px' }}>{label}</div>
      <div className="mono" style={{ fontSize: '22px', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: accent ? '#10b981' : '#f5f5f7' }}>{value}</div>
      {sub && <div style={{ fontSize: '11px', color: '#71717a' }}>{sub}</div>}
    </div>
  )
}

export async function SystemPicksPage({ system }: { system: string }) {
  const meta = getPickSystemByKey(system) ?? {
    key: system,
    slug: system.toLowerCase(),
    name: system,
    shortName: system,
    description: 'Today\'s model-qualified picks from Beezy.FYI.',
  }
  const systemColor = SYSTEM_COLOR[system] ?? '#10b981'

  const [picks, allStats] = await Promise.all([
    getPicks({ system, date: siteDateKey(), limit: 50 }).catch(() => []),
    apiGetStats().then(s => s.bySystem).catch(() => []),
  ])

  const stats     = allStats.find(s => s.system === system)
  const winRate   = stats ? parseFloat(String(stats.win_rate)).toFixed(1) : '--'
  const roi       = stats ? parseFloat(String(stats.roi)) : null
  const totalBets = stats ? parseInt(String(stats.total_bets)) : 0
  const avgEdge   = stats ? parseFloat(String(stats.avg_edge)).toFixed(1) : '--'

  const history = await getPicks({ system, status: 'settled', limit: 20 }).catch(() => [])
  const gatePct = Math.min(100, (totalBets / 200) * 100)

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>

      {/* Breadcrumb */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '20px' }}>
        <Link href="/picks/mlb" className="mono" style={{ fontSize: '11px', color: '#71717a', textDecoration: 'none' }}>
          MLB Picks
        </Link>
        <span className="mono" style={{ fontSize: '11px', color: '#2a2a31' }}>&middot;</span>
        <SystemBadge system={system} />
      </div>

      {/* Header */}
      <div style={{ marginBottom: '28px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 600, color: '#f5f5f7', marginBottom: '8px', letterSpacing: '-0.01em' }}>
          {meta.name}
        </h1>
        <p style={{ fontSize: '13px', color: '#a1a1aa', lineHeight: 1.65, maxWidth: '480px', marginBottom: '10px' }}>
          {meta.description}
        </p>
        {meta.learnSlug && (
          <Link href={`/learn/${meta.learnSlug}`} className="mono" style={{ fontSize: '11px', color: '#71717a', textDecoration: 'none' }}>
            Learn about {meta.shortName} betting &rarr;
          </Link>
        )}
      </div>

      {/* Stats grid — responsive via .stats-4up class */}
      <div className="stats-4up" style={{ border: B, marginBottom: '20px' }}>
        <StatBlock label="Season ROI"  value={roi !== null ? `${roi >= 0 ? '+' : ''}${roi.toFixed(1)}%` : '--'} accent={roi !== null && roi > 0} />
        <StatBlock label="Win Rate"    value={winRate !== '--' ? `${winRate}%` : '--'} sub="W / (W+L)" />
        <StatBlock label="Total Bets"  value={String(totalBets)} sub="Paper mode" />
        {/* Last cell: raw div so no isLast needed on StatBlock; raw div has no borderRight */}
        <div style={{ padding: '18px' }}>
          <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: '#71717a', marginBottom: '6px' }}>Avg Edge</div>
          <div className="mono" style={{ fontSize: '22px', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: '#f5f5f7' }}>{avgEdge !== '--' ? `+${avgEdge}%` : '--'}</div>
          <div style={{ fontSize: '11px', color: '#71717a' }}>Model vs implied</div>
        </div>
      </div>

      {/* 200-bet gate */}
      <div style={{ border: B, padding: '16px 18px', marginBottom: '28px', display: 'flex', alignItems: 'center', gap: '20px' }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
            <span className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: '#71717a' }}>200-Bet Gate</span>
            <span className="mono" style={{ fontSize: '9px', color: '#71717a' }}>{totalBets}/200</span>
          </div>
          <div style={{ height: '2px', background: '#1f1f24' }}>
            <div style={{ height: '2px', background: systemColor, width: `${gatePct}%`, transition: 'width 0.3s' }} />
          </div>
        </div>
        <span className="mono" style={{
          fontSize: '9px', letterSpacing: '0.08em', padding: '4px 10px',
          border: totalBets >= 200 ? '0.5px solid #0f6e56' : B,
          color: totalBets >= 200 ? '#10b981' : '#71717a',
          background: totalBets >= 200 ? '#052016' : 'transparent',
        }}>
          {totalBets >= 200 ? 'GATE CLEARED' : 'PAPER MODE'}
        </span>
      </div>

      {/* Today's picks */}
      <div style={{ marginBottom: '36px' }}>
        <h2 style={{ fontSize: '12px', fontWeight: 600, color: '#f5f5f7', letterSpacing: '0.06em', textTransform: 'uppercase' as const, marginBottom: '14px' }}>
          Today&apos;s {meta.shortName} Picks
        </h2>
        {picks.length === 0 ? (
          <div style={{ border: B, padding: '40px', textAlign: 'center' as const }}>
            <p className="mono" style={{ fontSize: '12px', color: '#71717a' }}>No qualifying {meta.shortName} picks today.</p>
            <p className="mono" style={{ fontSize: '11px', color: '#71717a', marginTop: '6px' }}>Model runs in Central Time after lineups post.</p>
          </div>
        ) : (
          <PicksTable bets={picks} />
        )}
      </div>

      {/* Recent history */}
      {history.length > 0 && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
            <h2 style={{ fontSize: '12px', fontWeight: 600, color: '#f5f5f7', letterSpacing: '0.06em', textTransform: 'uppercase' as const }}>
              Recent Results
            </h2>
            <Link href={`/results?system=${system}`} className="mono" style={{ fontSize: '11px', color: '#71717a', textDecoration: 'none' }}>
              Full History &rarr;
            </Link>
          </div>
          <PicksTable bets={history} />
        </div>
      )}
    </div>
  )
}
