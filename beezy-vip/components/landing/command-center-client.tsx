'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import type { Bet } from '@/lib/types'
import { beezyscore } from '@/lib/beezy-score'
import { bookDisplay, CARD_STYLE, pickLabel } from '@/lib/tokens'
import { formatOdds } from '@/lib/odds'
import { ScoreBadge, SystemBadge } from '@/components/ui/primitives'
import { SPORT_LIST, STATUS_LABEL } from '@/lib/sports'
import { collapseBooks } from '@/lib/dedupe'

type Tab = 'plays' | 'sports'

export function CommandCenterClient({ bets }: { bets: Bet[] }) {
  const [tab, setTab] = useState<Tab>('plays')
  const [open, setOpen] = useState<number | null>(null)
  const top = useMemo(() => collapseBooks([...bets].sort((a, b) => beezyscore(b) - beezyscore(a))).slice(0, 8), [bets])

  return (
    <section style={{ marginTop: '36px' }} aria-label="Daily command center">
      <div role="tablist" style={{ display: 'flex', gap: '6px', marginBottom: '14px' }}>
        {([['plays', "TODAY'S TOP PLAYS"], ['sports', 'SPORTS']] as [Tab, string][]).map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
            style={{ fontFamily: 'var(--font-text), sans-serif', fontSize: '12px', fontWeight: 600, letterSpacing: '0.04em', padding: '8px 14px', borderRadius: '6px', cursor: 'pointer',
              color: tab === id ? '#090a0f' : 'var(--silver)', background: tab === id ? 'var(--signal)' : 'var(--surface-elevated)', border: `1px solid ${tab === id ? 'var(--signal)' : '#1e2230'}` }}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'plays' ? (
        top.length === 0 ? (
          <div style={{ ...CARD_STYLE, padding: '32px 20px', textAlign: 'center', color: 'var(--fog)', fontSize: '13.5px', lineHeight: 1.55 }}>
            No open plays right now. The model card publishes after the ~11:00 AM CT run and the +EV scanner re-checks lines every 15 minutes.
          </div>
        ) : (
          <div style={{ display: 'grid', gap: '8px' }}>
            {top.map(b => {
              const book = bookDisplay(b.book)
              const bullets = (b.notes ?? '').split(' . ').map(s => s.trim()).filter(Boolean)
              const isOpen = open === b.id
              return (
                <article key={b.id} style={{ ...CARD_STYLE, padding: '12px 16px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
                    <div style={{ flex: '0 0 64px' }}><ScoreBadge bet={b} /></div>
                    <div style={{ flex: '1 1 260px', minWidth: 0 }}>
                      <div style={{ marginBottom: '5px' }}><SystemBadge system={b.system} /></div>
                      <div style={{ color: 'var(--chalk)', fontWeight: 600, fontSize: '14.5px', lineHeight: 1.35 }}>{pickLabel(b)}</div>
                    </div>
                    <span style={{ fontSize: '11px', fontWeight: 700, color: book.color, background: book.bg, border: `1px solid ${book.color}55`, borderRadius: '6px', padding: '3px 8px' }}>{b.book ? book.label : '--'}{b.bookCount > 1 ? ` +${b.bookCount - 1}` : ''}</span>
                    <span className="mono" style={{ fontSize: '16px', fontWeight: 700, color: 'var(--chalk)', minWidth: '56px' }}>{formatOdds(b.odds)}</span>
                    <span className="mono" style={{ fontSize: '13px', fontWeight: 700, color: '#090a0f', background: 'var(--signal)', borderRadius: '6px', padding: '4px 8px' }}>
                      +{((Math.abs(b.edge ?? 0) < 2 ? (b.edge ?? 0) * 100 : (b.edge ?? 0))).toFixed(1)}%
                    </span>
                    {bullets.length > 0 && (
                      <button onClick={() => setOpen(isOpen ? null : b.id)} aria-expanded={isOpen} className="btn btn-ghost" style={{ padding: '5px 11px', fontSize: '12px' }}>{isOpen ? 'Hide why' : 'Why'}</button>
                    )}
                  </div>
                  {isOpen && (
                    <ul style={{ margin: '10px 0 0 80px', padding: 0, listStyle: 'none', display: 'grid', gap: '4px' }}>
                      {bullets.map((t, i) => <li key={i} className="mono" style={{ fontSize: '11.5px', color: 'var(--fog)' }}>&middot; {t}</li>)}
                    </ul>
                  )}
                </article>
              )
            })}
            <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', padding: '6px 2px' }}>
              <Link href="/ev" style={{ color: 'var(--link)', fontSize: '13px' }}>+EV terminal &rarr;</Link>
              <Link href="/picks" style={{ color: 'var(--link)', fontSize: '13px' }}>All picks &rarr;</Link>
            </div>
          </div>
        )
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '12px' }}>
          {SPORT_LIST.map(p => (
            <Link key={p.slug} href={`/sports/${p.slug}`} className="card-hover" style={{ ...CARD_STYLE, padding: '18px', textDecoration: 'none', borderColor: `${p.accent}33`, display: 'block' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '10px' }}>
                <span className="dell-display" style={{ fontSize: '24px', color: p.accent }}>{p.shortName}</span>
                <span className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>{STATUS_LABEL[p.status]}</span>
              </div>
              <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--silver)', marginBottom: '8px' }}>{p.seasonBadge}</div>
              <p className="times" style={{ fontSize: '12.5px', color: 'var(--fog)', lineHeight: 1.55 }}>{p.description}</p>
            </Link>
          ))}
        </div>
      )}
    </section>
  )
}
