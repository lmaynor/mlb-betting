import type { BoardRow } from './board-rows'

export type CardSort = 'ev' | 'score' | 'time'

/**
 * Filter board rows down to those suitable for card presentation:
 * must have a posted line, odds, book, at least 3 books, and either positive market EV or score >= 35.
 * Input order is preserved.
 */
export function cardGate(rows: BoardRow[]): BoardRow[] {
  return rows.filter(
    r =>
      r.hasLine &&
      r.odds != null &&
      r.book != null &&
      (r.nBooks ?? 0) >= 3 &&
      ((r.marketEv ?? 0) > 0 || r.score >= 35)
  )
}

/**
 * Sort cards by EV, score, or game tip time.
 * Pure function returning a new sorted array.
 */
export function sortCards(rows: BoardRow[], sort: CardSort): BoardRow[] {
  const evOf = (r: BoardRow) => r.marketEv ?? -Infinity

  return [...rows].sort((a, b) => {
    if (sort === 'ev') {
      const aEv = evOf(a)
      const bEv = evOf(b)
      if (bEv !== aEv) return bEv - aEv
      if (b.score !== a.score) return b.score - a.score
      return 0
    }

    if (sort === 'score') {
      if (b.score !== a.score) return b.score - a.score
      const aEv = evOf(a)
      const bEv = evOf(b)
      if (bEv !== aEv) return bEv - aEv
      return 0
    }

    // sort === 'time': start asc (ISO string compare; null starts last), tiebreak marketEv desc
    if (a.start == null && b.start == null) {
      const aEv = evOf(a)
      const bEv = evOf(b)
      if (bEv !== aEv) return bEv - aEv
      return 0
    }
    if (a.start == null) return 1
    if (b.start == null) return -1

    const cmp = a.start.localeCompare(b.start)
    if (cmp !== 0) return cmp

    const aEv = evOf(a)
    const bEv = evOf(b)
    if (bEv !== aEv) return bEv - aEv
    return 0
  })
}

/**
 * Resolve layout preference between cards and table view:
 * param ('cards' | 'table') wins, then stored preference, then mobile defaults to cards and desktop to table.
 */
export function resolveLayout(input: {
  param: string | null
  stored: string | null
  isMobile: boolean
}): 'cards' | 'table' {
  if (input.param === 'cards' || input.param === 'table') return input.param
  if (input.stored === 'cards' || input.stored === 'table') return input.stored
  return input.isMobile ? 'cards' : 'table'
}

/**
 * Cockpit inspector deep link URL.
 */
export function cockpitHref(r: BoardRow): string {
  return `/edge?sport=${r.sport}&prop=${encodeURIComponent(`proj|${r.sport}|${r.key}`)}`
}
