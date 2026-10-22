import type { Metadata } from 'next'
import { apiGetOddsBoard } from '@/lib/betting-api'
import { limitOddsBoard } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import type { OddsBoard } from '@/lib/odds-screen'
import type { ProjectionSport } from '@/lib/sports'
import { OddsClient } from './odds-client'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title: 'Odds Screen',
  description:
    'Every sportsbook and exchange price for NFL, NHL and NBA player props side by side, with fair value and EV from the reference books you choose.',
}

const SPORTS: ProjectionSport[] = ['nfl', 'nhl', 'nba']

export default async function OddsPage() {
  const [boards, viewer] = await Promise.all([
    Promise.all(SPORTS.map(s => apiGetOddsBoard(s, { limit: 3000 }).catch(() => null))),
    getViewer(),
  ])
  // Pro surface (lib/access.ts): cut here so withheld rows are never in the payload.
  let locked = 0
  const cut: Partial<Record<ProjectionSport, OddsBoard>> = {}
  boards.forEach((b, i) => {
    if (!b) return
    const c = limitOddsBoard(b, viewer)
    locked += c.locked
    cut[SPORTS[i]] = c.board
  })
  return <OddsClient initial={cut} initialLocked={locked} />
}
