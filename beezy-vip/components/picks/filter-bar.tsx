'use client'
import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useState } from 'react'

const TYPE_MARKETS: Record<string, string[]> = {
  'All':          ['All', 'NRFI', 'HR', 'F5', 'K', 'OUTS'],
  'Game Lines':   ['All', 'F5', 'NRFI'],
  'Player Props': ['All', 'HR', 'K', 'OUTS'],
}
const TYPES    = ['All', 'Game Lines', 'Player Props']
const BOOKS    = ['All', 'DraftKings', 'FanDuel', 'Caesars', 'BetMGM', 'theScore', 'PointsBet']
const DATES    = [{ label: 'Today', value: 'today' }, { label: 'Yesterday', value: 'yesterday' }, { label: 'Last 7 Days', value: 'last7' }]
const STATUSES = [{ label: 'All', value: 'all' }, { label: 'Pending', value: 'pending' }, { label: 'Won', value: 'won' }, { label: 'Lost', value: 'lost' }]

const PILL: Record<string, string> = {
  NRFI: '#10b981', HR: '#f59e0b', F5: '#3b82f6', K: '#a78bfa', OUTS: '#fb923c',
}
const B = '0.5px solid #1f1f24'

function Chip({ label, active, disabled, color, onClick }: {
  label: string; active: boolean; disabled?: boolean; color?: string; onClick: () => void
}) {
  return (
    <button onClick={onClick} disabled={disabled} className="mono" style={{
      fontSize: '11px', letterSpacing: '0.04em', padding: '4px 10px',
      cursor: disabled ? 'not-allowed' : 'pointer',
      border: `0.5px solid ${active ? (color ?? '#10b981') : '#1f1f24'}`,
      color: active ? (color ?? '#10b981') : '#71717a',
      background: active ? `${color ?? '#10b981'}12` : 'transparent',
      opacity: disabled ? 0.3 : 1,
      whiteSpace: 'nowrap' as const,
      transition: 'all 0.12s',
    }}>{label}</button>
  )
}

const labelStyle: React.CSSProperties = {
  fontFamily: 'var(--font-mono, monospace)', fontSize: '10px',
  letterSpacing: '0.1em', textTransform: 'uppercase',
  color: '#71717a', minWidth: '56px',
}
const rowStyle: React.CSSProperties = {
  display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '6px',
}
const divider: React.CSSProperties = {
  ...labelStyle, minWidth: 'auto', margin: '0 6px', color: '#2a2a31',
}

export function PicksFilterBar() {
  const router = useRouter()
  const sp     = useSearchParams()
  const [showMore, setShowMore] = useState(false)

  const get = (key: string, fallback = 'All') => sp.get(key) ?? fallback

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

  // Show "More filters" button as active if any non-default value is set
  const hasMoreActive = sp.get('type') || sp.get('book')

  return (
    <div style={{ position: 'sticky', top: '48px', zIndex: 40, background: 'rgba(10,10,12,0.97)', borderBottom: B, padding: '10px 20px', backdropFilter: 'blur(8px)' }}>
      <div style={{ maxWidth: '1200px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '8px' }}>

        {/* Row 1: Date + Result + More filters toggle */}
        <div style={{ ...rowStyle, justifyContent: 'space-between' }}>
          <div style={rowStyle}>
            <span style={labelStyle}>Date</span>
            {DATES.map(d => (
              <Chip key={d.value} label={d.label} active={get('date', 'today') === d.value}
                onClick={() => set('date', d.value)} />
            ))}
            <span style={divider}>·</span>
            <span style={labelStyle}>Result</span>
            {STATUSES.map(s => (
              <Chip key={s.value} label={s.label}
                active={get('status', 'all') === s.value}
                color={s.value === 'won' ? '#10b981' : s.value === 'lost' ? '#ef4444' : undefined}
                onClick={() => set('status', s.value)} />
            ))}
          </div>
          <button
            onClick={() => setShowMore(v => !v)}
            className="mono"
            style={{
              fontSize: '10px', letterSpacing: '0.08em', padding: '4px 10px',
              border: `0.5px solid ${hasMoreActive ? '#10b981' : '#1f1f24'}`,
              color: hasMoreActive ? '#10b981' : '#71717a',
              background: 'transparent', cursor: 'pointer',
              whiteSpace: 'nowrap',
            }}>
            {showMore ? 'Less ↑' : `More filters${hasMoreActive ? ' ·' : ' ↓'}`}
          </button>
        </div>

        {/* Row 2: Market (always visible, primary filter) */}
        <div style={rowStyle}>
          <span style={labelStyle}>Market</span>
          {markets.map(m => (
            <Chip key={m} label={m} color={PILL[m]}
              active={activeMarket === m || (m === 'All' && !sp.get('market'))}
              onClick={() => set('market', m)} />
          ))}
        </div>

        {/* Expandable: Type + Book */}
        {showMore && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', paddingTop: '4px', borderTop: B }}>
            <div style={rowStyle}>
              <span style={labelStyle}>Type</span>
              {TYPES.map(t => (
                <Chip key={t} label={t} active={activeType === t}
                  onClick={() => set('type', t, ['market'])} />
              ))}
            </div>
            <div style={rowStyle}>
              <span style={labelStyle}>Book</span>
              {BOOKS.map(b => (
                <Chip key={b} label={b}
                  active={get('book') === b || (b === 'All' && !sp.get('book'))}
                  onClick={() => set('book', b)} />
              ))}
            </div>
          </div>
        )}

      </div>
    </div>
  )
}
