'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback } from 'react'
import { clsx } from 'clsx'

const LEAGUES  = ['All', 'MLB', 'NFL', 'NBA']
const BOOKS    = ['All', 'DraftKings', 'FanDuel', 'BetMGM', 'Caesars']
const MARKETS  = ['All', 'NRFI', 'HR', 'F5', 'K', 'OUTS']
const DATES    = [
  { label: 'Today',       value: 'today'     },
  { label: 'Yesterday',   value: 'yesterday' },
  { label: 'Last 7 Days', value: 'last7'     },
]
const STATUSES = [
  { label: 'All',     value: 'all'     },
  { label: 'Pending', value: 'pending' },
  { label: 'Won',     value: 'won'     },
  { label: 'Lost',    value: 'lost'    },
]

function FilterChip({
  label,
  active,
  disabled,
  onClick,
}: {
  label:    string
  active:   boolean
  disabled?: boolean
  onClick:  () => void
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        'mono text-xs tracking-wide px-3 py-1.5 border transition-colors whitespace-nowrap',
        active
          ? 'border-accent text-accent bg-accent/5'
          : 'border-[var(--border)] text-muted hover:border-[var(--border-bright)] hover:text-text',
        disabled && 'opacity-30 cursor-not-allowed'
      )}
    >
      {label}
    </button>
  )
}

export function PicksFilterBar() {
  const router = useRouter()
  const sp     = useSearchParams()

  const get = (key: string, fallback = 'All') => sp.get(key) ?? fallback

  const set = useCallback((key: string, value: string) => {
    const params = new URLSearchParams(sp.toString())
    if (value === 'All' || value === 'all') {
      params.delete(key)
    } else {
      params.set(key, value)
    }
    router.push(`?${params.toString()}`, { scroll: false })
  }, [router, sp])

  return (
    <div className="sticky top-14 z-40 bg-[var(--bg)]/95 backdrop-blur-sm border-b border-[var(--border)] py-3">
      <div className="max-w-7xl mx-auto px-4 space-y-2">
        {/* Row 1 */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="mono text-xs text-muted uppercase tracking-widest mr-2 w-12">League</span>
          {LEAGUES.map(l => (
            <FilterChip
              key={l}
              label={l}
              active={get('league') === l || (l === 'All' && !sp.get('league'))}
              disabled={l !== 'All' && l !== 'MLB'}
              onClick={() => set('league', l)}
            />
          ))}
        </div>

        {/* Row 2 */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="mono text-xs text-muted uppercase tracking-widest mr-2 w-12">Market</span>
          {MARKETS.map(m => (
            <FilterChip
              key={m}
              label={m}
              active={get('market') === m || (m === 'All' && !sp.get('market'))}
              onClick={() => set('market', m)}
            />
          ))}
        </div>

        {/* Row 3 */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="mono text-xs text-muted uppercase tracking-widest mr-2 w-12">Date</span>
          {DATES.map(d => (
            <FilterChip
              key={d.value}
              label={d.label}
              active={get('date', 'today') === d.value}
              onClick={() => set('date', d.value)}
            />
          ))}
          <span className="mono text-xs text-muted mx-2">·</span>
          <span className="mono text-xs text-muted uppercase tracking-widest mr-2">Status</span>
          {STATUSES.map(s => (
            <FilterChip
              key={s.value}
              label={s.label}
              active={get('status', 'all') === s.value}
              onClick={() => set('status', s.value)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
