import type { OddsBoard } from '@/lib/odds-screen'
import { hasHeadshots } from '@/lib/sport-assets'
import { playerIdByName } from '@/lib/sport-assets.server'

/** Adds `headshot_id` to every row (name + team lookup in the sport's player map). SERVER only: the maps are large JSON. */
export function withHeadshots<T extends OddsBoard>(board: T): T {
  if (!hasHeadshots(board.sport)) return board
  const sport = board.sport
  return { ...board, rows: board.rows.map(r => ({ ...r, headshot_id: playerIdByName(sport, r.player, r.team) })) }
}
