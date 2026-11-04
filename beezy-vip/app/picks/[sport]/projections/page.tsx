import { notFound, permanentRedirect } from 'next/navigation'
import { isSportSlug } from '@/lib/sports'

type Props = { params: Promise<{ sport: string }>; searchParams: Promise<Record<string, string>> }

/** Projections moved to the Scanner's own tab: /picks/nba/projections is now /ev/projections?league=nba (filters carry over). */
export default async function SportProjectionsRedirect({ params, searchParams }: Props) {
  const { sport } = await params
  if (!isSportSlug(sport)) notFound()
  const sp = new URLSearchParams(await searchParams)
  sp.set('league', sport)
  permanentRedirect(`/ev/projections?${sp.toString()}`)
}
