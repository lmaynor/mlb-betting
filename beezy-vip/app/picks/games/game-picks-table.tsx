'use client'

import { Fragment, useMemo, useState } from 'react'
import { GameDetail } from '@/app/ev/games/game-detail'
import { GREEN, SKY, signedPct } from '@/app/ev/games/game-prices-parts'
import { BookLogo } from '@/components/ui/book-logo'
import { SportLogo } from '@/components/ui/sport-assets'
import { buildGameBoard, leanOf, type GameBoardEntry, type GameLean } from '@/lib/game-board'
import type { GameProjectionsFeed } from '@/lib/game-lines'
import { GAME_MIN_REF, startLabel, type GameOddsFeed } from '@/lib/game-odds'
import { formatOdds } from '@/lib/odds'
import { useMyBooks } from '@/lib/use-my-books'

type LogoSport = Parameters<typeof SportLogo>[0]['sport']
const pct = (p: number) => `${(p * 100).toFixed(1)}%`

/**
 * Game Picks for the sports with a game model (NBA, NHL): one row per game, the side where our model is furthest above the market
 * fair price, sorted by that gap. A row opens the same deep-dive as the Game Lines board. There is no graded record behind these,
 * so every row says UNVALIDATED and the gap is a model-versus-market difference, never an edge. The graded MLB card is a separate
 * section of the page while MLB is in season.
 */
export function GamePicksTable({ feeds }: { feeds: { odds: Record<string, GameOddsFeed | null>; proj: Record<string, GameProjectionsFeed | null> } }) {
  const myBooks = useMyBooks()
  const bettable = useMemo(() => (myBooks.enabled ? new Set(myBooks.books) : null), [myBooks.enabled, myBooks.books])
  const offshore = useMemo(() => new Set(Object.values(feeds.odds).flatMap(f => f?.offshore_books ?? [])), [feeds.odds])
  const entries = useMemo(
    () => buildGameBoard(feeds, { refs: [], method: 'proportional', bettable, offshore, minRef: GAME_MIN_REF, includeExchanges: true, bankroll: 1000 }),
    [feeds, bettable, offshore],
  )
  const rows = useMemo(() => {
    const out: { g: GameBoardEntry; lean: GameLean }[] = []
    for (const g of entries) { const lean = leanOf(g); if (lean) out.push({ g, lean }) }
    return out.sort((a, b) => b.lean.gap - a.lean.gap || (a.g.start ?? '').localeCompare(b.g.start ?? ''))
  }, [entries])
  const [open, setOpen] = useState<string | null>(null)
  const noView = entries.length - rows.length

  if (entries.length === 0) {
    return <div role="status" className="py-6 text-neutral-300 text-cozy">No game lines are posted yet. Picks appear once a slate and its prices are posted.</div>
  }
  if (rows.length === 0) {
    return <div role="status" className="py-6 text-neutral-300 text-cozy">Our model has no side above the market on today&apos;s games. See <a className="text-link" href="/ev/games">every game and line</a>.</div>
  }

  return (
    <div className="bg-background-primary-default border border-separator-border rounded-xl shadow-card overflow-hidden" data-testid="game-picks">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-compact">
          <thead>
            <tr className="text-[11px] text-text-tertiary" style={{ background: 'var(--color-background-secondary-default)' }}>
              <th scope="col" className="text-left font-medium px-3 py-2">Game</th>
              <th scope="col" className="text-left font-medium px-3 py-2">Model side</th>
              <th scope="col" className="text-right font-medium px-3 py-2">Model</th>
              <th scope="col" className="text-right font-medium px-3 py-2 hidden sm:table-cell">Market fair</th>
              <th scope="col" className="text-right font-medium px-3 py-2" title="Model minus market fair probability. UNVALIDATED.">Gap</th>
              <th scope="col" className="text-right font-medium px-3 py-2">Best price</th>
              <th scope="col" className="text-right font-medium px-3 py-2 hidden sm:table-cell" title="EV of the best price against the market fair value">EV vs market</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ g, lean }) => {
              const on = open === g.key
              const sport = g.sport as LogoSport
              return (
                <Fragment key={g.key}>
                  <tr data-testid="pick-row" aria-expanded={on} onClick={() => setOpen(on ? null : g.key)}
                    className="cursor-pointer border-t border-t-[color:var(--color-separator-border)] transition-colors duration-150 hover:bg-[color:var(--color-background-primary-hover)]"
                    style={{ background: on ? 'var(--color-background-primary-hover)' : undefined }}>
                    <td className="px-3 py-2 text-left whitespace-nowrap" style={{ boxShadow: `inset 3px 0 0 ${on ? GREEN : 'transparent'}` }}>
                      <span className="inline-flex items-center gap-1.5 font-semibold text-text-primary">
                        <SportLogo sport={sport} team={g.away} size={18} />{g.away}<span className="text-text-tertiary font-normal">@</span><SportLogo sport={sport} team={g.home} size={18} />{g.home}
                      </span>
                      <span className="mono block text-[11px] text-text-secondary">{g.sport.toUpperCase()} &middot; {startLabel(g.start)}</span>
                    </td>
                    <td className="px-3 py-2 text-left whitespace-nowrap font-semibold text-text-primary">{lean.label}<span className="mono block text-[10px] font-semibold tracking-fine text-warn">UNVALIDATED</span></td>
                    <td className="mono px-3 py-2 text-right" style={{ color: SKY }}>{pct(lean.pModel)}</td>
                    <td className="mono px-3 py-2 text-right text-text-secondary hidden sm:table-cell">{pct(lean.pMarket)}</td>
                    <td className="mono px-3 py-2 text-right font-semibold" style={{ color: SKY }}>{signedPct(lean.gap)}</td>
                    <td className="mono px-3 py-2 text-right whitespace-nowrap">
                      {lean.bestBook && lean.bestAmerican != null ? (
                        <span className="inline-flex items-center gap-1.5 justify-end"><BookLogo book={lean.bestBook} size={16} /><span className="font-semibold rounded-chip px-1.5" style={{ color: GREEN, background: 'rgba(87,242,135,0.06)' }}>{formatOdds(lean.bestAmerican)}</span></span>
                      ) : '--'}
                    </td>
                    <td className="mono px-3 py-2 text-right hidden sm:table-cell" style={{ color: lean.evMarket != null && lean.evMarket > 0 ? GREEN : 'var(--color-text-secondary)' }}>{lean.evMarket != null ? signedPct(lean.evMarket) : '--'}</td>
                  </tr>
                  {on && (
                    <tr><td colSpan={7} className="p-3" style={{ background: 'var(--color-background-secondary-default)' }}><GameDetail game={g} bettable={bettable} ageLabel="live" /></td></tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[11.5px] text-text-secondary leading-[1.5] px-4 py-3 m-0 border-t border-separator-border">
        Sorted by how far our model sits above the market fair price on each game&apos;s main lines. Blue is the model and has no tested edge; green is always against the market fair value.
        {noView > 0 ? ` ${noView} more ${noView === 1 ? 'game has' : 'games have'} no side above the market.` : ''} Not financial advice.
      </p>
    </div>
  )
}
