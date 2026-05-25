import { apiGetStats, apiGetSparklineBySystem } from '@/lib/betting-api'
import Link from 'next/link'
import { SystemSparkline } from './system-sparkline'

const B = '0.5px solid #1f1f24'

const META: Record<string, { name: string; desc: string; href: string; bg: string; color: string; border: string }> = {
  NRFI: { name: 'No Run First Inning',  desc: 'Starter ERA, ump K-rate, weather, and park factors combine to predict scoreless first innings.', href: '/picks/mlb/nrfi', bg: '#052016', color: '#10b981', border: '0.5px solid #0f6e56' },
  HR:   { name: 'Home Run Props',        desc: 'Launch angle, exit velocity, and barrel rate vs. pitcher HR/9.',                                  href: '/picks/mlb/hr',   bg: '#1c1207', color: '#f59e0b', border: '0.5px solid #854f0b' },
  F5:   { name: 'First 5 Innings',       desc: 'Starting pitcher quality, bullpen rest, and lineup data for F5 moneylines.',                      href: '/picks/mlb/f5',   bg: '#040e1c', color: '#3b82f6', border: '0.5px solid #185fa5' },
  K:    { name: 'Strikeout Props',        desc: 'SwStr%, zone rate, and opponent K% to project starter strikeout over/unders.',                   href: '/picks/mlb/k',    bg: '#0e0718', color: '#a78bfa', border: '0.5px solid #534ab7' },
  OUTS: { name: 'Pitcher Outs Props',    desc: 'IP projection via Normal model for DraftKings pitcher outs markets.',                              href: '/picks/mlb/outs', bg: '#1a0d05', color: '#fb923c', border: '0.5px solid #9a3412' },
}

export async function ModelsGrid() {
  let stats: Array<{ system: string; win_rate: number; roi: number; total_bets: number }> = []
  let sparklines: Record<string, Awaited<ReturnType<typeof apiGetSparklineBySystem>>> = {}
  try {
    const db = await apiGetStats().then(s => s.bySystem)
    stats = db.map(s => ({
      system: s.system, win_rate: parseFloat(String(s.win_rate)),
      roi: parseFloat(String(s.roi ?? 0)), total_bets: parseInt(String(s.total_bets)),
    }))
    const sparklineResults = await Promise.allSettled(
      stats.map(s => apiGetSparklineBySystem(s.system, 30))
    )
    sparklineResults.forEach((r, i) => {
      if (r.status === 'fulfilled') sparklines[stats[i].system] = r.value
    })
  } catch { /* API unavailable -- render empty */ }

  if (stats.length === 0) return (
    <section style={{ padding: '24px 20px', borderBottom: B }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px' }}>
        <span className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)' }}>Active systems</span>
        <Link href="/models" style={{ fontSize: '11px', color: '#3b82f6', textDecoration: 'none' }}>Full methodology &rarr;</Link>
      </div>
      <div style={{ border: B, padding: '40px', textAlign: 'center' }}>
        <p className="mono" style={{ fontSize: '11px', color: '#71717a' }}>Loading systems&hellip;</p>
      </div>
    </section>
  )

  const items = [...stats, null] // null = ghost card

  return (
    <section style={{ padding: '24px 20px', borderBottom: B }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px' }}>
        <span className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)' }}>Active systems</span>
        <Link href="/models" style={{ fontSize: '11px', color: '#3b82f6', textDecoration: 'none' }}>Full methodology &rarr;</Link>
      </div>

      <div className="systems-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)', border: B }}>
        {items.map((s, i) => {
          const col = i % 3
          const row = Math.floor(i / 3)
          const cellStyle: React.CSSProperties = {
            borderRight:  col < 2 ? B : undefined,
            borderBottom: row < 1 ? B : undefined,
          }

          if (!s) return (
            <div key="more" style={{ ...cellStyle, background: '#0d0d0f', minHeight: '160px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <div style={{ textAlign: 'center' }}>
                <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#2a2a31', marginBottom: '4px' }}>More coming soon</div>
                <div style={{ fontSize: '10px', color: '#2a2a31' }}>Batter props &middot; Live models</div>
              </div>
            </div>
          )

          const meta = META[s.system]
          if (!meta) return null
          const roiPos = s.roi >= 0

          return (
            <Link key={s.system} href={meta.href} className="card-hover" style={{ ...cellStyle, display: 'block', padding: '16px', background: '#0a0a0c', textDecoration: 'none', borderRadius: 'var(--radius)', boxShadow: 'var(--shadow-card)' }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '10px', gap: '8px' }}>
                <span className="mono" style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '0.06em', padding: '3px 7px', background: meta.bg, color: meta.color, border: meta.border, borderRadius: 'var(--radius-sm)' }}>
                  {s.system}
                </span>
                <span className="mono" style={{ fontSize: '12px', fontWeight: 600, color: roiPos ? '#10b981' : '#ef4444' }}>
                  {roiPos ? '+' : ''}{s.roi.toFixed(1)}%
                </span>
              </div>
              <div style={{ fontSize: '12px', fontWeight: 600, color: '#f5f5f7', marginBottom: '4px' }}>{meta.name}</div>
              <div style={{ fontSize: '11px', color: '#71717a', lineHeight: 1.5, marginBottom: '12px' }}>{meta.desc}</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '8px', paddingTop: '10px', borderTop: B }}>
                {[['WR', s.win_rate.toFixed(1)+'%'], ['Bets', String(s.total_bets)], ['Gate', s.total_bets+'/200']].map(([label, val]) => (
                  <div key={label}>
                    <div className="mono" style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.08em', color: '#71717a' }}>{label}</div>
                    <div className="mono" style={{ fontSize: '11px', fontWeight: 600, color: label === 'Gate' ? '#71717a' : '#f5f5f7' }}>{val}</div>
                  </div>
                ))}
              </div>
              {sparklines[s.system] && (
                <SystemSparkline data={sparklines[s.system]} color={meta.color} />
              )}
            </Link>
          )
        })}
      </div>
    </section>
  )
}
