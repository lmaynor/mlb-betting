export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { ContestClient } from '@/components/contest/contest-client'
import { contestEnabled } from '@/lib/contest/flag'
import { sampleFeed } from '@/lib/contest/fixture'
import './contest.css'

export const metadata: Metadata = {
  title: 'Bankroll Challenge',
  description: 'A play-money betting contest: straights, parlays, teasers and round robins, picks hidden until kickoff.',
  robots: { index: false, follow: false },
}

/**
 * The Bankroll Challenge. OFF by default: it answers 404 unless CONTEST_ENABLED is on (0 / false / off / no / empty all mean off).
 * Until the contest API exists (milestone M2) the page runs on SAMPLE data (lib/contest/fixture.ts) and says so in its header.
 */
export default async function ContestPage({ searchParams }: { searchParams?: Promise<Record<string, string | string[] | undefined>> } = {}) {
  if (!contestEnabled()) notFound()
  // ?preview=broke shows the hidden blackjack lifeline on the sample data (a player down to $40); it changes nothing else
  const sp = (await searchParams) ?? {}
  return <ContestClient feed={sampleFeed(new Date().toISOString(), { broke: sp.preview === 'broke' })} />
}
