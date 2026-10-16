/**
 * What an NFL / NHL / NBA hub is allowed to receive. Pure so the siloing (a hub only ever sees its own sport) and the access
 * cut are testable without Clerk or the network; the server page calls it and hands the result to the client component.
 */
import { limitRows, type Viewer } from '@/lib/access'
import type { EvBookOutlierRow, EvFeed, SportProjections } from '@/lib/types'

export type HubSport = 'nfl' | 'nhl' | 'nba'

export interface HubData {
  projections: SportProjections | null
  lockedProjections: number
  outliers: EvBookOutlierRow[]
  lockedPrices: number
  block: { status: string; age_minutes: number | null } | null
}

type Env = Record<string, string | undefined>

export function shapeHub(sport: HubSport, feed: EvFeed | null, projections: SportProjections | null, viewer: Viewer, env: Env = process.env): HubData {
  const board = limitRows(projections?.rows ?? [], viewer, 'player_board', sport, r => r.beezy_score, env)
  // NFL carries its book/exchange outliers under `book_outliers` (its `rows` are Kalshi asks, shown on /ev); NHL and NBA under `rows`.
  const all: EvBookOutlierRow[] = sport === 'nfl' ? (feed?.nfl?.book_outliers ?? []) : (feed?.[sport]?.rows ?? [])
  const prices = limitRows(all, viewer, 'player_board', sport, r => r.ev, env)
  const mine = feed?.[sport] ?? null
  return {
    projections: projections ? { ...projections, rows: board.rows } : null,
    lockedProjections: board.locked,
    outliers: prices.rows,
    lockedPrices: prices.locked,
    block: mine ? { status: mine.status, age_minutes: mine.age_minutes } : null,
  }
}
