export const revalidate = 60

import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { SystemsPage } from '@/components/picks/systems-page'
import { isSportSlug, SPORT_PILLARS, SPORT_SLUGS } from '@/lib/sports'

/** Closed sport registry: prerender every sport's systems index; revalidate = 60 above keeps it ISR. */
export function generateStaticParams() {
  return SPORT_SLUGS.map(sport => ({ sport }))
}

type Props = { params: Promise<{ sport: string }> }

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { sport } = await params
  if (!isSportSlug(sport)) return { title: 'Not Found' }
  const p = SPORT_PILLARS[sport]
  return { title: `${p.shortName} Systems`, description: `Every ${p.shortName} system: its market, method, record and accuracy.` }
}

export default async function SportSystemsRoute({ params }: Props) {
  const { sport } = await params
  if (!isSportSlug(sport)) notFound()
  return <SystemsPage sport={sport} />
}
