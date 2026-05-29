import { notFound } from 'next/navigation'
import { SystemPicksPage } from '@/components/picks/system-picks-page'
import { PICK_SYSTEMS, getPickSystemBySlug } from '@/lib/pick-systems'
import type { Metadata } from 'next'

type Props = { params: Promise<{ system: string }> }

export function generateStaticParams() {
  return PICK_SYSTEMS.map(system => ({ system: system.slug }))
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { system } = await params
  const spec = getPickSystemBySlug(system)
  if (!spec) return { title: 'Not Found' }

  return {
    title: `${spec.name} Picks -- Beezy.FYI`,
    description: `Today's ${spec.name} picks from Beezy.FYI. ${spec.description}`,
  }
}

export default async function SystemPage({ params }: Props) {
  const { system } = await params
  const spec = getPickSystemBySlug(system)
  if (!spec) notFound()

  return <SystemPicksPage system={spec.key} />
}
