import { useEffect, useMemo, useState } from 'react'
import { apiGetGameOddsBoard, apiGetGameProjections } from '@/lib/betting-api'
import type { GameProjectionsFeed } from '@/lib/game-lines'
import type { GameOddsFeed } from '@/lib/game-odds'

// The banker rewrites the boards every 30 minutes, so a faster poll only re-downloads identical data.
const POLL_MS = 3 * 60_000

export interface GameBoardFeeds { odds: Record<string, GameOddsFeed | null>; proj: Record<string, GameProjectionsFeed | null> }

/** The price and model feeds of the game boards, kept fresh: re-fetched every few minutes while the tab is visible, replaced only when a feed's `as_of` changed. */
export function useGameFeeds(initial: GameBoardFeeds, opts: { model?: boolean } = {}) {
  const withModel = opts.model !== false
  const [odds, setOdds] = useState(initial.odds)
  const [proj, setProj] = useState(initial.proj)
  const feedSports = useMemo(() => Object.keys(odds).filter(s => odds[s]), [odds])

  useEffect(() => {
    const id = setInterval(async () => {
      if (document.visibilityState !== 'visible') return
      const next = await Promise.all(feedSports.map(async s => [s, await apiGetGameOddsBoard(s, { limit: 3000 }).catch(() => null), withModel ? await apiGetGameProjections(s).catch(() => null) : null] as const))
      setOdds(prev => {
        const out = { ...prev }
        let changed = false
        for (const [s, o] of next) if (o && Array.isArray(o.rows) && prev[s]?.as_of !== o.as_of) { out[s] = { ...o, locked: prev[s]?.locked }; changed = true }
        return changed ? out : prev
      })
      setProj(prev => {
        const out = { ...prev }
        let changed = false
        for (const [s, , p] of next) if (p && prev[s]?.as_of !== p.as_of) { out[s] = p; changed = true }
        return changed ? out : prev
      })
    }, POLL_MS)
    return () => clearInterval(id)
  }, [feedSports, withModel])

  return { odds, proj, feedSports }
}
