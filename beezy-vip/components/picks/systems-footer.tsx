import Link from 'next/link'
import { kindOfSystem, PICK_SYSTEMS, systemPill, type PickKind } from '@/lib/pick-systems'
import { SPORT_PILLARS } from '@/lib/sports'
import type { SystemStats } from '@/lib/types'

const TH = { textAlign: 'left', padding: '9px 12px', fontSize: '10px', fontWeight: 600, letterSpacing: '0.06em', color: 'var(--fog)', background: '#11131a', borderBottom: '1px solid #1e2230', whiteSpace: 'nowrap', position: 'sticky', top: 0 } as const
const TD = { padding: '10px 12px', borderBottom: '1px solid #151822', whiteSpace: 'nowrap' } as const

const num = (v: unknown) => { const n = parseFloat(String(v ?? '')); return Number.isFinite(n) ? n : null }

/**
 * The MLB systems behind a card, as the page footer: only the systems of this card's kind (player or game), each with its
 * own settled record, linking to its detail page. Replaces the old standalone Systems tab.
 */
export function SystemsFooter({ kind, stats }: { kind: PickKind; stats: SystemStats[] }) {
  const status = new Map(SPORT_PILLARS.mlb.markets.map(m => [m.key, m.status]))
  const rows = PICK_SYSTEMS
    .filter(s => kindOfSystem(s.key) === kind)
    .map(s => ({ s, stat: stats.find(x => x.system === s.key), status: status.get(s.key) ?? null }))
    // a system with no live market entry and no bets is a shelved idea, not something to list
    .filter(r => r.status != null || num(r.stat?.total_bets) !== null && (num(r.stat?.total_bets) ?? 0) > 0)

  return (
    <section id="systems" aria-labelledby="systems-h" style={{ marginTop: '36px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap', marginBottom: '10px' }}>
        <h2 id="systems-h" style={{ fontSize: '15px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>
          {kind === 'player' ? 'Player systems' : 'Game systems'}
        </h2>
        <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>{rows.length} systems &middot; best available book &middot; paper mode</span>
      </div>
      <div style={{ overflowX: 'auto', border: '1px solid #1e2230', borderRadius: '8px', background: '#11131a' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '680px' }}>
          <thead>
            <tr>
              <th style={TH}>SYSTEM</th>
              <th style={TH}>MARKET</th>
              <th style={TH}>STATUS</th>
              <th style={{ ...TH, textAlign: 'right' }}>BETS</th>
              <th style={{ ...TH, textAlign: 'right' }}>WIN RATE</th>
              <th style={{ ...TH, textAlign: 'right' }}>ROI</th>
              <th style={{ ...TH, textAlign: 'right' }}>SAMPLE</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ s, stat, status: st }) => {
              const bets = num(stat?.total_bets) ?? 0
              const roi = num(stat?.roi)
              const wr = num(stat?.win_rate)
              const pill = systemPill(s.key)
              return (
                <tr key={s.key} className="sys-row">
                  <td style={TD}>
                    <Link href={`/picks/mlb/${s.slug}`} style={{ textDecoration: 'none' }}>
                      <span className="dell-heading" style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.05em', padding: '3px 8px', borderRadius: 'var(--radius-pill)', background: pill.bg, color: pill.color, border: pill.border }}>{s.shortName}</span>
                    </Link>
                  </td>
                  <td style={{ ...TD, fontSize: '13.5px', fontWeight: 600 }}>
                    <Link href={`/picks/mlb/${s.slug}`} style={{ color: 'var(--chalk)', textDecoration: 'none' }}>{s.name}</Link>
                  </td>
                  <td style={{ ...TD, fontSize: '12px', color: 'var(--silver)' }}>{st ?? '--'}</td>
                  <td className="mono" style={{ ...TD, textAlign: 'right', fontSize: '13px', color: 'var(--chalk)' }}>{bets}</td>
                  <td className="mono" style={{ ...TD, textAlign: 'right', fontSize: '13px', color: 'var(--chalk)' }}>{wr !== null ? `${wr.toFixed(1)}%` : '--'}</td>
                  <td className="mono" style={{ ...TD, textAlign: 'right', fontSize: '13px', fontWeight: 600, color: roi == null ? 'var(--fog)' : roi >= 0 ? 'var(--signal)' : 'var(--loss)' }}>
                    {roi == null ? '--' : `${roi >= 0 ? '+' : ''}${roi.toFixed(1)}%`}
                  </td>
                  <td className="mono" style={{ ...TD, textAlign: 'right', fontSize: '12px', color: 'var(--fog)' }}>{bets >= 200 ? '200+ bets' : `${bets}/200 bets`}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p style={{ fontSize: '11.5px', color: 'var(--fog)', margin: '10px 2px 0', lineHeight: 1.55 }}>
        Log-only systems record every pick but are not counted as a live card until they clear 200 settled bets. Every result is public on the <Link href="/results" style={{ color: 'var(--link)' }}>Results</Link> ledger.
      </p>
    </section>
  )
}
