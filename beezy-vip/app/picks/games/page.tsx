export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { SportBoardPage } from '@/components/picks/sport-board-page'
import type { SportSlug } from '@/lib/sports'

export const metadata: Metadata = {
  title: 'Game Picks',
  description: 'MLB, NHL, and NBA game picks: moneyline, puck line / spread, and game totals, ranked by Beezy Score and settled publicly.',
}

export default async function GamePicksPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const params = await searchParams
  const sport: SportSlug = params.sport === 'nba' ? 'nba' : params.sport === 'nhl' ? 'nhl' : 'mlb'
  return <SportBoardPage sport={sport} view="picks" kind="game" searchParams={searchParams} />
}
