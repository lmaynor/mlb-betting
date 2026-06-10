export const dynamic = 'force-dynamic'

import Link from 'next/link'
import { apiGetStats } from '@/lib/betting-api'
import { PICK_SYSTEMS, systemPill } from '@/lib/pick-systems'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'MLB Picks Today -- All Systems',
  description: "Today's MLB picks from all Beezy.FYI machine learning systems.",
}

const B = '1px solid #1f1f24'

export default async function MLBPicksPage() {
  const stats = await apiGetStats().then(s => s.bySystem).catch(() => [])
  const columns = 3
  const rows = Math.ceil(PICK_SYSTEMS.length / columns)

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '24px' }}>
        <p className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: '#888890', marginBottom: '6px' }}>Picks</p>
        <h1 className="dell-display" style={{ fontSize: '20px', color: '#f5f5f7', marginBottom: '4px' }}>MLB Picks</h1>
        <p className="mono" style={{ fontSize: '13px', color: '#888890' }}>{PICK_SYSTEMS.length} systems &middot; DraftKings &middot; Paper mode</p>
      </div>

      <div className="systems-grid" style={{ gridTemplateColumns: 'repeat(3,1fr)', border: B }}>
        {PICK_SYSTEMS.map((s, i) => {
          const col = i % columns
          const row = Math.floor(i / columns)
          const stat = stats.find(x => x.system === s.key)
          const roi = stat ? parseFloat(String(stat.roi ?? 0)) : null
          const wr = stat ? parseFloat(String(stat.win_rate)) : null
          const bets = stat ? parseInt(String(stat.total_bets)) : 0
          const pill = systemPill(s.key)

          return (
            <Link
              key={s.key}
              href={`/picks/mlb/${s.slug}`}
              style={{
                display: 'block',
                padding: '16px',
                background: '#0a0a0c',
                textDecoration: 'none',
                borderRight: col < columns - 1 ? B : undefined,
                borderBottom: row < rows - 1 ? B : undefined,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '10px', gap: '8px' }}>
                <span className="mono" style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '0.06em', padding: '3px 7px', background: pill.bg, color: pill.color, border: pill.border }}>
                  {s.shortName}
                </span>
                {roi !== null && (
                  <span className="mono" style={{ fontSize: '12px', fontWeight: 600, color: roi >= 0 ? '#b3bd95' : '#d77a7a' }}>
                    {roi >= 0 ? '+' : ''}{roi.toFixed(1)}%
                  </span>
                )}
              </div>
              <div style={{ fontSize: '13px', fontWeight: 600, color: '#f5f5f7', marginBottom: '12px' }}>{s.name}</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '8px', paddingTop: '10px', borderTop: B }}>
                <div>
                  <div className="mono" style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.08em', color: '#888890' }}>Win Rate</div>
                  <div className="mono" style={{ fontSize: '12px', fontWeight: 600, color: '#f5f5f7' }}>{wr !== null ? `${wr.toFixed(1)}%` : '--'}</div>
                </div>
                <div>
                  <div className="mono" style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.08em', color: '#888890' }}>Bets</div>
                  <div className="mono" style={{ fontSize: '12px', fontWeight: 600, color: '#f5f5f7' }}>{bets}</div>
                </div>
                <div>
                  <div className="mono" style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.08em', color: '#888890' }}>Gate</div>
                  <div className="mono" style={{ fontSize: '12px', fontWeight: 600, color: '#888890' }}>{bets}/200</div>
                </div>
              </div>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
