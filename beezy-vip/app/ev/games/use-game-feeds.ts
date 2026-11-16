import { useEffect, useMemo, useState } from 'react'
import { apiGetGameOddsBoard, apiGetGameProjections } from '@/lib/betting-api'
import type { GameProjectionsFeed } from '@/lib/game-lines'
import type { GameOddsFeed } from '@/lib/game-odds'

// The banker rewrites the boards every 30 minutes, so a faster poll only re-downloads identical data.
// 5 minutes (OPS-08) still samples each snapshot ~6 times; every avoided poll is one less invocation.
const POLL_MS = 5 * 60_000

export interface GameBoardFeeds { odds: Record<string, GameOddsFeed | null>; proj: Record<string, GameProjectionsFeed | null> }

/** The price and model feeds of the game boards, kept fresh: re-fetched every few minutes while the tab is visible, replaced only when a feed's `as_of` changed. */
export function useGameFeeds(initial: GameBoardFeeds, opts: { model?: boolean } = {}) {
  const withModel = opts.model !== false
  const [odds, setOdds] = useState(initial.odds)
  const [proj, setProj] = useState(initial.proj)
  const feedSports = useMemo(() => Object.keys(odds).filter(s => odds[s]), [odds])

  useEffect(() => {
    // OPS-08: skip a tick while the previous one is still fetching, so a slow upstream cannot stack polls.
    let running = false
    const id = setInterval(async () => {
      if (document.visibilityState !== 'visible' || running) return
      running = true
      try {
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
      } finally { running = false }
    }, POLL_MS)
    return () => clearInterval(id)
  }, [feedSports, withModel])

  return { odds, proj, feedSports }
}
