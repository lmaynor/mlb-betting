import type { Bet } from '@/lib/types'

export type CollapsedBet = Bet & { bookCount: number }

/**
 * Stable identity of the underlying prop, independent of which book it was flagged at.
 * EV rows store bet_type as "<underlying>_<book>" (e.g. K_OVER_5.5_betmgm); strip that suffix.
 */
export function propKey(b: Bet): string {
  let bt = b.bet_type ?? ''
  const book = (b.book ?? '').toLowerCase()
  if (book && bt.toLowerCase().endsWith('_' + book)) bt = bt.slice(0, bt.length - book.length - 1)
  return [b.game_date, b.game_pk, (b.player ?? '').toLowerCase(), bt].join('|')
}

/**
 * One row per prop: the same prop flagged at several books collapses to the best-edge row,
 * carrying the number of books it was flagged at. Input order is otherwise preserved.
 */
export function collapseBooks(bets: Bet[]): CollapsedBet[] {
  const best = new Map<string, CollapsedBet>()
  const order: string[] = []
  for (const b of bets) {
    const k = propKey(b)
    const cur = best.get(k)
    if (!cur) {
      best.set(k, { ...b, bookCount: 1 })
      order.push(k)
    } else {
      cur.bookCount += 1
      if ((b.edge ?? -Infinity) > (cur.edge ?? -Infinity)) {
        const n = cur.bookCount
        best.set(k, { ...b, bookCount: n })
      }
    }
  }
  return order.map(k => best.get(k)!)
}
