'use client'

import { useMemo, useState } from 'react'
import { GameLinesNotice, GameLinesView } from '@/app/ev/game-lines-view'
import { useIsMobile } from '@/components/ui/mobile-sheet'
import { buildGameLinesData, gameLinesViewLabel, type GameLinesFilters, type GameProjectionsFeed } from '@/lib/game-lines'

type Market = GameLinesFilters['market']
const MARKETS: { key: Market; label: string }[] = [
  { key: 'all', label: 'All markets' },
  { key: 'spread', label: 'Spread / puck line' },
  { key: 'total', label: 'Total' },
  { key: 'ml', label: 'Moneyline' },
]

/** NBA / NHL game lines for /ev/games, fed by the game feed (the props projection feed never carries these rows). */
export function GameLinesClient({ feeds, sport }: { feeds: Record<string, GameProjectionsFeed | null>; sport: 'all' | 'nba' | 'nhl' }) {
  const [market, setMarket] = useState<Market>('all')
  const [view, setView] = useState<'cards' | 'table'>('cards')
  const [limit, setLimit] = useState(40)
  const isMobile = useIsMobile()
  const filters = useMemo<GameLinesFilters>(() => ({ sport, market, matchup: 'all', side: 'all', minEdge: 0, q: '' }), [sport, market])
  const data = useMemo(() => buildGameLinesData(feeds, filters), [feeds, filters])
  const label = useMemo(() => gameLinesViewLabel(Object.values(feeds)), [feeds])
  const unavailable = Object.values(feeds).every(f => !f || f.status === 'unavailable')
  const shown = view === 'cards' ? data.pricedGames.length : data.rows.length

  return (
    <div>
      <GameLinesNotice label={label.label} validated={label.validated} />
      <div role="group" aria-label="Market" className="flex gap-1.5 flex-wrap mb-3">
        {MARKETS.map(m => (
          <button
            key={m.key}
            type="button"
            aria-pressed={market === m.key}
            onClick={() => { setMarket(m.key); setLimit(40) }}
            className="mono text-mini py-1 px-[10px] cursor-pointer"
            style={{
              borderRadius: '6px',
              border: `1px solid ${market === m.key ? 'var(--color-border-button-hover)' : 'var(--color-separator-border)'}`,
              background: market === m.key ? 'var(--color-background-tertiary-default)' : 'transparent',
              color: market === m.key ? '#ffffff' : 'var(--color-text-secondary)',
            }}
          >{m.label}</button>
        ))}
      </div>
      {unavailable ? (
        <div role="status" className="py-6 px-0 text-neutral-300 text-cozy">
          The NBA / NHL game-line feed is not published yet. It refreshes with the odds banker; check back shortly.
        </div>
      ) : shown === 0 ? (
        <div role="status" className="py-6 px-0 text-neutral-300 text-cozy">
          No priced games for this selection. Game lines appear once a slate and book prices are posted.
        </div>
      ) : (
        <GameLinesView pricedGames={data.pricedGames} rows={data.rows} view={view} limit={limit} onSwitchView={setView} isMobile={isMobile} />
      )}
    </div>
  )
}
