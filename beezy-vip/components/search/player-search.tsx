'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import type { PlayerSearchPlayer } from '@/app/api/player-search/route'
import { formatOdds } from '@/lib/odds'

/**
 * Player-entity search (T-4): type a name, get that player's props for today with source and price, and
 * land in the Prop Research inspector for the exact prop. Debounced; the API applies the viewer's cut.
 */
export function PlayerSearch() {
  const [q, setQ] = useState('')
  const [result, setResult] = useState<{ q: string; players: PlayerSearchPlayer[] } | null>(null)
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)
  const seq = useRef(0)

  const query = q.trim()
  const players = result && result.q === query ? result.players : null
  const loading = query.length >= 2 && players === null

  useEffect(() => {
    if (query.length < 2) return
    const t = setTimeout(async () => {
      const mine = ++seq.current
      try {
        const res = await fetch(`/api/player-search?q=${encodeURIComponent(query)}`)
        const data = (await res.json()) as { players?: PlayerSearchPlayer[] }
        if (seq.current !== mine) return
        setResult({ q: query, players: data.players ?? [] })
        setOpen(true)
      } catch {
        if (seq.current === mine) setResult({ q: query, players: [] })
      }
    }, 200)
    return () => clearTimeout(t)
  }, [query])

  useEffect(() => {
    const onDown = (e: MouseEvent) => { if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey) }
  }, [])

  const showPanel = open && q.trim().length >= 2

  return (
    <div ref={boxRef} className="relative mx-auto w-full max-w-page px-6 pt-4">
      <input
        type="search"
        value={q}
        onChange={e => { setQ(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        placeholder="Search a player: every prop for him today"
        aria-label="Search players"
        className="h-11 w-full rounded-lg border border-border-button-default bg-background-secondary-default px-3.5 text-body-1-regular text-text-primary outline-none placeholder:text-text-placeholder focus:border-accent-500"
      />
      {showPanel && (
        <div className="absolute inset-x-6 top-full z-50 mt-1.5 max-h-[70dvh] overflow-y-auto rounded-xl border border-separator-border bg-background-primary-default shadow-card" role="listbox" aria-label="Player results">
          {loading && players == null ? (
            <div className="px-4 py-3 text-body-2-regular text-text-secondary">Searching today&apos;s boards...</div>
          ) : players == null || players.length === 0 ? (
            <div className="px-4 py-3 text-body-2-regular text-text-secondary">No players match on today&apos;s boards.</div>
          ) : (
            players.map(p => (
              <div key={`${p.sport}|${p.player}`} className="border-b border-separator-border px-4 py-3 last:border-b-0">
                <div className="flex flex-wrap items-baseline gap-x-2.5">
                  <span className="text-body-2-semibold text-text-primary">{p.player}</span>
                  <span className="mono text-caption-1-regular text-text-secondary">{p.sport.toUpperCase()}{p.team ? ` | ${p.team}` : ''}{p.matchup ? ` | ${p.matchup}` : ''}</span>
                </div>
                <div className="mt-1.5 grid gap-1">
                  {p.props.map(prop => (
                    <Link
                      key={prop.id}
                      href={`/edge?sport=${p.sport}&prop=${encodeURIComponent(prop.id)}`}
                      onClick={() => setOpen(false)}
                      className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 rounded-md px-1.5 py-1 no-underline hover:bg-background-primary-hover"
                    >
                      <span className="text-body-2-regular text-text-primary">{prop.label}</span>
                      <span className="mono text-caption-1-regular text-text-secondary">
                        {prop.odds != null ? formatOdds(prop.odds) : '--'}{prop.book ? ` | ${prop.book}` : ''}
                      </span>
                      <span className="ui-heading text-micro text-text-secondary">{prop.source === 'model' ? 'MODEL PROJECTION' : 'MARKET SWEEP'}</span>
                    </Link>
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
