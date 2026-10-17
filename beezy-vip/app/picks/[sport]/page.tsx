export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { SportBoardPage } from '@/components/picks/sport-board-page'
import { isSportSlug, SPORT_PILLARS } from '@/lib/sports'

type Props = { params: Promise<{ sport: string }>; searchParams: Promise<Record<string, string>> }

export async function generateMetadata({ params }: Pick<Props, 'params'>): Promise<Metadata> {
  const { sport } = await params
  if (!isSportSlug(sport)) return { title: 'Not Found' }
  const p = SPORT_PILLARS[sport]
  return { title: `${p.shortName} Picks`, description: p.description }
}

/** Every sport's PICKS tab. One shell (components/picks/sport-board-page.tsx); the registry picks the data source. */
export default async function SportPicksPage({ params, searchParams }: Props) {
  const { sport } = await params
  if (!isSportSlug(sport)) notFound()
  return <SportBoardPage sport={sport} view="picks" kind={sport === 'mlb' ? 'player' : undefined} searchParams={searchParams} />
}
