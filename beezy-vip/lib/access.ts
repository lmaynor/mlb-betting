/**
 * Who sees how much of the picks product. Pure (no Clerk, no Next) so pages, API proxies and tests share one policy.
 *
 *   free         Top 10 player picks, picks history, every PAST day's card, and a short preview of today's boards
 *   single_sport everything for the ONE league chosen at checkout (its player board, its game picks, its prop-research cockpit)
 *   all-access   everything, including the +EV Terminal (the Pro plan's feature; a Single-Sport Pass does not include it)
 *
 * Today's boards are the paid surface: the player board per league (MLB card, NFL / NHL / NBA projections and prices)
 * and game picks (MLB only so far). Settled history is public so the record stays checkable.
 *
 * Enforcement is SERVER-SIDE: callers cut the rows before they reach the client or the response body. Hiding rows in the
 * browser would still ship them. While `PRE_LAUNCH` is true nothing is gated (there is no way to pay yet); set the server
 * env `ENFORCE_TIERS=1` to exercise the gates before launch.
 */
import { PRE_LAUNCH } from '@/lib/launch'
import { isAllAccess, isLeague, type League, type Tier } from '@/lib/tiers'

export type Surface = 'player_board' | 'game_picks' | 'cockpit' | 'ev_terminal'

export interface Viewer { tier: Tier; sport: League | null }

/** Rows of a player board a free viewer sees (the highest-scoring ones). Game picks have no free rows. */
export const FREE_PREVIEW_ROWS: Record<Surface, number> = { player_board: 5, game_picks: 0, cockpit: 5, ev_terminal: 5 }

export type Env = Record<string, string | undefined>

export function gatingOn(env: Env = process.env): boolean {
  return !PRE_LAUNCH || env.ENFORCE_TIERS === '1'
}

/** Can this viewer see the whole of today's `surface` for `sport`? */
export function canViewFull(viewer: Viewer, surface: Surface, sport: League, env: Env = process.env): boolean {
  if (!gatingOn(env)) return true
  if (isAllAccess(viewer.tier)) return true
  if (surface === 'ev_terminal') return false // Pro only
  return viewer.tier === 'single_sport' && viewer.sport === sport
}

export interface Limited<T> { rows: T[]; locked: number; full: boolean }

/**
 * Apply the policy to a list. `score` orders the preview (best first); a full view keeps the caller's order untouched.
 * `locked` is how many rows were withheld, so the page can say so without revealing them.
 */
export function limitRows<T>(rows: T[], viewer: Viewer, surface: Surface, sport: League, score: (r: T) => number, env: Env = process.env): Limited<T> {
  if (canViewFull(viewer, surface, sport, env)) return { rows, locked: 0, full: true }
  const keep = FREE_PREVIEW_ROWS[surface]
  const ranked = [...rows].sort((a, b) => score(b) - score(a)).slice(0, keep)
  return { rows: ranked, locked: rows.length - ranked.length, full: false }
}

/** Viewer from raw Clerk metadata values. Unknown / missing -> free. */
export function viewerOf(tier: Tier, sportChoice: unknown): Viewer {
  const sport = isLeague(sportChoice) ? sportChoice : null
  return { tier, sport }
}
