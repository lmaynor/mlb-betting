'use client'

import Link from 'next/link'
import { usePathname, useSearchParams } from 'next/navigation'
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
export function DayToggle({ day, today, yesterday }: { day: PickDay; today: string; yesterday: string }) {
  const pathname = usePathname()
  const sp = useSearchParams()
  const hrefFor = (d: PickDay) => {
    const p = new URLSearchParams(sp.toString())
    p.delete('game')
    if (d === 'today') p.delete('day')
    else p.set('day', d)
    const qs = p.toString()
    return qs ? `${pathname}?${qs}` : pathname
  }
  const label = (key: string) => formatDateKey(key, { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase().replace(',', '')
  return (
    <nav aria-label="Day" style={{ display: 'inline-flex' }}>
      <Link href={hrefFor('today')} scroll={false} aria-current={day === 'today' ? 'date' : undefined}
        style={{ ...SEG, borderRadius: 'var(--radius) 0 0 var(--radius)', ...(day === 'today' ? ON : {}) }}>
        TODAY <span style={{ color: 'var(--fog)', fontWeight: 400 }}>{label(today)}</span>
      </Link>
      <Link href={hrefFor('yesterday')} scroll={false} aria-current={day === 'yesterday' ? 'date' : undefined}
        style={{ ...SEG, borderRadius: '0 var(--radius) var(--radius) 0', borderLeft: 'none', ...(day === 'yesterday' ? ON : {}) }}>
        YESTERDAY <span style={{ color: 'var(--fog)', fontWeight: 400 }}>{label(yesterday)}</span>
      </Link>
    </nav>
  )
}
