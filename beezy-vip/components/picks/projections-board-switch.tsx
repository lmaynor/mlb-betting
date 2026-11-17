'use client'

import React, { useEffect, useState } from 'react'
import type { BoardRow } from '@/lib/board-rows'
import { resolveLayout } from '@/lib/prop-cards'
import type { SportSlug } from '@/lib/sports'
import { SportProjectionsBoard } from './sport-projections-board'
import { ProjectionsCards } from './projections-cards'

const pill = (on: boolean): React.CSSProperties => ({
  borderRadius: '6px',
  color: on ? '#ffffff' : 'var(--color-neutral-300)',
  background: on ? 'var(--color-background-tertiary-default)' : 'transparent',
  border: `1px solid ${on ? 'var(--color-border-button-hover)' : 'transparent'}`,
})
const PILL_CLASS = 'text-compact font-semibold py-[7px] px-[13px] no-underline whitespace-nowrap'
const GROUP_CLASS = 'inline-flex gap-0.5 p-[3px] bg-background-secondary-default border border-separator-border rounded-control max-w-full overflow-x-auto no-scrollbar'

export interface ProjectionsBoardSwitchProps {
  sport: SportSlug
  rows: BoardRow[]
  census: number
  status?: string
  statusWarn?: boolean
  emptyBody?: string
  initialLayout: 'cards' | 'table' | null
}

export function ProjectionsBoardSwitch({
  sport,
  rows,
  census,
  status,
  statusWarn,
  emptyBody,
  initialLayout,
}: ProjectionsBoardSwitchProps) {
  const [layout, setLayout] = useState<'cards' | 'table' | null>(initialLayout)

  useEffect(() => {
    if (initialLayout) return
    try {
      const stored = window.localStorage.getItem('ia08-proj-layout')
      const isMobile = window.matchMedia('(max-width: 768px)').matches
      const resolved = resolveLayout({ param: null, stored, isMobile })
      setLayout(resolved)
    } catch {
      setLayout('table')
    }
  }, [initialLayout])

  if (rows.length === 0) {
    return (
      <SportProjectionsBoard
        sport={sport}
        rows={rows}
        status={status}
        statusWarn={statusWarn}
        emptyBody={emptyBody}
        visuals
      />
    )
  }

  const choose = (next: 'cards' | 'table') => {
    setLayout(next)
    try {
      window.localStorage.setItem('ia08-proj-layout', next)
    } catch {
      // ignore
    }
    try {
      const url = new URL(window.location.href)
      url.searchParams.set('layout', next)
      window.history.replaceState(null, '', url.toString())
    } catch {
      // ignore
    }
  }

  const isCards = layout === 'cards'

  return (
    <div>
      <div className="mb-3.5 flex justify-end">
        <div role="group" aria-label="Layout view" className={GROUP_CLASS}>
          <button
            type="button"
            className={PILL_CLASS}
            style={{ ...pill(isCards), cursor: 'pointer' }}
            aria-pressed={isCards}
            onClick={() => choose('cards')}
          >
            Cards
          </button>
          <button
            type="button"
            className={PILL_CLASS}
            style={{ ...pill(!isCards), cursor: 'pointer' }}
            aria-pressed={!isCards}
            onClick={() => choose('table')}
          >
            Table
          </button>
        </div>
      </div>

      {isCards ? (
        <>
          {status && (
            <div
              className="text-[12px] mb-2"
              role={statusWarn ? 'status' : undefined}
              style={{ color: statusWarn ? 'var(--warn)' : 'var(--color-text-secondary)' }}
            >
              {status}
            </div>
          )}
          <ProjectionsCards rows={rows} census={census} onShowTable={() => choose('table')} />
        </>
      ) : (
        <SportProjectionsBoard
          sport={sport}
          rows={rows}
          status={status}
          statusWarn={statusWarn}
          emptyBody={emptyBody}
          visuals
        />
      )}
    </div>
  )
}
