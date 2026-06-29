export const dynamic = 'force-dynamic'

import Link from 'next/link'
import { apiGetStats } from '@/lib/betting-api'
import { PICK_SYSTEMS, systemPill } from '@/lib/pick-systems'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'MLB Picks Today -- All Systems',
  description: "Today's MLB picks from all Beezy.FYI machine learning systems.",
}

const B = '1px solid var(--basalt)'

export default async function MLBPicksPage() {
  const stats = await apiGetStats().then(s => s.bySystem).catch(() => [])

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 24px' }}>
      <div style={{ marginBottom: '28px' }}>
        <p className="dell-heading" style={{ fontSize: '11px', letterSpacing: '0.12em', color: 'var(--fog)', marginBottom: '8px' }}>Picks</p>
        <h1 className="dell-display" style={{ fontSize: '34px', color: 'var(--chalk)', marginBottom: '8px' }}>MLB picks</h1>
        <p className="mono" style={{ fontSize: '13px', color: 'var(--fog)' }}>{PICK_SYSTEMS.length} systems &middot; best onshore book &middot; paper mode</p>
      </div>

      <div className="systems-grid">
        {PICK_SYSTEMS.map((s) => {
          const stat = stats.find(x => x.system === s.key)
          const roi = stat ? parseFloat(String(stat.roi ?? 0)) : null
          const wr = stat ? parseFloat(String(stat.win_rate)) : null
          const bets = stat ? parseInt(String(stat.total_bets)) : 0
          const pill = systemPill(s.key)

          return (
            <Link
              key={s.key}
              href={`/picks/mlb/${s.slug}`}
              className="card-hover"
              style={{
                display: 'block',
                background: 'var(--graphite)',
                textDecoration: 'none',
                border: B,
                borderRadius: 'var(--radius-lg)',
                overflow: 'hidden',
              }}
            >
              <div style={{ height: '3px', background: pill.color, opacity: 0.85 }} />
              <div style={{ padding: '18px' }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '12px', gap: '8px' }}>
                  <span className="dell-heading" style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.05em', padding: '3px 8px', borderRadius: 'var(--radius-pill)', background: pill.bg, color: pill.color, border: pill.border }}>
                    {s.shortName}
                  </span>
                  {roi !== null && (
                    <span className="mono" style={{ fontSize: '13px', fontWeight: 600, color: roi >= 0 ? 'var(--signal)' : 'var(--loss)' }}>
                      {roi >= 0 ? '+' : ''}{roi.toFixed(1)}%
                    </span>
                  )}
                </div>
                <div className="dell-display" style={{ fontSize: '17px', color: 'var(--chalk)', marginBottom: '16px', letterSpacing: '-0.01em' }}>{s.name}</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '8px', paddingTop: '14px', borderTop: B }}>
                  <div>
                    <div className="dell-heading" style={{ fontSize: '8.5px', textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '4px' }}>Win Rate</div>
                    <div className="mono" style={{ fontSize: '13px', fontWeight: 600, color: 'var(--chalk)' }}>{wr !== null ? `${wr.toFixed(1)}%` : '--'}</div>
                  </div>
                  <div>
                    <div className="dell-heading" style={{ fontSize: '8.5px', textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '4px' }}>Bets</div>
                    <div className="mono" style={{ fontSize: '13px', fontWeight: 600, color: 'var(--chalk)' }}>{bets}</div>
                  </div>
                  <div>
                    <div className="dell-heading" style={{ fontSize: '8.5px', textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '4px' }}>Gate</div>
                    <div className="mono" style={{ fontSize: '13px', fontWeight: 600, color: bets >= 200 ? 'var(--signal)' : 'var(--fog)' }}>{bets >= 200 ? '✓' : `${bets}/200`}</div>
                  </div>
                </div>
              </div>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
