import Link from 'next/link'
import { apiGetRecentSettled } from '@/lib/betting-api'
import { SYSTEM_PILL, SURFACE_CARD, bookDisplay, pickLabel } from '@/lib/tokens'
import { formatOdds } from '@/lib/odds'
import { collapseBooks } from '@/lib/dedupe'
import { SectionHeader } from './section-header'
import type { Bet } from '@/lib/types'

const TH: React.CSSProperties = { position: 'sticky', top: 0, background: '#0d0f16', fontSize: '10px', letterSpacing: '0.08em', textTransform: 'uppercase', color: '#7d8596', fontWeight: 600, padding: '8px 12px', textAlign: 'left', borderBottom: '1px solid #1e2230' }
const TD: React.CSSProperties = { padding: '10px 12px', borderBottom: '1px solid #151822', verticalAlign: 'middle' }

function units(profit: number | null, stake: number | null) {
  if (profit == null) return '--'
  const unitSize = stake && stake > 0 ? stake : 10
  return `${profit >= 0 ? '+' : ''}${(profit / unitSize).toFixed(2)}u`
}

function pickText(bet: Bet) {
  const label = pickLabel(bet)
  if (!bet.player) return label
  return label.replace(bet.player, '').trim().replace(/^[-\s]+/, '') || label
}

const RESULT: Record<string, { label: string; color: string }> = {
  win: { label: 'WIN', color: '#22c55e' }, loss: { label: 'LOSS', color: '#ef4444' },
  push: { label: 'PUSH', color: '#9ca3af' }, void: { label: 'VOID', color: '#7d8596' },
}

export async function RecentPicksTable() {
  let rows: Bet[] = []
  try {
    rows = collapseBooks(await apiGetRecentSettled(60)).slice(0, 6)
  } catch { rows = [] }

  return (
    <section style={{ paddingTop: '32px' }}>
      <SectionHeader title="Recent graded plays" href="/results" link="View full ledger &rarr;" />
      <div style={{ ...SURFACE_CARD, overflow: 'hidden' }}>
        {rows.length === 0 ? (
          <div style={{ padding: '20px 16px', textAlign: 'center', color: 'var(--fog)', fontSize: '13px' }}>
            No settled bets yet. Check back after today&apos;s games.
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={TH}>Play</th>
                <th style={TH}>Book &amp; price</th>
                <th style={{ ...TH, textAlign: 'right' }}>Result</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(b => {
                const pill = SYSTEM_PILL[b.system] ?? SYSTEM_PILL.ALL
                const res = RESULT[b.result ?? ''] ?? { label: 'OPEN', color: '#7d8596' }
                const book = bookDisplay(b.book)
                return (
                  <tr key={b.id}>
                    <td style={{ ...TD, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span className="dell-heading" style={{ flexShrink: 0, fontSize: '10px', fontWeight: 600, padding: '2px 6px', borderRadius: '6px', background: pill.bg, color: pill.color, border: pill.border }}>{b.system}</span>
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontSize: '13px', fontWeight: 700, color: 'var(--chalk)' }}>{b.player ?? pickText(b)}</div>
                          {b.player && <div style={{ fontSize: '12px', color: '#9ca3af' }}>{pickText(b)}</div>}
                        </div>
                      </div>
                    </td>
                    <td className="mono" style={{ ...TD, fontSize: '12px', color: 'var(--ash)', whiteSpace: 'nowrap' }}>{b.book ? book.short : '--'} {formatOdds(b.odds)}</td>
                    <td style={{ ...TD, textAlign: 'right', whiteSpace: 'nowrap' }}>
                      <span className="mono" style={{ fontSize: '10px', fontWeight: 700, color: res.color, marginRight: '8px' }}>{res.label}</span>
                      <span className="mono" style={{ fontSize: '13px', fontWeight: 700, color: (b.profit ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>{units(b.profit, b.stake)}</span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
      <div style={{ marginTop: '8px' }}><Link href="/results" style={{ fontSize: '12px', color: 'var(--link)' }}>All results by sport &rarr;</Link></div>
    </section>
  )
}
