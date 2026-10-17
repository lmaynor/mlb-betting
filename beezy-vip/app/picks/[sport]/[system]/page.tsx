export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { ProjectionSystemPage } from '@/components/picks/projection-system-page'
import { SystemPicksPage } from '@/components/picks/system-picks-page'
import { getSportSystemBySlug } from '@/lib/pick-systems'
import { isSportSlug, SPORT_PILLARS, type ProjectionSport } from '@/lib/sports'

type Props = { params: Promise<{ sport: string; system: string }>; searchParams: Promise<Record<string, string>> }

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { sport, system } = await params
  const spec = isSportSlug(sport) ? getSportSystemBySlug(sport, system) : undefined
  if (!spec) return { title: 'Not Found' }
  return {
    title: `${SPORT_PILLARS[spec.sport].shortName} ${spec.name} Picks`,
    description: `Today's ${spec.name} for ${SPORT_PILLARS[spec.sport].shortName} from Beezy.FYI. ${spec.description}`,
  }
}

/** One system of any sport, resolved from the registry (lib/pick-systems.ts). Shelved systems still resolve, so old URLs keep working. */
export default async function SystemRoute({ params, searchParams }: Props) {
  const { sport, system } = await params
  if (!isSportSlug(sport)) notFound()
  const spec = getSportSystemBySlug(sport, system)
  if (!spec) notFound()
  return SPORT_PILLARS[sport].board === 'tracked'
    ? <SystemPicksPage system={spec.key} />
    : <ProjectionSystemPage sport={sport as ProjectionSport} system={spec} searchParams={searchParams} />
}
