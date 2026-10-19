export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { SportBoardPage } from '@/components/picks/sport-board-page'

export const metadata: Metadata = {
  title: 'Game Picks',
  description: 'MLB game picks: no-run-first-inning, first-5, first-half and full-game sides, ranked by Beezy Score and settled publicly.',
}

export default function GamePicksPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  return <SportBoardPage sport="mlb" view="picks" kind="game" searchParams={searchParams} />
}
