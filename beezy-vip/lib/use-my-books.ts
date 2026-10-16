'use client'

import { useCallback, useSyncExternalStore } from 'react'

/**
 * "My books": the sportsbooks (and exchanges) a member can actually bet at in their state, kept per browser and used to
 * filter the +EV terminal. An EMPTY list means no filter (every venue shows). Stored as a JSON array of lowercase book
 * keys under `beezy_my_books`.
 */
export const MY_BOOKS_KEY = 'beezy_my_books'
const EVENT = 'beezy:my-books'

/** Parse whatever is in storage into a clean, de-duplicated list of lowercase keys (garbage -> []). */
export function parseMyBooks(raw: string | null | undefined): string[] {
  if (!raw) return []
  try {
    const v: unknown = JSON.parse(raw)
    if (!Array.isArray(v)) return []
    return [...new Set(v.filter((x): x is string => typeof x === 'string').map(x => x.trim().toLowerCase()).filter(Boolean))]
  } catch {
    return []
  }
}

/** Row passes when no books are selected, or its book is one of them. */
export function matchesMyBooks(bookKey: string, mine: readonly string[]): boolean {
  return mine.length === 0 || mine.includes((bookKey ?? '').toLowerCase())
}

export function toggleBook(mine: readonly string[], book: string): string[] {
  const k = book.toLowerCase()
  return mine.includes(k) ? mine.filter(b => b !== k) : [...mine, k]
}

// Snapshot cache: useSyncExternalStore needs a referentially stable value between changes.
let memory: string[] | null = null
let cachedRaw: string | null | undefined
let cachedList: string[] = []
const EMPTY: string[] = []

function readRaw(): string | null {
  try { return window.localStorage.getItem(MY_BOOKS_KEY) } catch { return null }
}

function snapshot(): string[] {
  const raw = readRaw()
  if (raw === null && memory) return memory            // storage blocked or empty: fall back to this tab's in-memory choice
  if (raw !== cachedRaw) { cachedRaw = raw; cachedList = parseMyBooks(raw) }
  return cachedList
}

function subscribe(cb: () => void) {
  window.addEventListener('storage', cb)
  window.addEventListener(EVENT, cb)
  return () => { window.removeEventListener('storage', cb); window.removeEventListener(EVENT, cb) }
}

export function useMyBooks(): { books: string[]; enabled: boolean; toggle: (book: string) => void; clear: () => void } {
  const books = useSyncExternalStore(subscribe, snapshot, () => EMPTY)
  const write = useCallback((next: string[]) => {
    memory = next
    try {
      if (next.length) window.localStorage.setItem(MY_BOOKS_KEY, JSON.stringify(next))
      else window.localStorage.removeItem(MY_BOOKS_KEY)
    } catch { /* keep the in-memory value */ }
    window.dispatchEvent(new Event(EVENT))
  }, [])
  const toggle = useCallback((book: string) => write(toggleBook(snapshot(), book)), [write])
  const clear = useCallback(() => { memory = null; write([]) }, [write])
  return { books, enabled: books.length > 0, toggle, clear }
}
