/**
 * Server-side data for a sport's picks / projections / system pages. ONE loader for every sport: the sport's registry entry
 * (`board`) says whether the rows come from the tracked MLB bets or from the projection feed, and both come back as the same
 * `BoardRow`s with the same games list, so a new sport needs no page code. The access policy (lib/access.ts) is applied
 * here, so withheld rows never reach the browser.
 *
 * Two days exist: today (the live board) and yesterday (the settled MLB card, or the archived board of the projection
 * sports). Anything older is the Results ledger and Picks history, not this page.
 */
import { limitRows, type Viewer } from '@/lib/access'
import { apiGetEvFeed, apiGetPicks, apiGetSportProjections, apiGetTodayAllPredictions, apiGetTodaySlate } from '@/lib/betting-api'
import { gamesFromSlate, mergeGames, type BoardGame } from '@/lib/board-games'
import { boardStatusText, confluenceKey, fromMlbProjections, fromSportProjections, systemTodayCounts, type BoardRow, type SystemToday } from '@/lib/board-rows'
import { addDaysToDateKey, siteDateKey } from '@/lib/dates'
import { shapeHub } from '@/lib/hub-data'
import { fromBets } from '@/lib/mlb-board'
import { buildMlbProjections } from '@/lib/mlb-projections'
import { betKind, type PickKind } from '@/lib/pick-systems'
import type { PickDay } from '@/lib/picks-filters'
import { hasHeadshots } from '@/lib/sport-assets'
import { playerIdByName } from '@/lib/sport-assets.server'
import { SPORT_PILLARS, type ProjectionSport, type SportSlug } from '@/lib/sports'
import type { Bet, EvBookOutlierRow } from '@/lib/types'

export type BoardView = 'picks' | 'projections'

export interface SportBoard {
  /** the day shown and its date key (site clock) */
  day: PickDay
  date: string
  rows: BoardRow[]
  /** every game of the day (the games strip), with the number of rows in each */
  games: BoardGame[]
  /** rows withheld by the access policy */
  locked: number
  /** one-line freshness note for the board, when the feed has one */
  status?: string
  /** the status line should read as a warning (the lines on the board are stale) */
  statusWarn?: boolean
  /** copy for the empty state, when it should differ from the default */
  emptyBody?: string
  /** books and exchanges beating the consensus (projection sports, today only) */
  outliers: EvBookOutlierRow[]
  lockedPrices: number
  block: { status: string; age_minutes: number | null } | null
  /** player|stat|side keys the projection board also favors, to badge the matching market prices */
  confluenceKeys: string[]
  /** per-system row counts, from the uncut feed (projection sports) */
  today: Record<string, SystemToday>
}

/** The most rows the API serves in one page: the whole board (a week of NFL, three days of NHL), nearest game day first. */
const BOARD_LIMIT = 1200

export const dayKey = (day: PickDay) => (day === 'yesterday' ? addDaysToDateKey(siteDateKey(), -1) : siteDateKey())

const EMPTY: Omit<SportBoard, 'day' | 'date' | 'rows' | 'games' | 'locked'> = { outliers: [], lockedPrices: 0, block: null, confluenceKeys: [], today: {} }

async function mlbBoard(sport: SportSlug, viewer: Viewer, day: PickDay, view: BoardView, kind?: PickKind): Promise<SportBoard> {
  const date = dayKey(day)
  // the card the model and the +EV scanner logged for the day; the projections view also wants the unstaked predictions (today only)
  const bets: Bet[] = view === 'projections' && day === 'today'
    ? await apiGetTodayAllPredictions().catch(() => [] as Bet[])
    : await apiGetPicks({ date, limit: 200 }).catch(() => [] as Bet[])
  const scoped = kind ? bets.filter(b => betKind(b) === kind) : bets
  const adapt = (bs: Bet[]) => (view === 'projections' ? fromMlbProjections(buildMlbProjections(bs)) : fromBets(bs))
  // player picks and game picks are separate products with separate access, so they are adapted (and cut) separately
  const player = adapt(scoped.filter(b => betKind(b) === 'player'))
  const game = adapt(scoped.filter(b => betKind(b) === 'game'))
  // Today's card is the paid surface; a settled day is the public record (as the Results ledger is).
  let rows = [...player, ...game]
  let locked = 0
  if (day === 'today') {
    const p = limitRows(player, viewer, 'player_board', sport, r => r.score)
    const g = limitRows(game, viewer, 'game_picks', sport, r => r.score)
    rows = [...p.rows, ...g.rows]
    locked = p.locked + g.locked
  }
  const slate = day === 'today' ? await apiGetTodaySlate().catch(() => null) : null
  const label = kind === 'game' ? 'game picks' : 'picks'
  return {
    ...EMPTY, day, date, rows, locked,
    games: gamesFromSlate(slate, rows),
    emptyBody: view === 'projections'
      ? 'The model projections publish with the ~11:00 AM CT run, once lineups and prices are in.'
      : day === 'today'
        ? `No ${label} are logged yet today. The model card publishes after the ~11:00 AM CT run and +EV alerts post through the day.`
        : `No ${label} were logged yesterday.`,
  }
}

export async function loadSportBoard(
  sport: SportSlug, viewer: Viewer, opts: { day?: PickDay; view?: BoardView; kind?: PickKind } = {},
): Promise<SportBoard> {
  const day = opts.day ?? 'today'
  const view = opts.view ?? 'picks'
  if (SPORT_PILLARS[sport].board === 'tracked') return mlbBoard(sport, viewer, day, view, opts.kind)

  const ps = sport as ProjectionSport
  const date = dayKey(day)
  const [feed, pr] = await Promise.all([
    // market prices exist for today only; yesterday's prices are history
    day === 'today' ? apiGetEvFeed({ sport: ps, minEv: 0.02, limit: 150 }).catch(() => null) : Promise.resolve(null),
    apiGetSportProjections(ps, { limit: BOARD_LIMIT, ...(day === 'yesterday' ? { date } : {}) }).catch(() => null),
  ])
  // Headshots resolve by name on the server, for the sports that have a source.
  const withHeads = pr && hasHeadshots(sport)
    ? { ...pr, rows: pr.rows.map(r => ({ ...r, headshot_id: playerIdByName(sport, r.player, r.team) })) }
    : pr
  const hub = shapeHub(ps, feed, withHeads, viewer)
  const rows = fromSportProjections(hub.projections?.rows ?? [])
  const confluenceKeys = (hub.projections?.rows ?? []).filter(r => r.confluence && r.side).map(r => confluenceKey(r.player, r.stat, r.side as string))
  const unavailable = !hub.projections || hub.projections.status === 'unavailable'
  return {
    day, date, rows,
    games: mergeGames(sport, pr?.games, rows),
    locked: hub.lockedProjections,
    status: day === 'yesterday'
      ? (unavailable ? undefined : 'Yesterday\'s board: each row as it stood before its game')
      : hub.projections ? boardStatusText(hub.projections) : undefined,
    statusWarn: day === 'today' && hub.projections?.lines_stale === true,
    emptyBody: day === 'yesterday'
      ? 'Yesterday\'s board was not saved. Boards are archived from the day this feature went live.'
      : unavailable
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
