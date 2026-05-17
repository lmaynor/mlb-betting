'use client'
import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback } from 'react'

const TYPE_MARKETS: Record<string, string[]> = {
  'All':          ['All', 'NRFI', 'HR', 'F5', 'K', 'OUTS'],
  'Game Lines':   ['All', 'F5', 'NRFI'],
  'Player Props': ['All', 'HR', 'K', 'OUTS'],
}
const TYPES  = ['All', 'Game Lines', 'Player Props']
const BOOKS  = ['All', 'DraftKings', 'FanDuel', 'Caesars', 'BetMGM', 'theScore', 'PointsBet']
const DATES  = [{ label: 'Today', value: 'today' }, { label: 'Yesterday', value: 'yesterday' }, { label: 'Last 7 Days', value: 'last7' }]
const STATUSES = [{ label: 'All', value: 'all' }, { label: 'Pending', value: 'pending' }, { label: 'Won', value: 'won' }, { label: 'Lost', value: 'lost' }]

const PILL: Record<string, string> = {
  NRFI: '#10b981', HR: '#f59e0b', F5: '#3b82f6', K: '#a78bfa', OUTS: '#fb923c',
}
const B = '0.5px solid #1f1f24'

function Chip({ label, active, disabled, color, onClick }: {
  label: string; active: boolean; disabled?: boolean; color?: string; onClick: () => void
}) {
  const c = active ? (color ?? '#10b981') : '#71717a'
  return (
    <button onClick={onClick} disabled={disabled} className="mono" style={{
      fontSize: '11px', letterSpacing: '0.04em', padding: '4px 10px',
      cursor: disabled ? 'not-allowed' : 'pointer',
      border: `0.5px solid ${active ? (color ?? '#10b981') : '#1f1f24'}`,
      color: c,
      background: active ? `${color ?? '#10b981'}12` : 'transparent',
      opacity: disabled ? 0.3 : 1,
      whiteSpace: 'nowrap' as const,
      transition: 'all 0.12s',
    }}>{label}</button>
  )
}

export function PicksFilterBar() {
  const router = useRouter()
  const sp     = useSearchParams()
  const get    = (key: string, fallback = 'All') => sp.get(key) ?? fallback

  const set = useCallback((key: string, value: string, clearKeys?: string[]) => {
    const params = new URLSearchParams(sp.toString())
    if (value === 'All' || value === 'all') params.delete(key)
    else params.set(key, value)
    clearKeys?.forEach(k => params.delete(k))
    router.push(`?${params.toString()}`, { scroll: false })
  }, [router, sp])

  const activeType   = get('type', 'All')
  const activeMarket = get('market', 'All')
  const markets      = TYPE_MARKETS[activeType] ?? TYPE_MARKETS['All']

  const rowStyle   = { display: 'flex', flexWrap: 'wrap' as const, alignItems: 'center', gap: '6px' }
  const labelStyle = { fontFamily: 'var(--font-mono, monospace)', fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: '#71717a', minWidth: '56px' }

  return (
    <div style={{ position: 'sticky', top: '48px', zIndex: 40, background: 'rgba(10,10,12,0.97)', borderBottom: B, padding: '10px 20px', backdropFilter: 'blur(8px)' }}>
      <div style={{ maxWidth: '1200px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '7px' }}>

        {/* Row 1: Type (hierarchy) + dynamic Market */}
        <div style={{ display: 'flex', flexWrap: 'wrap' as const, alignItems: 'center', gap: '16px' }}>
          <div style={rowStyle}>
            <span style={labelStyle}>Type</span>
            {TYPES.map(t => (
              <Chip key={t} label={t} active={activeType === t}
                onClick={() => set('type', t, ['market'])} />
            ))}
          </div>
          <div style={rowStyle}>
            <span style={{ ...labelStyle, color: '#2a2a31' }}>·</span>
            <span style={labelStyle}>Market</span>
            {markets.map(m => (
              <Chip key={m} label={m} color={PILL[m]}
                active={activeMarket === m || (m === 'All' && !sp.get('market'))}
                onClick={() => set('market', m)} />
            ))}
          </div>
        </div>

        {/* Row 2: Book */}
        <div style={rowStyle}>
          <span style={labelStyle}>Book</span>
          {BOOKS.map(b => (
            <Chip key={b} label={b} active={get('book') === b || (b === 'All' && !sp.get('book'))}
              onClick={() => set('book', b)} />
          ))}
        </div>

        {/* Row 3: Date + Status */}
        <div style={rowStyle}>
          <span style={labelStyle}>Date</span>
          {DATES.map(d => (
            <Chip key={d.value} label={d.label} active={get('date', 'today') === d.value}
              onClick={() => set('date', d.value)} />
          ))}
          <span style={{ ...labelStyle, minWidth: 'auto', margin: '0 8px', color: '#2a2a31' }}>·</span>
          <span style={labelStyle}>Result</span>
          {STATUSES.map(s => (
            <Chip key={s.value} label={s.label}
              active={get('status', 'all') === s.value}
              color={s.value === 'won' ? '#10b981' : s.value === 'lost' ? '#ef4444' : undefined}
              onClick={() => set('status', s.value)} />
          ))}
        </div>

      </div>
    </div>
  )
}
