export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { PicksCard } from '@/components/picks/picks-card'
import { ProjectionPicksPage } from '@/components/picks/projection-picks-page'
import { isSportSlug, SPORT_PILLARS, type ProjectionSport } from '@/lib/sports'

type Props = { params: Promise<{ sport: string }>; searchParams: Promise<Record<string, string>> }

export async function generateMetadata({ params }: Pick<Props, 'params'>): Promise<Metadata> {
  const { sport } = await params
  if (!isSportSlug(sport)) return { title: 'Not Found' }
  const p = SPORT_PILLARS[sport]
  return { title: `${p.shortName} Picks`, description: p.description }
}

/**
 * Every sport's picks board lives here. The registry (lib/sports.ts `board`) picks the source: the tracked model card and
 * +EV alerts (MLB) or the projection board and market prices (the rest). A new sport adds no page.
 */
export default async function SportPicksPage({ params, searchParams }: Props) {
  const { sport } = await params
  if (!isSportSlug(sport)) notFound()
  return SPORT_PILLARS[sport].board === 'tracked'
    ? <PicksCard sport={sport} kind="player" searchParams={searchParams} />
    : <ProjectionPicksPage sport={sport as ProjectionSport} searchParams={searchParams} />
}
