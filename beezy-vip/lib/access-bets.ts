/**
 * The access policy (lib/access.ts) applied to raw MLB bet rows, for every route and page that serves today's picks:
 * the Next proxies, the per-system pages and the Daily Card. Settled / past days are never cut.
 *
 * Counting is per PROP, not per row: the same prop flagged at five books is one pick, so a free preview of 5 is five props
 * (every book's row for them is kept) rather than one prop repeated.
 */
import { limitRows, type Env, type Viewer } from '@/lib/access'
import { beezyscore } from '@/lib/beezy-score'
import { collapseBooks, propKey } from '@/lib/dedupe'
import { betKind, type PickKind } from '@/lib/pick-systems'
import type { Bet } from '@/lib/types'

const SURFACE = { player: 'player_board', game: 'game_picks' } as const

export function limitBets<T extends Bet>(bets: T[], viewer: Viewer, today: string, env?: Env): { bets: T[]; locked: number } {
  const live = bets.filter(b => b.game_date >= today)
  if (live.length === 0) return { bets, locked: 0 }

  const keep = new Set<string>()
  let locked = 0
  for (const kind of ['player', 'game'] as PickKind[]) {
    const props = collapseBooks(live.filter(b => betKind(b) === kind))
    const l = limitRows(props, viewer, SURFACE[kind], 'mlb', beezyscore, env)
    locked += l.locked
    for (const p of l.rows) keep.add(propKey(p))
  }
  return { bets: bets.filter(b => b.game_date < today || keep.has(propKey(b))), locked }
}
