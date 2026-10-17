export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { SystemsPage } from '@/components/picks/systems-page'
import { isSportSlug, SPORT_PILLARS } from '@/lib/sports'

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
