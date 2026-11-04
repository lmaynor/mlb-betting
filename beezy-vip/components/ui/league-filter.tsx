'use client'

import { leaguePills, type LeaguePill, type SportSlug } from '@/lib/sports'
import { cx } from '@/utils/cx'

export type LeagueValue = 'all' | SportSlug

const SUFFIX: Record<LeaguePill['state'], string> = { live: '', empty: 'off', soon: 'soon' }

/**
 * The one league filter: All, then MLB, NFL, NHL, NBA, always in that order. A league with nothing on the board is dimmed and tagged
 * but still pickable, so the row never changes shape with the calendar; picking it shows why (see LeagueNote).
 */
export function LeagueFilter({ value, onChange, live, soon, label = 'League' }: {
  value: LeagueValue
  onChange: (v: LeagueValue) => void
  live: readonly string[]
  soon?: readonly string[]
  label?: string
}) {
  const pills = leaguePills(live, soon)
  const item = (key: LeagueValue, text: string, state: LeaguePill['state'], note: string) => (
    <button key={key} type="button" aria-pressed={value === key} title={note || undefined} onClick={() => onChange(key)}
      className={cx('button-press-motion inline-flex min-h-[32px] cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-md border px-2.5 py-1 text-caption-1-semibold',
        value === key ? 'border-border-button-hover bg-background-tertiary-default text-text-primary' : 'border-transparent bg-transparent text-text-secondary hover:bg-background-primary-hover',
        state !== 'live' && value !== key && 'opacity-60')}>
      {text}
      {state !== 'live' && <span className="text-[10px] font-normal text-text-tertiary">{SUFFIX[state]}</span>}
    </button>
  )
  return (
    <div role="group" aria-label={label} className="inline-flex max-w-full flex-wrap gap-0.5 rounded-lg border border-separator-border bg-background-secondary-default p-[3px]">
      {item('all', 'All', 'live', '')}
      {pills.map(p => item(p.slug, p.label, p.state, p.note))}
    </div>
  )
}

/** Shown in place of a board when the picked league has nothing: the reason, then one button per league that does. */
export function LeagueNote({ value, live, soon, onPick }: {
  value: SportSlug
  live: readonly string[]
  soon?: readonly string[]
  onPick: (v: LeagueValue) => void
}) {
  const pills = leaguePills(live, soon)
  const me = pills.find(p => p.slug === value)
  const others = pills.filter(p => p.state === 'live' && p.slug !== value)
  return (
    <div role="status" className="rounded-xl border border-separator-border bg-background-primary-default px-5 py-9 text-center shadow-card" data-testid="league-note">
      <div className="mb-1.5 text-body-semibold text-text-primary">{me ? `${me.label}: nothing on the board` : 'Nothing on the board'}</div>
      <div className="mx-auto max-w-[60ch] text-body-2-regular text-text-secondary">{me?.note}</div>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {others.map(p => (
          <button key={p.slug} type="button" onClick={() => onPick(p.slug)}
            className="button-press-motion cursor-pointer rounded-md border border-separator-border bg-background-primary-default px-3 py-1.5 text-caption-1-semibold text-text-primary hover:bg-background-primary-hover">
            Open {p.label}
          </button>
        ))}
        {others.length > 0 && <button type="button" onClick={() => onPick('all')} className="button-press-motion cursor-pointer rounded-md border border-transparent bg-transparent px-3 py-1.5 text-caption-1-semibold text-text-secondary hover:text-text-primary">All leagues</button>}
      </div>
    </div>
  )
}
