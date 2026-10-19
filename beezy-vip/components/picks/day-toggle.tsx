'use client'

import Link from 'next/link'
import { usePathname, useSearchParams } from 'next/navigation'
import type { BoardDay } from '@/lib/board-days'
import { formatDateKey } from '@/lib/dates'
import type { PickDay } from '@/lib/picks-filters'

const B = '1px solid var(--basalt)'
const SEG: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'baseline', gap: '8px', padding: '6px 14px', border: B, background: 'var(--graphite)', color: 'var(--silver)',
  fontFamily: 'var(--font-mono)', fontSize: '11px', letterSpacing: '0.04em', textDecoration: 'none', whiteSpace: 'nowrap',
}
const ON: React.CSSProperties = { borderColor: 'var(--win-border)', background: 'var(--win-wash)', color: 'var(--signal)', fontWeight: 600 }

/**
 * TODAY | YESTERDAY, the one day control on every picks page (MLB, NFL, NHL, NBA). Today is the live board; yesterday is the
 * settled MLB card or the archived board of the projection sports. Older days live on the Results ledger and Picks history.
 * Switching day clears the game filter, because the games differ.
 */
export function DayToggle({ day, today, yesterday, upcoming = [], viewDate }: {
  day: PickDay; today: string; yesterday: string
  /** later game days that have rows (projection sports): one chip each after YESTERDAY */
  upcoming?: BoardDay[]
  /** the day the rows are from; differs from `today` when the board opened on the next day with games */
  viewDate?: string
}) {
  const pathname = usePathname()
  const sp = useSearchParams()
  const hrefFor = (d: PickDay | string) => {
    const p = new URLSearchParams(sp.toString())
    p.delete('game')
    p.delete('date')
    // With later game days on the strip, TODAY names its own date. Without it the board opens on the next day with games when today
    // has none, so clicking TODAY would silently show another day's events.
    if (d === 'today') { p.delete('day'); if (upcoming.length > 0) p.set('date', today) }
    else if (d === 'yesterday') p.set('day', 'yesterday')
    else { p.delete('day'); p.set('date', d) }
    const qs = p.toString()
    return qs ? `${pathname}?${qs}` : pathname
  }
  const label = (key: string) => formatDateKey(key, { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase().replace(',', '')
  const shown = viewDate ?? today
  const todayOn = day === 'today' && shown === today
  const first = upcoming.length === 0
  return (
    <nav aria-label="Day" style={{ display: 'inline-flex', maxWidth: '100%', overflowX: 'auto' }}>
      <Link href={hrefFor('today')} scroll={false} aria-current={todayOn ? 'date' : undefined}
        style={{ ...SEG, borderRadius: 'var(--radius) 0 0 var(--radius)', ...(todayOn ? ON : {}) }}>
        TODAY <span style={{ color: 'var(--fog)', fontWeight: 400 }}>{label(today)}</span>
      </Link>
      <Link href={hrefFor('yesterday')} scroll={false} aria-current={day === 'yesterday' ? 'date' : undefined}
        style={{ ...SEG, borderRadius: first ? '0 var(--radius) var(--radius) 0' : 0, borderLeft: 'none', ...(day === 'yesterday' ? ON : {}) }}>
        YESTERDAY <span style={{ color: 'var(--fog)', fontWeight: 400 }}>{label(yesterday)}</span>
      </Link>
      {upcoming.map((u, i) => {
        const on = day === 'today' && shown === u.date
        return (
          <Link key={u.date} href={hrefFor(u.date)} scroll={false} aria-current={on ? 'date' : undefined} title={`${u.count} ${u.count === 1 ? 'row' : 'rows'}`}
            style={{ ...SEG, borderRadius: i === upcoming.length - 1 ? '0 var(--radius) var(--radius) 0' : 0, borderLeft: 'none', ...(on ? ON : {}) }}>
            {u.label}
          </Link>
        )
      })}
    </nav>
  )
}
