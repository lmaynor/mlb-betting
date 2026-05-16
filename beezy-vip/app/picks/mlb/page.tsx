export const dynamic = 'force-dynamic'

import Link from 'next/link'
import { apiGetStats } from '@/lib/betting-api'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'MLB Picks Today — All Systems',
  description: "Today's MLB picks from all Beezy.VIP machine learning systems: NRFI, HR, F5, K, and OUTS.",
}

const B = '0.5px solid #1f1f24'

const SYSTEMS = [
  { key: 'NRFI', slug: 'nrfi', name: 'No Run First Inning', bg: '#052016', color: '#10b981', border: '0.5px solid #0f6e56' },
  { key: 'HR',   slug: 'hr',   name: 'Home Run Props',       bg: '#1c1207', color: '#f59e0b', border: '0.5px solid #854f0b' },
  { key: 'F5',   slug: 'f5',   name: 'First 5 Innings',      bg: '#040e1c', color: '#3b82f6', border: '0.5px solid #185fa5' },
  { key: 'K',    slug: 'k',    name: 'Strikeout Props',       bg: '#0e0718', color: '#a78bfa', border: '0.5px solid #534ab7' },
  { key: 'OUTS', slug: 'outs', name: 'Pitcher Outs Props',   bg: '#1a0d05', color: '#fb923c', border: '0.5px solid #9a3412' },
]

export default async function MLBPicksPage() {
  const stats = await apiGetStats().then(s => s.bySystem).catch(() => [])

  const items = [...SYSTEMS, null] // ghost card

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '24px' }}>
        <p className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#10b981', marginBottom: '6px' }}>Picks</p>
        <h1 style={{ fontSize: '20px', fontWeight: 600, color: '#f5f5f7', marginBottom: '4px' }}>MLB Picks</h1>
        <p className="mono" style={{ fontSize: '13px', color: '#71717a' }}>5 systems · DraftKings · Paper mode</p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', border: B }}>
        {items.map((s, i) => {
          const col = i % 3
          const row = Math.floor(i / 3)
          const cellStyle: React.CSSProperties = {
            borderRight:  col < 2 ? B : undefined,
            borderBottom: row < 1 ? B : undefined,
          }

          if (!s) return (
            <div key="more" style={{ ...cellStyle, background: '#0d0d0f', minHeight: '140px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <div style={{ textAlign: 'center' }}>
                <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#2a2a31', marginBottom: '4px' }}>More coming soon</div>
                <div style={{ fontSize: '10px', color: '#2a2a31' }}>Batter props &middot; Live models</div>
              </div>
            </div>
          )

          const stat = stats.find(x => x.system === s.key)
          const roi  = stat ? parseFloat(String(stat.roi ?? 0)) : null
          const wr   = stat ? parseFloat(String(stat.win_rate)) : null
          const bets = stat ? parseInt(String(stat.total_bets)) : 0

          return (
            <Link key={s.key} href={`/picks/mlb/${s.slug}`} style={{ ...cellStyle, display: 'block', padding: '16px', background: '#0a0a0c', textDecoration: 'none' }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '10px', gap: '8px' }}>
                <span className="mono" style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '0.06em', padding: '3px 7px', background: s.bg, color: s.color, border: s.border }}>
                  {s.key}
                </span>
                {roi !== null && (
                  <span className="mono" style={{ fontSize: '12px', fontWeight: 600, color: roi >= 0 ? '#10b981' : '#ef4444' }}>
                    {roi >= 0 ? '+' : ''}{roi.toFixed(1)}%
                  </span>
                )}
              </div>
              <div style={{ fontSize: '13px', fontWeight: 600, color: '#f5f5f7', marginBottom: '12px' }}>{s.name}</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '8px', paddingTop: '10px', borderTop: B }}>
                <div>
                  <div className="mono" style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.08em', color: '#71717a' }}>Win Rate</div>
                  <div className="mono" style={{ fontSize: '12px', fontWeight: 600, color: '#f5f5f7' }}>{wr !== null ? `${wr.toFixed(1)}%` : '—'}</div>
                </div>
                <div>
                  <div className="mono" style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.08em', color: '#71717a' }}>Bets</div>
                  <div className="mono" style={{ fontSize: '12px', fontWeight: 600, color: '#f5f5f7' }}>{bets}</div>
                </div>
                <div>
                  <div className="mono" style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.08em', color: '#71717a' }}>Gate</div>
                  <div className="mono" style={{ fontSize: '12px', fontWeight: 600, color: '#71717a' }}>{bets}/200</div>
                </div>
              </div>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
