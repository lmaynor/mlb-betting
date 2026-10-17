'use client'

import Link from 'next/link'
import { useMemo, useSyncExternalStore } from 'react'
import { usePathname, useSearchParams } from 'next/navigation'
import { SportLogo } from '@/components/ui/sport-assets'
import { gameTimeLabel, localTimeLabel, type BoardGame } from '@/lib/board-games'

const B = '1px solid var(--basalt)'
const CARD: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '3px', padding: '6px 10px', border: B,
  borderRadius: 'var(--radius)', background: 'var(--graphite)', color: 'var(--silver)', textDecoration: 'none', flex: '0 0 auto',
  scrollSnapAlign: 'start',
}
const ON: React.CSSProperties = { borderColor: 'var(--win-border)', background: 'var(--win-wash)' }

const noSubscription = () => () => {}

/** The viewer's clock: the server renders the Central label; in the browser (never during hydration) each card shows its own time zone. */
function useLocalTimes(games: BoardGame[], today: string): Record<string, string> {
  const inBrowser = useSyncExternalStore(noSubscription, () => true, () => false)
  return useMemo(() => {
    if (!inBrowser) return {}
    const now = new Date()
    return Object.fromEntries(games.map(g => [g.key, localTimeLabel(g, today, { now })]))
  }, [inBrowser, games, today])
}

/**
 * The matchup filter: every game of the day as a compact card with both teams' logos and the start time on the viewer's own
 * clock, one tap to show only that game's rows (`?game=`), a second tap to clear it. Games with no rows yet are listed too, so
 * the whole slate is visible before lines post. Team codes, row counts and the date live in each card's tooltip and label.
 * The row lines up with the page content (max 1200px, 24px gutter) and scrolls sideways when the slate is wider.
 */
export function GamesStrip({ games, selected, today, total }: { games: BoardGame[]; selected?: string; today: string; total: number }) {
  const pathname = usePathname()
  const sp = useSearchParams()
  const local = useLocalTimes(games, today)
  if (games.length === 0) return null
  const hrefFor = (key: string | null) => {
    const p = new URLSearchParams(sp.toString())
    if (key == null || key === selected) p.delete('game')
    else p.set('game', key)
    const qs = p.toString()
    return qs ? `${pathname}?${qs}` : pathname
  }
  return (
    <nav aria-label="Games" style={{ overflowX: 'auto', scrollSnapType: 'x proximity', borderBottom: B, background: 'var(--carbon)' }}>
      <div style={{ display: 'flex', gap: '8px', width: 'max-content', padding: '8px 24px', marginLeft: 'max(0px, calc((100% - 1200px) / 2))' }}>
        <Link href={hrefFor(null)} scroll={false} aria-current={!selected ? 'true' : undefined}
          aria-label={`All games: ${games.length} games, ${total} rows`} title={`${games.length} games, ${total} rows`}
          style={{ ...CARD, minWidth: '56px', ...(!selected ? ON : {}) }}>
          <span className="mono" style={{ fontSize: '11px', fontWeight: 600, color: !selected ? 'var(--signal)' : 'var(--silver)' }}>ALL</span>
          <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)' }}>{games.length}</span>
        </Link>
        {games.map(g => {
          const on = g.key === selected
          const time = local[g.key] ?? gameTimeLabel(g, today)
          return (
            <Link key={g.key} href={hrefFor(g.key)} scroll={false} aria-current={on ? 'true' : undefined} style={{ ...CARD, ...(on ? ON : {}) }}
              aria-label={`${g.away} at ${g.home}${time ? `, ${time}` : ''}, ${g.count} ${g.count === 1 ? 'row' : 'rows'}`}
              title={`${g.away} @ ${g.home}${time ? ` \u00b7 ${time}` : ''} \u00b7 ${g.count} ${g.count === 1 ? 'row' : 'rows'}`}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                <SportLogo sport={g.sport} team={g.away} size={20} />
                <span style={{ color: on ? 'var(--signal)' : 'var(--fog)', fontSize: '10px' }} aria-hidden="true">@</span>
                <SportLogo sport={g.sport} team={g.home} size={20} />
              </span>
              <span className="mono" suppressHydrationWarning style={{ fontSize: '10px', color: on ? 'var(--signal)' : 'var(--fog)', whiteSpace: 'nowrap' }}>{time}</span>
            </Link>
          )
        })}
      </div>
    </nav>
  )
}
