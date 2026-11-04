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

/**
 * Pinnacle is offshore (never a place to bet) but it is the sharpest reference price there is, so it stays on every board as a
 * reference. Every OTHER offshore book (Bovada, BetOnline, MyBookie, ...) is junk: it is dropped from the feeds at the edge, so it
 * is never a column, a dot, a best price or a fair-value input. The backend drops the same books at the source (ev/props.py).
 */
export const SHARP_REFERENCE_BOOKS: ReadonlySet<string> = new Set(['pinnacle'])

export const isJunkBook = (book: string | null | undefined): boolean => isOffshore(book) && !SHARP_REFERENCE_BOOKS.has(book!.trim().toLowerCase())

interface BookFeed { rows: { books: { book: string }[] }[]; books?: string[]; offshore_books?: string[] }

/**
 * A board / game feed with every junk offshore book removed from each row's quotes and from the `books` / `offshore_books` lists.
 * Rows left with no quote are dropped. A feed with nothing to remove is returned as the SAME object, so memoised consumers
 * do not recompute.
 */
export function suppressJunkBooks<T extends BookFeed>(feed: T): T {
  if (!feed || !Array.isArray(feed.rows)) return feed
  const dirty = feed.rows.some(r => Array.isArray(r?.books) && r.books.some(b => isJunkBook(b.book)))
    || (feed.books ?? []).some(isJunkBook) || (feed.offshore_books ?? []).some(isJunkBook)
  if (!dirty) return feed
  const rows = feed.rows
    .map(r => (Array.isArray(r?.books) ? { ...r, books: r.books.filter(b => !isJunkBook(b.book)) } : r))
    .filter(r => !Array.isArray(r?.books) || r.books.length > 0)
  const out: T = { ...feed, rows }
  if (feed.books) out.books = feed.books.filter(b => !isJunkBook(b))
  if (feed.offshore_books) out.offshore_books = feed.offshore_books.filter(b => !isJunkBook(b))
  return out
}
