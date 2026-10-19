'use client'

import Link from 'next/link'
import { usePathname, useSearchParams } from 'next/navigation'
import type { BoardDay } from '@/lib/board-days'
import { addDaysToDateKey } from '@/lib/dates'

const B = '1px solid var(--basalt)'
const SEG: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'baseline', gap: '8px', padding: '6px 14px', border: B, background: 'var(--graphite)', color: 'var(--silver)',
  fontFamily: 'var(--font-mono)', fontSize: '11px', letterSpacing: '0.04em', textDecoration: 'none', whiteSpace: 'nowrap',
}
const ON: React.CSSProperties = { borderColor: 'var(--win-border)', background: 'var(--win-wash)', color: 'var(--signal)', fontWeight: 600 }

/**
 * The one day control on every picks page (MLB, NFL, NHL, NBA): a chip per date that has event records, so it matches the events.
 * YESTERDAY is the settled MLB card or the archived board of a projection sport (only when it has rows); the rest are the days from
 * today on with games, TODAY first when it has any. NFL skips from Sunday to Monday to Thursday, and NBA reads YESTERDAY | TODAY.
 * `selected` is the date of the rows on the page, which is what a chip highlights. Switching day clears the game filter.
 */
export function DayToggle({ today, yesterday, days, selected, pinDates = true }: {
  today: string
  /** the archive chip, or null when there is nothing to open */
  yesterday: BoardDay | null
  /** game days from today on that have rows, soonest first */
  days: BoardDay[]
  /** the day the rows on the page are from */
  selected: string
  /** projection sports name the date in the URL, so a chip never lands on another day; MLB's board is today's, so it keeps a clean URL */
  pinDates?: boolean
}) {
  const pathname = usePathname()
  const sp = useSearchParams()
  const hrefFor = (d: string, isYesterday: boolean) => {
    const p = new URLSearchParams(sp.toString())
    p.delete('game')
    p.delete('date')
    p.delete('day')
    if (isYesterday) p.set('day', 'yesterday')
    else if (pinDates) p.set('date', d)
    const qs = p.toString()
    return qs ? `${pathname}?${qs}` : pathname
  }
  const chips = [...(yesterday ? [{ ...yesterday, yest: true }] : []), ...days.map(d => ({ ...d, yest: false }))]
  const name = (c: { date: string; label: string }) =>
    c.date === today ? `TODAY ${c.label}` : c.date === addDaysToDateKey(today, -1) ? `YESTERDAY ${c.label}` : c.label
  return (
    <nav aria-label="Day" style={{ display: 'inline-flex', maxWidth: '100%', overflowX: 'auto' }}>
      {chips.map((c, i) => {
        const on = c.date === selected
        const [word, ...rest] = name(c).split(' ')
        const dated = word === 'TODAY' || word === 'YESTERDAY'
        return (
          <Link key={c.date} href={hrefFor(c.date, c.yest)} scroll={false} aria-current={on ? 'date' : undefined}
            title={c.count == null ? undefined : `${c.count} ${c.count === 1 ? 'row' : 'rows'}`}
            style={{ ...SEG, borderRadius: `${i === 0 ? 'var(--radius)' : 0} ${i === chips.length - 1 ? 'var(--radius)' : 0} ${i === chips.length - 1 ? 'var(--radius)' : 0} ${i === 0 ? 'var(--radius)' : 0}`, ...(i > 0 ? { borderLeft: 'none' } : {}), ...(on ? ON : {}) }}>
            {dated ? <>{word} <span style={{ color: 'var(--fog)', fontWeight: 400 }}>{rest.join(' ')}</span></> : name(c)}
          </Link>
        )
      })}
    </nav>
  )
}
