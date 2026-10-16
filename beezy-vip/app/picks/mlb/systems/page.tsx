export const dynamic = 'force-dynamic'

import Link from 'next/link'
import { apiGetStats } from '@/lib/betting-api'
import { PICK_SYSTEMS, systemPill } from '@/lib/pick-systems'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'MLB Systems',
  description: 'Every MLB system with its bet count, win rate and ROI. Paper mode, every result public.',
}

const B = '1px solid var(--basalt)'

export default async function MLBPicksPage() {
  const stats = await apiGetStats().then(s => s.bySystem).catch(() => [])
  const th: React.CSSProperties = { textAlign: 'left', padding: '9px 12px', fontSize: '10px', fontWeight: 600, letterSpacing: '0.06em', color: 'var(--fog)', background: '#11131a', borderBottom: B, whiteSpace: 'nowrap' }
  const td: React.CSSProperties = { padding: '10px 12px', borderBottom: '1px solid #151822', whiteSpace: 'nowrap' }

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', marginBottom: '14px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>MLB systems</h1>
        <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>{PICK_SYSTEMS.length} systems &middot; best available book &middot; paper mode</span>
      </div>

      <div style={{ overflowX: 'auto', border: B, borderRadius: '8px', background: '#11131a' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '640px' }}>
          <thead>
            <tr>
              <th style={th}>SYSTEM</th>
              <th style={th}>MARKET</th>
              <th style={{ ...th, textAlign: 'right' }}>BETS</th>
              <th style={{ ...th, textAlign: 'right' }}>WIN RATE</th>
              <th style={{ ...th, textAlign: 'right' }}>ROI</th>
              <th style={{ ...th, textAlign: 'right' }}>SAMPLE</th>
            </tr>
          </thead>
          <tbody>
            {PICK_SYSTEMS.map((s) => {
              const stat = stats.find(x => x.system === s.key)
              const roi = stat && stat.roi != null ? parseFloat(String(stat.roi)) : null
              const wr = stat ? parseFloat(String(stat.win_rate)) : null
              const bets = stat ? parseInt(String(stat.total_bets)) : 0
              const pill = systemPill(s.key)
              return (
                <tr key={s.key} className="sys-row">
                  <td style={td}>
                    <Link href={`/picks/mlb/${s.slug}`} style={{ textDecoration: 'none' }}>
                      <span className="dell-heading" style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.05em', padding: '3px 8px', borderRadius: 'var(--radius-pill)', background: pill.bg, color: pill.color, border: pill.border }}>{s.shortName}</span>
                    </Link>
                  </td>
                  <td style={{ ...td, fontSize: '13.5px', fontWeight: 600 }}>
                    <Link href={`/picks/mlb/${s.slug}`} style={{ color: 'var(--chalk)', textDecoration: 'none' }}>{s.name}</Link>
                  </td>
                  <td className="mono" style={{ ...td, textAlign: 'right', fontSize: '13px', color: 'var(--chalk)' }}>{bets}</td>
                  <td className="mono" style={{ ...td, textAlign: 'right', fontSize: '13px', color: 'var(--chalk)' }}>{wr !== null ? `${wr.toFixed(1)}%` : '--'}</td>
                  <td className="mono" style={{ ...td, textAlign: 'right', fontSize: '13px', fontWeight: 600, color: roi == null ? 'var(--fog)' : roi >= 0 ? 'var(--signal)' : 'var(--loss)' }}>
                    {roi == null ? '--' : `${roi >= 0 ? '+' : ''}${roi.toFixed(1)}%`}
                  </td>
                  <td className="mono" style={{ ...td, textAlign: 'right', fontSize: '12px', color: 'var(--fog)' }}>{bets >= 200 ? '200+ bets' : `${bets}/200 bets`}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
