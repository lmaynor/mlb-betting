import type { Metadata } from 'next'
import { notFound, redirect } from 'next/navigation'
import { getViewer } from '@/lib/auth'
import { apiGetEvFeed, apiGetSportProjections } from '@/lib/betting-api'
import { shapeHub } from '@/lib/hub-data'
import { isSportSlug, SPORT_PILLARS } from '@/lib/sports'
import { playerIdByName } from '@/lib/sport-assets.server'
import { SportHubClient } from './sport-hub-client'

export const dynamic = 'force-dynamic'

export async function generateMetadata({ params }: { params: Promise<{ sport: string }> }): Promise<Metadata> {
  const { sport } = await params
  if (!isSportSlug(sport)) return {}
  const p = SPORT_PILLARS[sport]
  return { title: `${p.shortName} Picks`, description: p.description }
}

/** NFL / NHL / NBA. MLB is its own section under /picks/mlb (next.config.ts redirects /sports/mlb, this is the backstop). */
export default async function SportHubPage({ params }: { params: Promise<{ sport: string }> }) {
  const { sport } = await params
  if (!isSportSlug(sport)) notFound()
  if (sport === 'mlb') redirect('/picks/mlb')

  const [feed, pr, viewer] = await Promise.all([
    apiGetEvFeed({ sport, minEv: 0.02, limit: 150 }).catch(() => null),
    apiGetSportProjections(sport).catch(() => null),
    getViewer(),
  ])
  // Headshots resolve by name on the server (NHL / NBA only; there is no NFL headshot source).
  const withHeads = pr && sport !== 'nfl'
    ? { ...pr, rows: pr.rows.map(r => ({ ...r, headshot_id: playerIdByName(sport, r.player, r.team) })) }
    : pr

  // The access policy (lib/access.ts) is applied HERE so withheld rows never reach the browser: the client only receives
  // what it renders, and only its own sport's data.
  const hub = shapeHub(sport, feed, withHeads, viewer)
  return <SportHubClient sport={sport} {...hub} />
}
