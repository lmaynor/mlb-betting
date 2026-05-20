'use client'

import { useState } from 'react'

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

interface FilterBarProps {
  system: string
  setSystem: (s: string) => void
  result: string
  setResult: (r: string) => void
  date: string
  setDate: (d: string) => void
  book: string
  setBook: (b: string) => void
}

export function FilterBar({
  system, setSystem,
  result, setResult,
  date, setDate,
  book, setBook,
}: FilterBarProps) {
  const [open, setOpen] = useState(false)

  const SYSTEMS = ['ALL', 'NRFI', 'HR', 'F5', 'K', 'OUTS']
  const RESULTS = ['ALL', 'WIN', 'LOSS', 'PENDING']
  const DATES   = ['TODAY', 'YESTERDAY', 'LAST7', 'ALL']
  const BOOKS   = ['ALL', 'DraftKings', 'FanDuel', 'Caesars', 'BetMGM', 'theScore']

  const DATE_LABELS: Record<string, string>   = { TODAY: 'Today', YESTERDAY: 'Yesterday', LAST7: 'Last 7d', ALL: 'All Time' }
  const RESULT_LABELS: Record<string, string> = { ALL: 'All', WIN: 'Win', LOSS: 'Loss', PENDING: 'Pending' }

  // How many active non-default filters for the badge
  const activeCount = [
    system !== 'ALL',
    result !== 'ALL',
    date   !== 'ALL',
    book   !== 'ALL',
  ].filter(Boolean).length

  function chip(label: string, active: boolean, onClick: () => void, dot?: string) {
    return (
      <button key={label} onClick={onClick} style={active ? CHIP_ACTIVE : CHIP_BASE}>
        {dot && (
          <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: dot, flexShrink: 0 }} />
        )}
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

      {/* ── Toggle bar — always visible ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 16px' }}>
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
          {/* Hamburger icon */}
          <svg width="12" height="10" viewBox="0 0 12 10" fill="none" style={{ flexShrink: 0 }}>
            <rect y="0" width="12" height="1.5" rx="0.75" fill="currentColor"/>
            <rect y="4.25" width="12" height="1.5" rx="0.75" fill="currentColor"/>
            <rect y="8.5" width="12" height="1.5" rx="0.75" fill="currentColor"/>
          </svg>
          FILTERS
          {activeCount > 0 && (
            <span style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              width: '16px', height: '16px', borderRadius: '50%',
              background: '#10b981', color: '#0a0a0c',
              fontSize: '9px', fontFamily: 'var(--font-mono)', fontWeight: 700,
            }}>
              {activeCount}
            </span>
          )}
          <span style={{ fontSize: '9px', color: '#71717a' }}>{open ? '▴' : '▾'}</span>
        </button>

        {/* Active filter summary — shown when collapsed so you can see what's set */}
        {!open && activeCount > 0 && (
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            {system !== 'ALL' && (
              <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>
                <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: SYSTEM_DOTS[system], flexShrink: 0, display: 'inline-block' }} />
                {system}
              </span>
            )}
            {date !== 'ALL' && (
              <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>{DATE_LABELS[date]}</span>
            )}
            {result !== 'ALL' && (
              <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>{RESULT_LABELS[result]}</span>
            )}
            {book !== 'ALL' && (
              <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>{book}</span>
            )}
          </div>
        )}
      </div>

      {/* ── Expandable filter rows ── */}
      {open && (
        <div style={{ borderTop: B }}>
          {row('MARKET', SYSTEMS.map(s => chip(s, system === s, () => setSystem(s), SYSTEM_DOTS[s])))}
          {row('DATE',   DATES.map(d => chip(DATE_LABELS[d], date === d, () => setDate(d))))}
          {row('RESULT', RESULTS.map(r => chip(RESULT_LABELS[r], result === r, () => setResult(r))))}
          {row('BOOK',   BOOKS.map(b => chip(b === 'ALL' ? 'All Books' : b, book === b, () => setBook(b))))}
        </div>
      )}
    </div>
  )
}
