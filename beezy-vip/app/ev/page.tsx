import type { Metadata } from 'next'
import { apiGetAnalyticsDeepDive, apiGetOddsBoard } from '@/lib/betting-api'
import { limitOddsBoard } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import { withHeadshots } from '@/lib/odds-headshots'
import type { OddsBoard } from '@/lib/odds-screen'
import type { SportSlug } from '@/lib/sports'
import { OddsClient } from './odds-client'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title: 'Odds Terminal',
  description:
    'Every sportsbook and exchange price for NFL, NBA, NHL and MLB player props side by side, with fair value and EV from the reference books you choose, arbitrage and alt-line longshots.',
}

const SPORTS: SportSlug[] = ['nfl', 'nhl', 'nba', 'mlb']

export default async function OddsPage() {
  const [boards, viewer, deep] = await Promise.all([
    Promise.all(SPORTS.map(s => apiGetOddsBoard(s, { limit: 3000 }).catch(() => null))),
    getViewer(),
    apiGetAnalyticsDeepDive('EV').catch(() => null),
  ])
  // Pro surface (lib/access.ts): cut here so withheld rows are never in the payload.
  let locked = 0
  const cut: Partial<Record<SportSlug, OddsBoard>> = {}
  boards.forEach((b, i) => {
    if (!b) return
    const c = limitOddsBoard(b, viewer)
    locked += c.locked
    cut[SPORTS[i]] = withHeadshots(c.board)
  })
  return <OddsClient initial={cut} initialLocked={locked} headline={deep?.ev_headline ?? null} />
}
