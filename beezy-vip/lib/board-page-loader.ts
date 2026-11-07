/**
 * The board loader the picks PAGES use (SportBoardPage / SystemPage). Kept apart from lib/sport-board-data.ts
 * so the pure loader stays importable anywhere (tests included) without pulling in next/cache or Clerk.
 *
 * While tier gating is OFF, the access policy ignores the viewer (limitRows/canViewFull in lib/access.ts
 * return the full board for everyone), so the rendered board is a pure function of (sport, day, date, view,
 * kind) -- and an expensive one: two projection-board fetches plus the EV feed, transformed and shaped on
 * every render. In that state the result is cached for 60s (the data layer's own window) and the Clerk
 * session is never resolved. The moment gating turns ON this falls back to the per-viewer load, uncached:
 * a per-viewer cut is never stored or shared. If the cache is unavailable (unit tests, odd runtimes) the
 * direct load runs instead -- same result, just uncached.
 */
import { unstable_cache } from 'next/cache'
import { gatingOn, viewerOf } from '@/lib/access'
import { getViewer } from '@/lib/auth'
import type { PickDay } from '@/lib/picks-filters'
import type { PickKind } from '@/lib/pick-systems'
import { loadSportBoard, type BoardView, type SportBoard } from '@/lib/sport-board-data'
import type { SportSlug } from '@/lib/sports'

const loadBoardResultCached = unstable_cache(
  async (sport: SportSlug, day: PickDay, date: string, view: BoardView, kind: string) =>
    loadSportBoard(sport, viewerOf('public', null), { day, date: date || undefined, view, kind: (kind || undefined) as PickKind | undefined }),
  ['sport-board-result-v1'],
  { revalidate: 60 },
)

export async function loadSportBoardForPage(
  sport: SportSlug,
  opts: { day?: PickDay; view?: BoardView; kind?: PickKind; date?: string } = {},
): Promise<SportBoard> {
  if (gatingOn()) return loadSportBoard(sport, await getViewer(), opts)
  try {
    return await loadBoardResultCached(sport, opts.day ?? 'today', opts.date ?? '', opts.view ?? 'picks', opts.kind ?? '')
  } catch {
    return loadSportBoard(sport, viewerOf('public', null), opts)
  }
}
