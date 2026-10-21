/**
 * Offshore books are never shown as a place to bet. The backend drops them too (ev/props.py OFFSHORE_BOOKS); this is the same
 * list applied at the edge so older stored boards and a not-yet-redeployed API cannot put one on screen.
 */
export const OFFSHORE_BOOKS: ReadonlySet<string> = new Set([
  'bovada', 'betfair', 'pinnacle', 'betonline', 'betonlineag', 'mybookie', 'bookmakereu', 'heritagesports', 'lowvig',
  'betanysports', 'sportsbetting', 'betus', 'matchbook',
])

export const isOffshore = (book: string | null | undefined): boolean => !!book && OFFSHORE_BOOKS.has(book.trim().toLowerCase())

/** Drop offshore quotes from a per-book list. */
export const onshoreQuotes = <T extends { book: string }>(quotes: T[] | null | undefined): T[] => (quotes ?? []).filter(q => !isOffshore(q.book))
