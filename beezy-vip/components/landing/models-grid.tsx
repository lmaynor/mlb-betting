import { apiGetStats, apiGetSparklineBySystem } from '@/lib/betting-api'
import Link from 'next/link'
import { SYSTEM_COLOR, SURFACE_CARD } from '@/lib/tokens'
import { SPORT_LIST } from '@/lib/sports'
import { ModelsGridClient, type SystemCard } from './models-grid-client'
import { SectionHeader } from './section-header'

const META: Record<string, { name: string; desc: string; href: string }> = {
  NRFI:        { name: 'No Run First Inning',   desc: 'Scoreless first innings.', href: '/picks/mlb/nrfi' },
  HR:          { name: 'Home Run Props',         desc: 'Launch angle, exit velocity and barrel rate vs. pitcher HR/9.', href: '/picks/mlb/hr' },
  F5:          { name: 'First 5 Innings',        desc: 'F5 moneylines.', href: '/picks/mlb/f5' },
  K:           { name: 'Strikeout Props',         desc: 'Starter strikeout over/unders.', href: '/picks/mlb/k' },
  OUTS:        { name: 'Pitcher Outs Props',     desc: 'Negative-binomial outs projection.', href: '/picks/mlb/outs' },
  GAME:        { name: 'Full Game',              desc: 'Full-game moneyline. Log-only until 200 bets.', href: '/picks/mlb/game' },
  F3:          { name: 'First 3 Innings',         desc: 'Early-game starter window.', href: '/picks/mlb/f3' },
  F1H:         { name: 'First Half',              desc: 'Innings 1-4 moneyline. Log-only.', href: '/picks/mlb/f1h' },
  F7:          { name: 'First 7 Innings',         desc: 'Late starter and bridge-relief pricing.', href: '/picks/mlb/f7' },
  BATTER_K:    { name: 'Batter Strikeouts',       desc: 'Batter K props.', href: '/picks/mlb/batter-k' },
  BATTER_TB:   { name: 'Total Bases',            desc: 'Total-base props.', href: '/picks/mlb/batter-tb' },
  BATTER_HITS: { name: 'Hits Props',             desc: 'Hits props.', href: '/picks/mlb/batter-hits' },
  SB:          { name: 'Stolen Base Props',      desc: 'Stolen-base props.', href: '/picks/mlb/sb' },
  PITCHER_ER:  { name: 'Pitcher ER Props',       desc: 'Earned-runs props.', href: '/picks/mlb/pitcher-er' },
  '1I':        { name: 'First Inning Moneyline', desc: 'Three-way first-inning pricing.', href: '/picks/mlb/1i' },
}

export async function ModelsGrid() {
  let systems: SystemCard[] = []
  try {
    const stats = (await apiGetStats()).bySystem.map(s => ({
      system: s.system, win_rate: parseFloat(String(s.win_rate)),
      roi: s.roi == null ? null : parseFloat(String(s.roi)), total_bets: parseInt(String(s.total_bets)),
    }))
    const sl = await Promise.allSettled(stats.map(s => apiGetSparklineBySystem(s.system, 30)))
    systems = stats.filter(s => META[s.system])
      .sort((a, b) => {
        const ag = a.total_bets >= 200 ? 1 : 0, bg = b.total_bets >= 200 ? 1 : 0
        return ag !== bg ? bg - ag : (b.roi ?? -Infinity) - (a.roi ?? -Infinity)
      })
      .map(s => {
        const i = stats.indexOf(s), r = sl[i]
        return { system: s.system, ...META[s.system], tint: SYSTEM_COLOR[s.system] ?? 'var(--silver)', win_rate: s.win_rate, roi: s.roi, total_bets: s.total_bets, sparkline: r.status === 'fulfilled' ? r.value : null }
      })
  } catch { /* API unavailable: league cards still render */ }

  return (
    <section style={{ paddingTop: '32px' }}>
      <SectionHeader title="Coverage by league & system" href="/picks" link="Explore all models &rarr;" />
      <div className="models-grid" style={{ gridTemplateColumns: 'repeat(2, 1fr)', gap: '10px' }}>
        {SPORT_LIST.map(p => (
          <Link key={p.slug} href={`/picks/${p.slug}`} style={{ ...SURFACE_CARD, padding: '14px', textDecoration: 'none', borderLeft: `3px solid ${p.accent}`, display: 'block' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', marginBottom: '6px' }}>
              <span style={{ fontSize: '13px', fontWeight: 700, color: p.accent }}>{p.shortName}</span>
              <span className="mono" style={{ fontSize: '10px', color: '#7d8596' }}>{p.seasonBadge}</span>
            </div>
            <div style={{ fontSize: '12px', lineHeight: 1.5, color: '#9ca3af' }}>
              {p.markets.filter(m => m.kind === 'prop' || m.kind === 'game').slice(0, 6).map(m => m.label.replace(/ O\/U| \(Yes\/No\)/, '')).join(' \u00b7 ')}
            </div>
          </Link>
        ))}
      </div>
      {systems.length > 0 && (
        <details style={{ ...SURFACE_CARD, marginTop: '10px', padding: '10px 14px' }}>
          <summary style={{ cursor: 'pointer', fontSize: '12px', fontWeight: 600, color: 'var(--silver)' }}>MLB system records ({systems.length})</summary>
          <div style={{ marginTop: '12px' }}><ModelsGridClient systems={systems} /></div>
        </details>
      )}
    </section>
  )
}
