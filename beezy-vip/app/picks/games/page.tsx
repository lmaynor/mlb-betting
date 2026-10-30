export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { SportBoardPage } from '@/components/picks/sport-board-page'
import type { SportSlug } from '@/lib/sports'

export const metadata: Metadata = {
  title: 'Game Picks',
  description: 'MLB and NBA game picks: moneyline, spread, and game totals, ranked by Beezy Score and settled publicly.',
}

export default async function GamePicksPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const params = await searchParams
  const sport: SportSlug = params.sport === 'nba' ? 'nba' : 'mlb'
  return <SportBoardPage sport={sport} view="picks" kind="game" searchParams={searchParams} />
}
