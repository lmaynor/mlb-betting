'use client'

import { Fragment, useMemo, useState } from 'react'
import Link from 'next/link'
import type { Bet } from '@/lib/types'
import { beezyscore } from '@/lib/beezy-score'
import { bookDisplay, SURFACE_CARD, pickLabel } from '@/lib/tokens'
import { formatOdds } from '@/lib/odds'
import { SystemBadge } from '@/components/ui/primitives'
import { collapseBooks } from '@/lib/dedupe'

const TH: React.CSSProperties = { position: 'sticky', top: 0, background: '#0d0f16', fontSize: '10px', letterSpacing: '0.08em', textTransform: 'uppercase', color: '#7d8596', fontWeight: 600, padding: '8px 12px', textAlign: 'left', borderBottom: '1px solid #1e2230' }
const TD: React.CSSProperties = { padding: '10px 12px', borderBottom: '1px solid #151822', verticalAlign: 'middle' }

export function CommandCenterClient({ bets }: { bets: Bet[] }) {
  const [open, setOpen] = useState<number | null>(null)
  const top = useMemo(() => collapseBooks([...bets].sort((a, b) => beezyscore(b) - beezyscore(a))).slice(0, 6), [bets])

  return (
    <section style={{ marginTop: '28px' }} aria-label="Today's top plays">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '16px', marginBottom: '12px' }}>
        <h2 style={{ fontSize: '16px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>Today&apos;s top plays</h2>
        <span style={{ display: 'flex', gap: '14px' }}>
          <Link href="/ev" style={{ color: 'var(--link)', fontSize: '12px', fontWeight: 600 }}>Odds Terminal &rarr;</Link>
          <Link href="/picks" style={{ color: 'var(--link)', fontSize: '12px', fontWeight: 600 }}>All picks &rarr;</Link>
        </span>
      </div>
      <div style={{ ...SURFACE_CARD, overflow: 'hidden' }}>
        {top.length === 0 ? (
          <div style={{ padding: '20px', textAlign: 'center', color: 'var(--fog)', fontSize: '13px', lineHeight: 1.55 }}>
            No open plays right now. The model card publishes after the ~11:00 AM CT run and the +EV scanner re-checks lines every 15 minutes.
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={TH}>Play</th>
                <th style={TH}>Book &amp; price</th>
                <th style={{ ...TH, textAlign: 'right' }}>Edge</th>
              </tr>
            </thead>
            <tbody>
              {top.map(b => {
                const book = bookDisplay(b.book)
                const bullets = (b.notes ?? '').split(' . ').map(s => s.trim()).filter(Boolean)
                const isOpen = open === b.id
                const edge = Math.abs(b.edge ?? 0) < 2 ? (b.edge ?? 0) * 100 : (b.edge ?? 0)
                return (
                  <Fragment key={b.id}>
                    <tr onClick={bullets.length ? () => setOpen(isOpen ? null : b.id) : undefined} style={{ cursor: bullets.length ? 'pointer' : 'default' }} aria-expanded={bullets.length ? isOpen : undefined}>
                      <td style={TD}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <SystemBadge system={b.system} />
                          <span style={{ fontSize: '13px', fontWeight: 700, color: 'var(--chalk)' }}>{pickLabel(b)}</span>
                        </div>
                      </td>
                      <td className="mono" style={{ ...TD, fontSize: '12px', color: 'var(--ash)', whiteSpace: 'nowrap' }}>
                        {b.book ? book.short : '--'}{b.bookCount > 1 ? ` +${b.bookCount - 1}` : ''} {formatOdds(b.odds)}
                      </td>
                      <td className="mono" style={{ ...TD, textAlign: 'right', fontSize: '13px', fontWeight: 700, color: 'var(--signal)' }}>+{edge.toFixed(1)}%</td>
                    </tr>
                    {isOpen && (
                      <tr><td colSpan={3} style={{ ...TD, background: '#0d0f16' }}>
                        <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'grid', gap: '4px' }}>
                          {bullets.map((t, i) => <li key={i} className="mono" style={{ fontSize: '11px', color: 'var(--fog)' }}>&middot; {t}</li>)}
                        </ul>
                      </td></tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
