'use client'

import Link from 'next/link'
import { usePathname, useSearchParams } from 'next/navigation'
import { SportLogo } from '@/components/ui/sport-assets'
import { gameTimeLabel, type BoardGame } from '@/lib/board-games'

const B = '1px solid var(--basalt)'
const CARD: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', gap: '4px', padding: '8px 12px', border: B, borderRadius: 'var(--radius)', background: 'var(--graphite)',
  color: 'var(--silver)', textDecoration: 'none', flex: '0 0 auto', scrollSnapAlign: 'start', minWidth: '118px',
}
const ON: React.CSSProperties = { borderColor: 'var(--win-border)', background: 'var(--win-wash)' }

/**
 * The matchup filter: every game of the day as a card with both teams' logos and the start time, one tap to show only that
 * game's rows (`?game=`), a second tap to clear it. Games with no rows yet are listed too (count 0), so the whole slate is
 * visible even before lines post. `today` is the site date key (to label a later day's game 'SUN 12:00 PM').
 */
export function GamesStrip({ games, selected, today, total }: { games: BoardGame[]; selected?: string; today: string; total: number }) {
  const pathname = usePathname()
  const sp = useSearchParams()
  if (games.length === 0) return null
  const hrefFor = (key: string | null) => {
    const p = new URLSearchParams(sp.toString())
    if (key == null || key === selected) p.delete('game')
    else p.set('game', key)
    const qs = p.toString()
    return qs ? `${pathname}?${qs}` : pathname
  }
  return (
    <nav aria-label="Games" style={{ display: 'flex', gap: '8px', overflowX: 'auto', scrollSnapType: 'x proximity', padding: '10px 16px', borderBottom: B, background: 'var(--carbon)' }}>
      <Link href={hrefFor(null)} scroll={false} aria-current={!selected ? 'true' : undefined}
        style={{ ...CARD, justifyContent: 'center', minWidth: '92px', ...(!selected ? ON : {}) }}>
        <span className="mono" style={{ fontSize: '11px', fontWeight: 600, color: !selected ? 'var(--signal)' : 'var(--silver)' }}>ALL GAMES</span>
        <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)' }}>{games.length} {games.length === 1 ? 'game' : 'games'} &middot; {total} {total === 1 ? 'row' : 'rows'}</span>
      </Link>
      {games.map(g => {
        const on = g.key === selected
        const time = gameTimeLabel(g, today)
        return (
          <Link key={g.key} href={hrefFor(g.key)} scroll={false} aria-current={on ? 'true' : undefined} style={{ ...CARD, ...(on ? ON : {}) }}>
            <span className="mono" style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', fontSize: '11.5px', fontWeight: 600, color: on ? 'var(--signal)' : 'var(--chalk)', whiteSpace: 'nowrap' }}>
              <SportLogo sport={g.sport} team={g.away} size={18} />{g.away}
              <span style={{ color: 'var(--fog)', fontWeight: 400 }}>@</span>
              <SportLogo sport={g.sport} team={g.home} size={18} />{g.home}
            </span>
            <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)', whiteSpace: 'nowrap' }}>
              {time ? `${time} \u00b7 ` : ''}{g.count} {g.count === 1 ? 'row' : 'rows'}
            </span>
          </Link>
        )
      })}
    </nav>
  )
}
