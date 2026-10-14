'use client'

import { useCallback, useSyncExternalStore } from 'react'

const KEY = 'beezy_bankroll'
const EVENT = 'beezy:bankroll'
const DEFAULT = 1000

function read(): number | null {
  try {
    const v = parseFloat(window.localStorage.getItem(KEY) ?? '')
    return isFinite(v) && v > 0 ? v : null
  } catch {
    return null
  }
}

function subscribe(cb: () => void) {
  window.addEventListener('storage', cb)
  window.addEventListener(EVENT, cb)
  return () => {
    window.removeEventListener('storage', cb)
    window.removeEventListener(EVENT, cb)
  }
}

// In-memory fallback when localStorage is unavailable (private window, blocked site data).
let memory: number | null = null

/** Bankroll shared by the nav pill, the +EV terminal and the Kelly tools (per-browser). */
export function useBankroll(fallback: number = DEFAULT): [number, (n: number) => void] {
  const bankroll = useSyncExternalStore(
    subscribe,
    () => read() ?? memory ?? fallback,
    () => fallback,
  )
  const set = useCallback((n: number) => {
    memory = n
    try { window.localStorage.setItem(KEY, String(n)) } catch { /* keep in-memory value */ }
    window.dispatchEvent(new Event(EVENT))
  }, [])
  return [bankroll, set]
}
