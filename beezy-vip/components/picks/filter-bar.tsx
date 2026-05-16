'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback } from 'react'

const MARKETS  = ['All', 'NRFI', 'HR', 'F5', 'K', 'OUTS']
const BOOKS    = ['All', 'DraftKings', 'FanDuel', 'Caesars', 'BetMGM', 'ESPN Bet', 'PointsBet']
const DATES    = [{ label: 'Today', value: 'today' }, { label: 'Yesterday', value: 'yesterday' }, { label: 'Last 7 Days', value: 'last7' }]
const STATUSES = [{ label: 'All', value: 'all' }, { label: 'Pending', value: 'pending' }, { label: 'Won', value: 'won' }, { label: 'Lost', value: 'lost' }]
const LEAGUES  = ['All', 'MLB', 'NFL', 'NBA']

const B  = '0.5px solid #1f1f24'
const BH = '0.5px solid #2a2a31'

function Chip({ label, active, disabled, onClick }: { label: string; active: boolean; disabled?: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} disabled={disabled} className="mono" style={{
      fontSize: '11px', letterSpacing: '0.04em', padding: '4px 10px', cursor: disabled ? 'not-allowed' : 'pointer',
      border: active ? '0.5px solid #10b981' : B,
      color:  active ? '#10b981' : '#71717a',
      background: active ? 'rgba(16,185,129,0.05)' : 'transparent',
      opacity: disabled ? 0.3 : 1,
      whiteSpace: 'nowrap' as const,
    }}>
      {label}
    </button>
  )
}

export function PicksFilterBar() {
  const router = useRouter()
  const sp     = useSearchParams()
  const get    = (key: string, fallback = 'All') => sp.get(key) ?? fallback
  const set    = useCallback((key: string, value: string) => {
    const params = new URLSearchParams(sp.toString())
    if (value === 'All' || value === 'all') params.delete(key)
    else params.set(key, value)
    router.push(`?${params.toString()}`, { scroll: false })
  }, [router, sp])

  const rowStyle = { display: 'flex', flexWrap: 'wrap' as const, alignItems: 'center', gap: '6px' }
  const labelStyle = { fontFamily: 'var(--font-mono, monospace)', fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: '#71717a', minWidth: '52px' }

  return (
    <div style={{ position: 'sticky', top: '48px', zIndex: 40, background: 'rgba(10,10,12,0.95)', borderBottom: B, padding: '10px 20px', backdropFilter: 'blur(8px)' }}>
      <div style={{ maxWidth: '1200px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <div style={rowStyle}>
          <span style={labelStyle}>League</span>
          {LEAGUES.map(l => <Chip key={l} label={l} active={get('league') === l || (l === 'All' && !sp.get('league'))} disabled={l !== 'All' && l !== 'MLB'} onClick={() => set('league', l)} />)}
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>Market</span>
          {MARKETS.map(m => <Chip key={m} label={m} active={get('market') === m || (m === 'All' && !sp.get('market'))} onClick={() => set('market', m)} />)}
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>Book</span>
          {BOOKS.map(b => <Chip key={b} label={b} active={get('book') === b || (b === 'All' && !sp.get('book'))} onClick={() => set('book', b)} />)}
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>Date</span>
          {DATES.map(d => <Chip key={d.value} label={d.label} active={get('date', 'today') === d.value} onClick={() => set('date', d.value)} />)}
          <span style={{ ...labelStyle, minWidth: 'auto', margin: '0 6px' }}>&middot; Status</span>
          {STATUSES.map(s => <Chip key={s.value} label={s.label} active={get('status', 'all') === s.value} onClick={() => set('status', s.value)} />)}
        </div>
      </div>
    </div>
  )
}
