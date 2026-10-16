export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { PicksCard } from '@/components/picks/picks-card'

export const metadata: Metadata = {
  title: 'MLB Player Picks',
  description: 'Model picks and +EV alerts on MLB player props, ranked by Beezy Score. Every pick is tracked and settled publicly.',
}

export default function MlbPlayerPicksPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  return <PicksCard kind="player" searchParams={searchParams} />
}
