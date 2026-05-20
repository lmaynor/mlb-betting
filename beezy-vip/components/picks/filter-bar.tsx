'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useState } from 'react'

const B = '0.5px solid #1f1f24'

const CHIP_BASE: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: '6px',
  padding: '5px 10px',
  borderRadius: '2px',
  border: B,
  background: '#111114',
  color: '#a1a1aa',
  fontSize: '11px',
  fontFamily: 'var(--font-mono)',
  letterSpacing: '0.06em',
  cursor: 'pointer',
  whiteSpace: 'nowrap' as const,
  userSelect: 'none' as const,
  WebkitTapHighlightColor: 'transparent',
  transition: 'color 0.1s, border-color 0.1s, background 0.1s',
}

const CHIP_ACTIVE: React.CSSProperties = {
  ...CHIP_BASE,
  color: '#f5f5f7',
  borderColor: '#3f3f46',
  background: '#18181b',
}

const SYSTEM_DOTS: Record<string, string> = {
  ALL:  '#71717a',
  NRFI: '#10b981',
  HR:   '#f59e0b',
  F5:   '#3b82f6',
  K:    '#a78bfa',
  OUTS: '#fb923c',
}

// Sort options shown inline in the toggle bar
const SORT_OPTIONS = [
  { value: 'date',  label: 'Date' },
  { value: 'edge',  label: 'Edge' },
  { value: 'odds',  label: 'Odds' },
]

export function FilterBar() {
  const router = useRouter()
  const sp = useSearchParams()
  const [open, setOpen] = useState(false)

  const market = sp.get('market') ?? 'ALL'
  const date   = sp.get('date')   ?? 'today'
  const status = sp.get('status') ?? 'ALL'
  const book   = sp.get('book')   ?? 'ALL'
  const sort   = sp.get('sort')   ?? 'date'
  const dir    = sp.get('dir')    ?? 'desc'

  const set = useCallback((key: string, value: string, defaultVal: string) => {
    const params = new URLSearchParams(sp.toString())
    if (value === defaultVal) {
      params.delete(key)
    } else {
      params.set(key, value)
    }
    router.push('/picks?' + params.toString())
  }, [router, sp])

  const toggleSort = useCallback((value: string) => {
    const params = new URLSearchParams(sp.toString())
    if (sort === value) {
      // toggle direction
      params.set('dir', dir === 'desc' ? 'asc' : 'desc')
    } else {
      params.set('sort', value)
      params.delete('dir') // reset to default desc
    }
    router.push('/picks?' + params.toString())
  }, [router, sp, sort, dir])

  const SYSTEMS = ['ALL', 'NRFI', 'HR', 'F5', 'K', 'OUTS']
  const RESULTS = ['ALL', 'WIN', 'LOSS', 'PENDING']
  // Removed 'ALL' (All Time) — results page handles full history
  const DATES   = ['today', 'yesterday', 'last7']
  const BOOKS   = ['ALL', 'DraftKings', 'FanDuel', 'Caesars', 'BetMGM', 'theScore']

  const DATE_LABELS:   Record<string, string> = { today: 'Today', yesterday: 'Yesterday', last7: 'Last 7d' }
  const RESULT_LABELS: Record<string, string> = { ALL: 'All', WIN: 'Win', LOSS: 'Loss', PENDING: 'Pending' }

  const activeCount = [
    market !== 'ALL',
    date   !== 'today',
    status !== 'ALL',
    book   !== 'ALL',
  ].filter(Boolean).length

  function chip(label: string, active: boolean, onClick: () => void, dot?: string) {
    return (
      <button key={label} onClick={onClick} style={active ? CHIP_ACTIVE : CHIP_BASE}>
        {dot && <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: dot, flexShrink: 0 }} />}
        {label}
      </button>
    )
  }

  function row(label: string, children: React.ReactNode) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', padding: '8px 16px', borderBottom: B }}>
        <span style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', color: '#71717a', letterSpacing: '0.08em', minWidth: '44px' }}>
          {label}
        </span>
        {children}
      </div>
    )
  }

  return (
    <div style={{ borderBottom: B, background: '#0a0a0c' }}>

      {/* Toggle bar — always visible */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', padding: '8px 16px', flexWrap: 'wrap' }}>

        {/* Left: hamburger + active summary */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <button
            onClick={() => setOpen(o => !o)}
            style={{
              ...CHIP_BASE,
              color: open ? '#f5f5f7' : '#a1a1aa',
              borderColor: open ? '#3f3f46' : '#1f1f24',
              background: open ? '#18181b' : '#111114',
              gap: '8px',
            }}
          >
            <svg width="12" height="10" viewBox="0 0 12 10" fill="none" style={{ flexShrink: 0 }}>
              <rect y="0"    width="12" height="1.5" rx="0.75" fill="currentColor"/>
              <rect y="4.25" width="12" height="1.5" rx="0.75" fill="currentColor"/>
              <rect y="8.5"  width="12" height="1.5" rx="0.75" fill="currentColor"/>
            </svg>
            FILTERS
            {activeCount > 0 && (
              <span style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                width: '16px', height: '16px', borderRadius: '50%',
                background: '#10b981', color: '#0a0a0c',
                fontSize: '9px', fontFamily: 'var(--font-mono)', fontWeight: 700,
              }}>{activeCount}</span>
            )}
            <span style={{ fontSize: '9px', color: '#71717a' }}>{open ? '▴' : '▾'}</span>
          </button>

          {/* Active filter summary when collapsed */}
          {!open && activeCount > 0 && (
            <>
              {market !== 'ALL' && (
                <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>
                  <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: SYSTEM_DOTS[market] ?? '#71717a', display: 'inline-block' }} />
                  {market}
                </span>
              )}
              {date !== 'today'  && <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>{DATE_LABELS[date] ?? date}</span>}
              {status !== 'ALL'  && <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>{RESULT_LABELS[status] ?? status}</span>}
              {book !== 'ALL'    && <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>{book}</span>}
            </>
          )}
        </div>

        {/* Right: sort buttons — always visible */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <span style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', color: '#71717a', letterSpacing: '0.08em', marginRight: '4px' }}>SORT</span>
          {SORT_OPTIONS.map(({ value, label }) => {
            const active = sort === value
            return (
              <button
                key={value}
                onClick={() => toggleSort(value)}
                style={{
                  ...(active ? CHIP_ACTIVE : CHIP_BASE),
                  padding: '4px 8px',
                  gap: '4px',
                }}
              >
                {label}
                {active && (
                  <span style={{ fontSize: '9px', opacity: 0.7 }}>{dir === 'desc' ? '↓' : '↑'}</span>
                )}
              </button>
            )
          })}
        </div>
      </div>

      {/* Expandable filter rows */}
      {open && (
        <div style={{ borderTop: B }}>
          {row('MARKET', SYSTEMS.map(s => chip(s, market === s, () => set('market', s, 'ALL'), SYSTEM_DOTS[s])))}
          {row('DATE',   DATES.map(d   => chip(DATE_LABELS[d], date === d, () => set('date', d, 'today'))))}
          {row('RESULT', RESULTS.map(r => chip(RESULT_LABELS[r], status === r, () => set('status', r, 'ALL'))))}
          {row('BOOK',   BOOKS.map(b   => chip(b === 'ALL' ? 'All Books' : b, book === b, () => set('book', b, 'ALL'))))}
        </div>
      )}
    </div>
  )
}
