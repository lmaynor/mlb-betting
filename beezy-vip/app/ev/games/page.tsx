export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import Link from 'next/link'
import { limitGameLines } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import { apiGetGameProjections } from '@/lib/betting-api'
import type { GameProjectionsFeed } from '@/lib/game-lines'
import { GameLinesClient } from './game-lines-client'

export const metadata: Metadata = {
  title: 'Game Lines',
  description: 'NBA and NHL moneyline, spread / puck line and total: model estimate next to the books\' de-vigged prices. Unvalidated.',
}

type GameSport = 'all' | 'nba' | 'nhl'
const SPORT_TABS: { key: GameSport; label: string }[] = [
  { key: 'all', label: 'NBA + NHL' },
  { key: 'nba', label: 'NBA' },
  { key: 'nhl', label: 'NHL' },
]

/** The Scanner's Game Lines tab: "what is the model saying against the market on game lines?", the game-line counterpart of the
 *  Odds Terminal's player props. These are UNVALIDATED model estimates with no tracked record, so they live here beside the
 *  market data and not under Picks (which is the tracked, graded record). MLB's tracked game picks stay at /picks/games. */
export default async function GameLinesPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const params = await searchParams
  const sport: GameSport = params.sport === 'nba' || params.sport === 'nhl' ? params.sport : 'all'
  const viewer = await getViewer()
  const [nba, nhl] = await Promise.all([
    sport === 'nhl' ? Promise.resolve(null) : apiGetGameProjections('nba').catch(() => null),
    sport === 'nba' ? Promise.resolve(null) : apiGetGameProjections('nhl').catch(() => null),
  ])
  // Same tier rule as the Odds Terminal (limitGameLines): cut before the payload leaves the server.
  const cut = (feed: GameProjectionsFeed | null): GameProjectionsFeed | null => {
    if (!feed) return null
    const c = limitGameLines(feed, viewer)
    return { ...c.feed, locked: c.locked }
  }
  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '16px 24px 24px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap', marginBottom: '12px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>Game Lines</h1>
        <nav aria-label="Game lines sport" style={{ display: 'flex', gap: '14px', fontSize: '12.5px' }}>
          {SPORT_TABS.map(t => (
            <Link
              key={t.key}
              href={t.key === 'all' ? '/ev/games' : `/ev/games?sport=${t.key}`}
              aria-current={sport === t.key ? 'page' : undefined}
              style={{ color: sport === t.key ? 'var(--chalk)' : 'var(--link)', fontWeight: sport === t.key ? 600 : 400, textDecoration: 'none' }}
            >{t.label}</Link>
          ))}
        </nav>
      </div>
      <GameLinesClient feeds={{ nba: cut(nba), nhl: cut(nhl) }} sport={sport} />
      <p style={{ fontSize: '12px', color: 'var(--fog)', marginTop: '16px' }}>
        Looking for MLB game picks with a graded record? They are under <Link href="/picks/games" style={{ color: 'var(--link)' }}>Picks &rarr; Game Picks</Link>.
      </p>
    </div>
  )
}
