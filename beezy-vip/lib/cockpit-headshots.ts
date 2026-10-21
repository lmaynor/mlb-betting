/**
 * Headshot ids for the /edge cockpit rows (SERVER only: the player maps are too big for a client bundle).
 * MLB rows without an id resolve by name; NFL rows get `headshot_id` (the nfl.com image token, keyed by name + team) because
 * the feed's NFL player_id is not an image id. NHL / NBA already carry a usable player_id.
 */
import { playerIdByName } from '@/lib/sport-assets.server'
import { isOffshore, onshoreQuotes } from '@/lib/offshore'
import type { CockpitRow } from '@/lib/types'

export function withHeadshotIds(rows: CockpitRow[]): CockpitRow[] {
  return rows.filter(r => !isOffshore(r.book)).map(r => ({ ...r, all_books: r.all_books ? onshoreQuotes(r.all_books) : r.all_books })).map(r => {
    if (!r.player) return r
    if (r.sport === 'mlb' && r.player_id == null) return { ...r, player_id: playerIdByName('mlb', r.player) }
    if (r.sport === 'nfl' && r.headshot_id == null) return { ...r, headshot_id: playerIdByName('nfl', r.player, r.team) }
    return r
  })
}
