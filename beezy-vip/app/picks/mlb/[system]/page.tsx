import { notFound }        from 'next/navigation'
import { SystemPicksPage } from '@/components/picks/system-picks-page'
import type { Metadata }   from 'next'

const VALID = ['nrfi', 'hr', 'f5', 'k', 'outs'] as const
type Slug   = typeof VALID[number]

const META: Record<Slug, { name: string; desc: string }> = {
  nrfi: { name: 'No Run First Inning', desc: "Today's NRFI picks from the Beezy.VIP XGBoost model." },
  hr:   { name: 'Home Run Props',      desc: "Today's HR prop picks from the Beezy.VIP model." },
  f5:   { name: 'First 5 Innings',     desc: "Today's F5 moneyline picks from the Beezy.VIP model." },
  k:    { name: 'Strikeout Props',     desc: "Today's pitcher strikeout picks from the Beezy.VIP model." },
  outs: { name: 'Outs Props',          desc: "Today's pitcher outs picks from the Beezy.VIP model." },
}

type Props = { params: Promise<{ system: string }> }

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { system } = await params
  const slug = system.toLowerCase() as Slug
  if (!VALID.includes(slug)) return { title: 'Not Found' }
  return {
    title:       `${META[slug].name} Picks -- Beezy.VIP`,
    description: META[slug].desc,
  }
}

export default async function SystemPage({ params }: Props) {
  const { system } = await params
  const slug = system.toLowerCase()
  if (!VALID.includes(slug as Slug)) notFound()
  return <SystemPicksPage system={slug.toUpperCase()} />
}
