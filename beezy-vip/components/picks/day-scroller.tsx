'use client'

import Link from 'next/link'
import { usePathname, useSearchParams } from 'next/navigation'
import { addDaysToDateKey, formatDateKey } from '@/lib/dates'
import type { BoardDay } from '@/lib/board-days'

const B = '1px solid var(--basalt)'

const BTN: React.CSSProperties = {
  fontFamily: 'var(--font-mono)', fontSize: '11px', padding: '6px 12px', borderRadius: 'var(--radius)', border: B,
  background: 'var(--graphite)', color: 'var(--silver)', textDecoration: 'none', whiteSpace: 'nowrap', flex: '0 0 auto',
}
const CHIP: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'baseline', gap: '6px', padding: '6px 12px', borderRadius: 'var(--radius)', border: B,
  background: 'var(--graphite)', color: 'var(--silver)', fontFamily: 'var(--font-mono)', fontSize: '11px', letterSpacing: '0.04em',
  textDecoration: 'none', whiteSpace: 'nowrap', flex: '0 0 auto', scrollSnapAlign: 'start',
}

/**
 * The day strip for the projection sports, the counterpart of the MLB card's DateBar: ALL DAYS plus one chip per day that has
 * games (with the number of rows), arrows to step between days, state in `?date=`. `selected` and `today` come from the
 * server so the first paint and the highlighted chip agree.
 */
export function DayScroller({ days, selected, today }: { days: BoardDay[]; selected: string; today: string }) {
  const pathname = usePathname()
  const sp = useSearchParams()
  if (days.length === 0) return null

  const hrefFor = (key: string) => {
    const p = new URLSearchParams(sp.toString())
    if (key === days[0].key) p.delete('date')
    else p.set('date', key)
    const qs = p.toString()
    return qs ? `${pathname}?${qs}` : pathname
  }
  const idx = days.findIndex(d => d.key === selected)
  const prev = idx > 0 ? days[idx - 1].key : null
  const next = idx >= 0 && idx < days.length - 1 ? days[idx + 1].key : null
  const total = days.reduce((n, d) => n + d.count, 0)
  const tag = (key: string) => (key === today ? 'TODAY' : key === addDaysToDateKey(today, 1) ? 'TOMORROW' : null)

  const active = (on: boolean): React.CSSProperties => on
    ? { borderColor: 'var(--win-border)', background: 'var(--win-wash)', color: 'var(--signal)', fontWeight: 600 }
    : {}
  const arrow = (label: React.ReactNode, key: string | null, dir: string) => key
    ? <Link href={hrefFor(key)} scroll={false} aria-label={`${dir} day`} style={BTN}>{label}</Link>
    : <span aria-disabled="true" style={{ ...BTN, opacity: 0.4 }}>{label}</span>

  return (
    <nav aria-label="Game day" style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 16px', borderBottom: B, background: 'var(--carbon)' }}>
      {arrow(<>&larr; Prev</>, prev, 'Previous')}
      <div role="list" style={{ display: 'flex', gap: '6px', overflowX: 'auto', scrollSnapType: 'x proximity', flex: '1 1 auto', paddingBottom: '2px' }}>
        <Link role="listitem" href={hrefFor('all')} scroll={false} aria-current={selected === 'all' ? 'true' : undefined}
          style={{ ...CHIP, ...active(selected === 'all') }}>
          ALL DAYS <span style={{ color: 'var(--fog)', fontWeight: 400 }}>{total}</span>
        </Link>
        {days.map(d => {
          const on = d.key === selected
          const t = tag(d.key)
          return (
            <Link key={d.key} role="listitem" href={hrefFor(d.key)} scroll={false} aria-current={on ? 'date' : undefined}
              style={{ ...CHIP, ...active(on) }}>
              {t && <span style={{ fontWeight: 600 }}>{t} &middot;</span>}
              {formatDateKey(d.key, { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase()}
              <span style={{ color: 'var(--fog)', fontWeight: 400 }}>{d.count}</span>
            </Link>
          )
        })}
      </div>
      {arrow(<>Next &rarr;</>, next, 'Next')}
    </nav>
  )
}
