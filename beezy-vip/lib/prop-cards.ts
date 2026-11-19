import type { BoardRow } from './board-rows'
import type { SportSlug } from './sports'

export type CardSort = 'ev' | 'score' | 'time'

/**
 * Leagues whose projections board offers the cards/table switch (IA-08). NBA at launch; NHL added
 * 2026-11-16 at Luc's direction — its feed carries the same percentiles, blend and book counts the
 * card consumes (verified against the live payload), so enablement is a board switch, not a feed
 * change. NFL added the same day, also at Luc's direction: its yardage/count families carry the
 * same fields, and the card gate's projection-range rule (below) keeps the anytime family, which
 * carries no percentiles by design, table-only. Every other league keeps the table only.
 */
export const CARD_LAYOUT_SPORTS: readonly SportSlug[] = ['nba', 'nhl', 'nfl']

export function hasCardLayout(sport: SportSlug): boolean {
  return CARD_LAYOUT_SPORTS.includes(sport)
}

/**
 * Filter board rows down to those suitable for card presentation:
 * must have a posted line, odds, book, at least 3 books, either positive market EV or score >= 35,
 * and a projection range (p10 and p90). The range is a uniform admission rule, not an NFL carve-out:
 * the card's evidence is the distribution strip, so a card without a range is not a card. It is a
 * proven no-op for NBA and NHL — measured against the live payloads on 2026-11-16, the gate with
 * and without the range rule admits identical sets (NBA 60 = 60, NHL 54 = 54), because their lined
 * rows carry percentiles universally. For NFL it is what keeps anytime_td (proj_range returns no
 * percentiles for the anytime family, by design) in the table where it belongs.
 * Input order is preserved.
 */
export function cardGate(rows: BoardRow[]): BoardRow[] {
  return rows.filter(
    r =>
      r.hasLine &&
      r.odds != null &&
      r.book != null &&
      (r.nBooks ?? 0) >= 3 &&
      ((r.marketEv ?? 0) > 0 || r.score >= 35) &&
      r.p10 != null &&
      r.p90 != null
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

export const PROP_DISP_MAX = 0.06

export function propCardStatus(r: BoardRow): 'OFFICIAL PLAY' | 'CANDIDATE · CONFLUENCE' | 'MODEL PROJECTION' {
  if (r.tier === 'official') return 'OFFICIAL PLAY'
  if (r.confluence) return 'CANDIDATE · CONFLUENCE'
  return 'MODEL PROJECTION'
}

export function evBadgeHighlighted(r: BoardRow): boolean {
  return (r.marketEv ?? 0) > 0 && r.disp != null && r.disp <= PROP_DISP_MAX
}
