'use client'

import React, { useMemo, useState } from 'react'
import type { BoardRow } from '@/lib/board-rows'
import { cardGate, sortCards, type CardSort } from '@/lib/prop-cards'
import { SURFACE_CARD } from '@/lib/tokens'
import { PropCard } from './prop-card'

const SORTS: { key: CardSort; label: string }[] = [
  { key: 'ev', label: 'Net EV %' },
  { key: 'score', label: 'Beezy Score' },
  { key: 'time', label: 'Tip Time' },
]

const pill = (on: boolean): React.CSSProperties => ({
  borderRadius: '6px',
  color: on ? '#ffffff' : 'var(--color-neutral-300)',
  background: on ? 'var(--color-background-tertiary-default)' : 'transparent',
  border: `1px solid ${on ? 'var(--color-border-button-hover)' : 'transparent'}`,
})
const PILL_CLASS = 'text-compact font-semibold py-[7px] px-[13px] no-underline whitespace-nowrap'
const GROUP_CLASS = 'inline-flex gap-0.5 p-[3px] bg-background-secondary-default border border-separator-border rounded-control max-w-full overflow-x-auto no-scrollbar'

const PAGE = 60

export interface ProjectionsCardsProps {
  rows: BoardRow[]
  census: number
  onShowTable: () => void
}

export function ProjectionsCards({ rows, census, onShowTable }: ProjectionsCardsProps) {
  const [sort, setSort] = useState<CardSort>('ev')
  const [limit, setLimit] = useState(PAGE)

  const gated = useMemo(() => cardGate(rows), [rows])
  const shown = useMemo(() => sortCards(gated, sort), [gated, sort])

  return (
    <div>
      {/* Controls row */}
      <div className="mb-4 flex items-center justify-between gap-3 flex-wrap">
        <div role="group" aria-label="Sort cards by" className={GROUP_CLASS}>
          {SORTS.map(s => (
            <button
              key={s.key}
              type="button"
              onClick={() => setSort(s.key)}
              aria-pressed={sort === s.key}
              className={PILL_CLASS}
              style={{ ...pill(sort === s.key), cursor: 'pointer' }}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      {shown.length === 0 ? (
        <div style={{ border: '1px dashed #1e2230', borderRadius: 8, padding: 32, textAlign: 'center' }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: '#f3f4f6', marginBottom: 8 }}>
            No cards for these filters
          </div>
          <p style={{ fontSize: 13, color: 'var(--color-text-secondary)', margin: 0, lineHeight: 1.5 }}>
            Cards need a posted price from at least 3 books, and a positive EV or a Beezy Score of 35 or more.{' '}
            <button
              type="button"
              onClick={onShowTable}
              style={{
                background: 'none',
                border: 'none',
                padding: 0,
                color: '#38bdf8',
                textDecoration: 'underline',
                cursor: 'pointer',
                font: 'inherit',
              }}
            >
              Show the table
            </button>
            {' '}to see all {census} rows.
          </p>
        </div>
      ) : (
        <div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(min(360px, 100%), 1fr))',
              gap: 12,
            }}
          >
            {shown.slice(0, limit).map(r => (
              <PropCard key={r.key} r={r} />
            ))}
          </div>

          {shown.length > limit && (
            <button
              type="button"
              onClick={() => setLimit(l => l + PAGE)}
              className="btn btn-ghost tap-m mt-[10px]"
            >
              Show {Math.min(PAGE, shown.length - limit)} more
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export function ProjectionsCardsSkeleton() {
  return (
    <>
      <style>{`
        @keyframes ia08-pulse {
          0%, 100% { opacity: 0.4; }
          50% { opacity: 0.85; }
        }
      `}</style>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(min(360px, 100%), 1fr))',
          gap: 12,
        }}
      >
        {[0, 1, 2, 3].map(i => (
          <div
            key={i}
            aria-busy="true"
            style={{
              ...SURFACE_CARD,
              padding: '14px 16px',
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
            }}
          >
            <div
              style={{
                height: 12,
                width: '40%',
                background: '#171a24',
                borderRadius: 4,
                animation: 'ia08-pulse 1.2s ease-in-out infinite',
              }}
            />
            <div
              style={{
                height: 20,
                width: '70%',
                background: '#171a24',
                borderRadius: 4,
                animation: 'ia08-pulse 1.2s ease-in-out infinite',
              }}
            />
            <div
              style={{
                height: 12,
                width: '55%',
                background: '#171a24',
                borderRadius: 4,
                animation: 'ia08-pulse 1.2s ease-in-out infinite',
              }}
            />
          </div>
        ))}
      </div>
    </>
  )
}
