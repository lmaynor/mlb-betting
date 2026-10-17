/**
 * Server-side data for a sport's picks / projections / system pages. ONE loader for every sport: the sport's registry
 * entry (`board`) says whether the rows come from the tracked MLB predictions or from the projection feed, so a new sport
 * needs no page code. The access policy (lib/access.ts) is applied here, so withheld rows never reach the browser.
 */
import { limitRows, type Viewer } from '@/lib/access'
import { apiGetEvFeed, apiGetSportProjections, apiGetTodayAllPredictions } from '@/lib/betting-api'
import { boardStatusText, confluenceKey, fromMlbProjections, fromSportProjections, systemTodayCounts, type BoardRow, type SystemToday } from '@/lib/board-rows'
import { shapeHub } from '@/lib/hub-data'
import { buildMlbProjections } from '@/lib/mlb-projections'
import { hasHeadshots } from '@/lib/sport-assets'
import { playerIdByName } from '@/lib/sport-assets.server'
import { SPORT_PILLARS, type ProjectionSport, type SportSlug } from '@/lib/sports'
import type { Bet, EvBookOutlierRow } from '@/lib/types'

export interface SportBoard {
  rows: BoardRow[]
  /** rows withheld by the access policy */
  locked: number
  /** one-line freshness note for the board, when the feed has one */
  status?: string
  /** the status line should read as a warning (the lines on the board are stale) */
  statusWarn?: boolean
  /** copy for the empty state, when it should differ from the default */
  emptyBody?: string
  /** books and exchanges beating the consensus (projection sports only) */
  outliers: EvBookOutlierRow[]
  lockedPrices: number
  block: { status: string; age_minutes: number | null } | null
  /** player|stat|side keys the projection board also favors, to badge the matching market prices */
  confluenceKeys: string[]
  /** per-system row counts today, from the uncut feed (projection sports) */
  today: Record<string, SystemToday>
}

export async function loadSportBoard(sport: SportSlug, viewer: Viewer): Promise<SportBoard> {
  if (SPORT_PILLARS[sport].board === 'tracked') {
    const bets = await apiGetTodayAllPredictions().catch(() => [] as Bet[])
    const board = limitRows(buildMlbProjections(bets), viewer, 'player_board', sport, r => r.score)
    return { rows: fromMlbProjections(board.rows), locked: board.locked, outliers: [], lockedPrices: 0, block: null, confluenceKeys: [], today: {} }
  }
  const ps = sport as ProjectionSport
  const [feed, pr] = await Promise.all([
    apiGetEvFeed({ sport: ps, minEv: 0.02, limit: 150 }).catch(() => null),
    apiGetSportProjections(ps).catch(() => null),
  ])
  // Headshots resolve by name on the server, for the sports that have a source.
  const withHeads = pr && hasHeadshots(sport)
    ? { ...pr, rows: pr.rows.map(r => ({ ...r, headshot_id: playerIdByName(sport, r.player, r.team) })) }
    : pr
  const hub = shapeHub(ps, feed, withHeads, viewer)
  const rows = fromSportProjections(hub.projections?.rows ?? [])
  const confluenceKeys = (hub.projections?.rows ?? []).filter(r => r.confluence && r.side).map(r => confluenceKey(r.player, r.stat, r.side as string))
  return {
    rows,
    locked: hub.lockedProjections,
    status: hub.projections ? boardStatusText(hub.projections) : undefined,
    statusWarn: hub.projections?.lines_stale === true,
    emptyBody: !hub.projections || hub.projections.status === 'unavailable'
      ? 'The projection board could not be loaded. It refreshes automatically; try again in a few minutes.'
      : `No ${SPORT_PILLARS[sport].shortName} games are on the schedule right now. The board fills in once a slate and prices are available.`,
    // NFL price rows have no usable league id; resolve every row's face by name here, on the server
    outliers: hasHeadshots(sport) ? hub.outliers.map(o => ({ ...o, headshot_id: playerIdByName(sport, o.player, o.team) })) : hub.outliers,
    lockedPrices: hub.lockedPrices,
    block: hub.block,
    confluenceKeys,
    today: systemTodayCounts(pr?.rows ?? []),
  }
}
