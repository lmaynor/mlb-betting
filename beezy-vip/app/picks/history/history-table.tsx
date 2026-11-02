'use client'

import { Fragment, useState } from 'react'
import { TopPicksTable } from '@/components/picks/top-picks-table'
import { formatDateKey } from '@/lib/dates'
import { TABLE_TD, TABLE_TH } from '@/lib/tokens'
import type { Record1u, TopPick } from '@/lib/top-picks'

export interface HistoryDay { date: string; picks: TopPick[]; record: Record1u }

const TH = { ...TABLE_TH, textAlign: 'left', padding: '9px 12px', position: 'sticky', top: 0, whiteSpace: 'nowrap' } as const
const TD = { ...TABLE_TD, padding: '10px 12px', whiteSpace: 'nowrap' } as const

/** One row per day; a day opens to its ranked ten as a colSpan sub-row. The newest day starts open. */
export function HistoryTable({ days }: { days: HistoryDay[] }) {
  const [open, setOpen] = useState<Set<string>>(() => new Set(days.slice(0, 1).map(d => d.date)))
  const toggle = (d: string) => setOpen(prev => { const n = new Set(prev); if (n.has(d)) n.delete(d); else n.add(d); return n })

  return (
    <div className="bg-background-primary-default border border-separator-border rounded-control overflow-x-auto">
      <table className="w-full border-collapse min-w-[560px] text-cozy">
        <thead>
          <tr>
            <th className="ui-heading" style={TH}>Day</th>
            <th className="ui-heading" style={{ ...TH, textAlign: 'right' }}>Picks</th>
            <th className="ui-heading" style={{ ...TH, textAlign: 'right' }}>W-L</th>
            <th className="ui-heading" style={{ ...TH, textAlign: 'right' }}>Units</th>
            <th className="ui-heading" style={{ ...TH, textAlign: 'right' }}>ROI</th>
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
                  <td style={{ ...TD, color: 'var(--color-text-primary)', fontWeight: 600 }}>
                    <button aria-expanded={isOpen} aria-label={`${isOpen ? 'Hide' : 'Show'} ${d.date}`} onClick={e => { e.stopPropagation(); toggle(d.date) }}
                      style={{ all: 'unset', cursor: 'pointer', color: 'var(--color-text-secondary)', width: '18px', display: 'inline-block' }}>{isOpen ? '-' : '+'}</button>
                    {formatDateKey(d.date, { weekday: 'short', month: 'short', day: 'numeric' })}
                  </td>
                  <td className="mono" style={{ ...TD, textAlign: 'right', color: 'var(--color-neutral-300)' }}>{d.picks.length}</td>
                  <td className="mono" style={{ ...TD, textAlign: 'right', color: 'var(--color-text-primary)' }}>
                    {r.graded === 0 ? <span className="text-text-secondary">{r.pending > 0 ? 'pending' : '--'}</span> : `${r.wins}-${r.losses}${r.pushes ? `-${r.pushes}` : ''}`}
                  </td>
                  <td className="mono" style={{ ...TD, textAlign: 'right', fontWeight: 700, color: r.graded === 0 ? 'var(--color-text-secondary)' : r.units >= 0 ? 'var(--signal)' : 'var(--loss)' }}>
                    {r.graded === 0 ? '--' : `${r.units >= 0 ? '+' : ''}${r.units.toFixed(2)}u`}
                  </td>
                  <td className="mono" style={{ ...TD, textAlign: 'right', color: roi == null ? 'var(--color-text-secondary)' : roi >= 0 ? 'var(--signal)' : 'var(--loss)' }}>
                    {roi == null ? '--' : `${roi >= 0 ? '+' : ''}${roi.toFixed(0)}%`}
                  </td>
                </tr>
                {isOpen && (
                  <tr>
                    <td colSpan={5} className="pt-2.5 px-3 pb-3.5 border-b border-b-[color:var(--color-separator-border)] bg-background-secondary-default">
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
