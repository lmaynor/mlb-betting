export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import Link from 'next/link'
import { SportBoardPage } from '@/components/picks/sport-board-page'
import { limitGameLines } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import { apiGetGameProjections } from '@/lib/betting-api'
import { isMlbOffseason } from '@/lib/dates'
import type { GameProjectionsFeed } from '@/lib/game-lines'
import type { SportSlug } from '@/lib/sports'
import { GamePicksClient } from './game-picks-client'

export const metadata: Metadata = {
  title: 'Game Picks',
  description: 'MLB, NHL, and NBA game lines: moneyline, puck line / spread, and game totals next to the books\' prices.',
}

type GameSport = 'all' | 'nba' | 'nhl' | 'mlb'

/** ?sport= picks the sport; with none, MLB is the default only while baseball is in season (it used to be the only choice,
 *  which left every NBA / NHL game line unreachable in the offseason). */
function pickSport(raw: string | undefined): GameSport {
  if (raw === 'nba' || raw === 'nhl' || raw === 'mlb' || raw === 'all') return raw
  return isMlbOffseason() ? 'all' : 'mlb'
}

const SPORT_TABS: { key: GameSport; label: string }[] = [
  { key: 'all', label: 'NBA + NHL' },
  { key: 'nba', label: 'NBA' },
  { key: 'nhl', label: 'NHL' },
  { key: 'mlb', label: 'MLB' },
]

export default async function GamePicksPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const params = await searchParams
  const sport = pickSport(params.sport)
  const nav = (
    <nav aria-label="Game lines sport" style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', fontSize: '12.5px', maxWidth: '1200px', margin: '0 auto', padding: '14px 24px 0' }}>
      {SPORT_TABS.map(t => (
        <Link
          key={t.key}
          href={`/picks/games?sport=${t.key}`}
          aria-current={sport === t.key ? 'page' : undefined}
          style={{ color: sport === t.key ? 'var(--chalk)' : 'var(--link)', fontWeight: sport === t.key ? 600 : 400, textDecoration: 'none' }}
        >{t.label}</Link>
      ))}
    </nav>
  )

  if (sport === 'mlb') {
    const mlb: SportSlug = 'mlb'
    return <>{nav}<SportBoardPage sport={mlb} view="picks" kind="game" searchParams={searchParams} /></>
  }

  const viewer = await getViewer()
  const [nba, nhl] = await Promise.all([
    sport === 'nhl' ? Promise.resolve(null) : apiGetGameProjections('nba').catch(() => null),
    sport === 'nba' ? Promise.resolve(null) : apiGetGameProjections('nhl').catch(() => null),
  ])
  // Same tier rule as the Odds Terminal's Game Lines (limitGameLines): cut before the payload leaves the server.
  const cut = (feed: GameProjectionsFeed | null): GameProjectionsFeed | null => {
    if (!feed) return null
    const c = limitGameLines(feed, viewer)
    return { ...c.feed, locked: c.locked }
  }
  return (
    <div>
      {nav}
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '16px 24px 24px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: '0 0 14px' }}>
          {sport === 'all' ? 'NBA + NHL game lines' : `${sport.toUpperCase()} game lines`}
        </h1>
        <GamePicksClient feeds={{ nba: cut(nba), nhl: cut(nhl) }} sport={sport === 'all' ? 'all' : sport} />
      </div>
    </div>
  )
}
