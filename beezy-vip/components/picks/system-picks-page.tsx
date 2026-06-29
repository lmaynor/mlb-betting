import { apiGetPicks as getPicks, apiGetStats } from '@/lib/betting-api'
import { PicksTable }   from '@/components/picks/picks-table'
import { SystemBadge }  from '@/components/ui/primitives'
import { siteDateKey }  from '@/lib/dates'
import { getPickSystemByKey } from '@/lib/pick-systems'
import { SYSTEM_COLOR } from '@/lib/tokens'
import Link             from 'next/link'

const B = '1px solid var(--basalt)'
const B_INNER = '1px solid var(--basalt)'

function StatBlock({ label, value, sub, accent, isLast }: { label: string; value: string; sub?: string; accent?: boolean; isLast?: boolean }) {
  return (
    <div style={{ padding: '20px', borderRight: isLast ? undefined : B_INNER }}>
      <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '8px' }}>{label}</div>
      <div className="mono" style={{ fontSize: '24px', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: accent ? 'var(--signal)' : 'var(--chalk)' }}>{value}</div>
      {sub && <div className="times" style={{ fontSize: '11px', color: 'var(--fog)' }}>{sub}</div>}
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
  const systemColor = SYSTEM_COLOR[system] ?? 'var(--silver)'

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
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 24px' }}>

      {/* Breadcrumb */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '20px' }}>
        <Link href="/picks/mlb" className="times" style={{ fontSize: '13px', color: 'var(--link)', textDecoration: 'none' }}>
          MLB Picks
        </Link>
        <span className="mono" style={{ fontSize: '11px', color: 'var(--iron)' }}>&middot;</span>
        <SystemBadge system={system} />
      </div>

      {/* Header */}
      <div style={{ marginBottom: '28px', display: 'flex', alignItems: 'flex-start', gap: '16px' }}>
        <span style={{ width: '4px', alignSelf: 'stretch', borderRadius: 'var(--radius-pill)', background: systemColor, flexShrink: 0, minHeight: '52px' }} />
        <div>
          <h1 className="dell-display" style={{ fontSize: '30px', color: 'var(--chalk)', marginBottom: '8px' }}>
            {meta.name}
          </h1>
          <p className="times" style={{ fontSize: '15px', color: 'var(--silver)', lineHeight: 1.6, maxWidth: '54ch', marginBottom: '10px' }}>
            {meta.description}
          </p>
          {meta.learnSlug && (
            <Link href={`/learn/${meta.learnSlug}`} className="times" style={{ fontSize: '13px', fontWeight: 600, color: 'var(--link)', textDecoration: 'none' }}>
              Learn about {meta.shortName} betting &rarr;
            </Link>
          )}
        </div>
      </div>

      {/* Stats grid */}
      <div className="stats-4up" style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', overflow: 'hidden', marginBottom: '20px', gap: 0 }}>
        <StatBlock label="Season ROI"  value={roi !== null ? `${roi >= 0 ? '+' : ''}${roi.toFixed(1)}%` : '--'} accent={roi !== null && roi > 0} />
        <StatBlock label="Win Rate"    value={winRate !== '--' ? `${winRate}%` : '--'} sub="W / (W+L)" />
        <StatBlock label="Total Bets"  value={String(totalBets)} sub="Paper mode" />
        <div style={{ padding: '20px' }}>
          <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '8px' }}>Avg Edge</div>
          <div className="mono" style={{ fontSize: '24px', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: 'var(--signal)' }}>{avgEdge !== '--' ? `+${avgEdge}%` : '--'}</div>
          <div className="times" style={{ fontSize: '11px', color: 'var(--fog)' }}>Model vs implied</div>
        </div>
      </div>

      {/* 200-bet gate */}
      <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', padding: '18px 20px', marginBottom: '32px', display: 'flex', alignItems: 'center', gap: '24px' }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
            <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)' }}>200-Bet Gate</span>
            <span className="mono" style={{ fontSize: '10px', color: 'var(--silver)' }}>{totalBets}/200</span>
          </div>
          <div style={{ height: '8px', borderRadius: 'var(--radius-pill)', background: 'var(--slate)', overflow: 'hidden' }}>
            <div style={{ height: '8px', borderRadius: 'var(--radius-pill)', background: systemColor, width: `${gatePct}%`, transition: 'width 0.4s var(--ease-out)' }} />
          </div>
        </div>
        <span className="dell-heading" style={{
          fontSize: '9px', letterSpacing: '0.08em', padding: '5px 11px', borderRadius: 'var(--radius-pill)',
          border: totalBets >= 200 ? '1px solid var(--win-border)' : '1px solid var(--basalt)',
          color: totalBets >= 200 ? 'var(--signal)' : 'var(--fog)',
          background: totalBets >= 200 ? 'var(--win-wash)' : 'var(--slate)',
        }}>
          {totalBets >= 200 ? 'GATE CLEARED' : 'PAPER MODE'}
        </span>
      </div>

      {/* Today's picks */}
      <div style={{ marginBottom: '40px' }}>
        <h2 className="dell-display" style={{ fontSize: '20px', color: 'var(--chalk)', marginBottom: '16px' }}>
          Today&rsquo;s {meta.shortName} picks
        </h2>
        {picks.length === 0 ? (
          <div style={{ border: B, padding: '40px', textAlign: 'center' as const }}>
            <p className="times" style={{ fontSize: '13px', color: 'var(--fog)' }}>No qualifying {meta.shortName} picks today.</p>
            <p className="times" style={{ fontSize: '12px', color: 'var(--fog)', marginTop: '6px' }}>Model runs in Central Time after lineups post.</p>
          </div>
        ) : (
          <PicksTable bets={picks} />
        )}
      </div>

      {/* Recent history */}
      {history.length > 0 && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
            <h2 className="dell-display" style={{ fontSize: '20px', color: 'var(--chalk)' }}>
              Recent results
            </h2>
            <Link href={`/results?system=${system}`} className="times" style={{ fontSize: '13px', fontWeight: 600, color: 'var(--link)', textDecoration: 'none' }}>
              Full history &rarr;
            </Link>
          </div>
          <PicksTable bets={history} />
        </div>
      )}
    </div>
  )
}
