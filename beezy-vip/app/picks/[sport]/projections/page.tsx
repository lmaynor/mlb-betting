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
  return {
    title: `${p.shortName} Projections`,
    description: `The projection for every ${p.shortName} player prop on today's slate, beside the line, the market consensus and the best price.`,
  }
}

export default async function SportProjectionsRoute({ params, searchParams }: Props) {
  const { sport } = await params
  if (!isSportSlug(sport)) notFound()
  return <SportBoardPage sport={sport} view="projections" kind={sport === 'mlb' ? 'player' : undefined} searchParams={searchParams} />
}
