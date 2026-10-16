'use client'

import { Fragment, useState } from 'react'
import { TopPicksTable } from '@/components/picks/top-picks-table'
import { formatDateKey } from '@/lib/dates'
import type { Record1u, TopPick } from '@/lib/top-picks'

export interface HistoryDay { date: string; picks: TopPick[]; record: Record1u }

const TH = { textAlign: 'left', padding: '9px 12px', fontSize: '10px', color: 'var(--fog)', borderBottom: '1px solid #1e2230', background: '#11131a', position: 'sticky', top: 0, whiteSpace: 'nowrap' } as const
const TD = { padding: '10px 12px', borderBottom: '1px solid #151822', whiteSpace: 'nowrap' } as const

/** One row per day; a day opens to its ranked ten as a colSpan sub-row. The newest day starts open. */
export function HistoryTable({ days }: { days: HistoryDay[] }) {
  const [open, setOpen] = useState<Set<string>>(() => new Set(days.slice(0, 1).map(d => d.date)))
  const toggle = (d: string) => setOpen(prev => { const n = new Set(prev); if (n.has(d)) n.delete(d); else n.add(d); return n })

  return (
    <div style={{ background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '560px', fontSize: '13px' }}>
        <thead>
          <tr>
            <th className="dell-heading" style={TH}>Day</th>
            <th className="dell-heading" style={{ ...TH, textAlign: 'right' }}>Picks</th>
            <th className="dell-heading" style={{ ...TH, textAlign: 'right' }}>W-L</th>
            <th className="dell-heading" style={{ ...TH, textAlign: 'right' }}>Units</th>
            <th className="dell-heading" style={{ ...TH, textAlign: 'right' }}>ROI</th>
          </tr>
        </thead>
        <tbody>
          {days.map(d => {
            const r = d.record
            const roi = r.graded > 0 ? (r.units / r.graded) * 100 : null
            const isOpen = open.has(d.date)
            return (
              <Fragment key={d.date}>
                <tr className="sys-row" onClick={() => toggle(d.date)} style={{ cursor: 'pointer' }}>
                  <td style={{ ...TD, color: 'var(--chalk)', fontWeight: 600 }}>
                    <button aria-expanded={isOpen} aria-label={`${isOpen ? 'Hide' : 'Show'} ${d.date}`} onClick={e => { e.stopPropagation(); toggle(d.date) }}
                      style={{ all: 'unset', cursor: 'pointer', color: 'var(--fog)', width: '18px', display: 'inline-block' }}>{isOpen ? '-' : '+'}</button>
                    {formatDateKey(d.date, { weekday: 'short', month: 'short', day: 'numeric' })}
                  </td>
                  <td className="mono" style={{ ...TD, textAlign: 'right', color: 'var(--silver)' }}>{d.picks.length}</td>
                  <td className="mono" style={{ ...TD, textAlign: 'right', color: 'var(--chalk)' }}>
                    {r.graded === 0 ? <span style={{ color: 'var(--fog)' }}>{r.pending > 0 ? 'pending' : '--'}</span> : `${r.wins}-${r.losses}${r.pushes ? `-${r.pushes}` : ''}`}
                  </td>
                  <td className="mono" style={{ ...TD, textAlign: 'right', fontWeight: 700, color: r.graded === 0 ? 'var(--fog)' : r.units >= 0 ? 'var(--signal)' : 'var(--loss)' }}>
                    {r.graded === 0 ? '--' : `${r.units >= 0 ? '+' : ''}${r.units.toFixed(2)}u`}
                  </td>
                  <td className="mono" style={{ ...TD, textAlign: 'right', color: roi == null ? 'var(--fog)' : roi >= 0 ? 'var(--signal)' : 'var(--loss)' }}>
                    {roi == null ? '--' : `${roi >= 0 ? '+' : ''}${roi.toFixed(0)}%`}
                  </td>
                </tr>
                {isOpen && (
                  <tr>
                    <td colSpan={5} style={{ padding: '10px 12px 14px', borderBottom: '1px solid #1e2230', background: '#090a0f' }}>
                      <TopPicksTable picks={d.picks} showResult />
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
