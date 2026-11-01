import type { Metadata } from 'next'
import { permanentRedirect } from 'next/navigation'
import { apiGetAnalyticsDeepDive, apiGetOddsBoard } from '@/lib/betting-api'
import { canViewFull } from '@/lib/access'
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

export default async function OddsPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  // Game Lines left the strategy-mode strip: the old deep link goes to its own page before anything is fetched.
  const { view } = await searchParams
  if (view === 'game_lines') permanentRedirect('/ev/games')
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
  // Whole-Terminal access (Pro, or anyone while the site is pre-launch / tiers are not enforced): drives the outlier book reveal.
  const fullAccess = canViewFull(viewer, 'ev_terminal', 'mlb')
  return <OddsClient initial={cut} initialLocked={locked} headline={deep?.ev_headline ?? null} fullAccess={fullAccess} />
}
