/** Server-side loader for the Prop Research list: the sweep cockpit plus the projection boards, merged (lib/cockpit-players.ts). */
import { apiGetEdgeCockpit, apiGetSportProjections } from '@/lib/betting-api'
import { withProjectionPlayers } from '@/lib/cockpit-players'
import type { ProjectionSport } from '@/lib/sports'
import type { CockpitFeed, SportProjections } from '@/lib/types'

const BOARDS: ProjectionSport[] = ['nba', 'nhl', 'nfl']
/** the API's rows run nearest game day first, so this window is tonight's and tomorrow's players, not the whole week */
const BOARD_LIMIT = 400

/** Each source fails on its own: a sweep outage still leaves the boards, and a board outage still leaves the sweep. */
export async function loadCockpit(opts: { sport?: string; limit?: number; minEv?: number } = {}): Promise<CockpitFeed | null> {
  const want = BOARDS.filter(s => !opts.sport || opts.sport === 'all' || opts.sport === s)
  const [feed, ...boards] = await Promise.all([
    apiGetEdgeCockpit({ sport: opts.sport, limit: opts.limit ?? 60, minEv: opts.minEv }).catch(() => null),
    ...want.map(s => apiGetSportProjections(s, { limit: BOARD_LIMIT }).catch(() => null)),
  ])
  const byLeague: Partial<Record<ProjectionSport, SportProjections | null>> = {}
  want.forEach((s, i) => { byLeague[s] = boards[i] })
  return withProjectionPlayers(feed, byLeague)
}
