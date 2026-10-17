export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { notFound, redirect } from 'next/navigation'
import { ProjectionModelDetail } from '@/components/models/projection-model-detail'
import { getModelSpec } from '@/lib/model-specs'
import { getSportSystemBySlug } from '@/lib/pick-systems'
import { isSportSlug, SPORT_PILLARS } from '@/lib/sports'

// `[slug]` is the sport here: Next needs one slug name per path level, and /models/[slug] already exists for MLB's own pages.
type Props = { params: Promise<{ slug: string; system: string }> }

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug: sport, system } = await params
  const spec = isSportSlug(sport) ? getSportSystemBySlug(sport, system) : undefined
  if (!spec) return { title: 'Model Not Found' }
  return {
    title: `${SPORT_PILLARS[spec.sport].shortName} ${spec.name} Model: Methodology`,
    description: `How the ${SPORT_PILLARS[spec.sport].shortName} ${spec.name.toLowerCase()} projection is built, how accurate it is, and what the studies say about prices. ${spec.description}`,
  }
}

/**
 * One model page for any sport's system: /models/nfl/pass-yds. MLB systems keep their /models/<slug> page (this redirects there);
 * NFL / NHL / NBA render the projection-model page. Unknown sports and systems 404.
 */
export default async function SportModelRoute({ params }: Props) {
  const { slug: sport, system } = await params
  if (!isSportSlug(sport)) notFound()
  const spec = getSportSystemBySlug(sport, system)
  if (!spec) notFound()
  if (sport === 'mlb') {
    if (getModelSpec(spec.slug)) redirect(`/models/${spec.slug}`)
    notFound()
  }
  return <ProjectionModelDetail system={spec} />
}
