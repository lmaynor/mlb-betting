/**
 * The `notes` column of a winter (NFL / NBA / NHL) bet is a JSON object written by the backend
 * (mlb_core/alerts/sport_alerts.py for the 'EV' outlier alerts, ev/model_picks.py for the model's own 'PROPS' picks:
 * stat, point, side, n_books, tier, player_id, game_key, ...). MLB notes are a human sentence. UI that prints notes must go through
 * `readableNotes` so a JSON blob never reaches a user.
 */
import type { Bet } from '@/lib/types'

export type WinterSport = 'nfl' | 'nba' | 'nhl'
const WINTER = new Set<string>(['nfl', 'nba', 'nhl'])

export const isWinterSport = (sport: string | null | undefined): sport is WinterSport => WINTER.has((sport ?? '').toLowerCase())

/** The notes parsed as a JSON object, or null when they are empty, a sentence, or not an object. */
export function parseNotesJson(notes: string | null | undefined): Record<string, unknown> | null {
  const s = (notes ?? '').trim()
  if (!s.startsWith('{')) return null
  try {
    const v = JSON.parse(s)
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/** Notes safe to print: the text itself for an MLB sentence, null when the notes are machine JSON (or empty). */
export function readableNotes(notes: string | null | undefined): string | null {
  const s = (notes ?? '').trim()
  if (!s) return null
  if (s.startsWith('{') || s.startsWith('[')) return null
  return s
}

/** 'official' | 'tracked' for a model 'PROPS' pick (the backend's own gate, written into the notes); null for anything else. */
export function betTier(bet: Pick<Bet, 'notes'>): 'official' | 'tracked' | null {
  const t = parseNotesJson(bet.notes)?.tier
  return t === 'official' || t === 'tracked' ? t : null
}

/** Reference-book count a winter bet carries in its JSON notes; null when absent. */
export function notesBookCount(notes: string | null | undefined): number | null {
  const n = parseNotesJson(notes)?.n_books
  return typeof n === 'number' && Number.isFinite(n) ? n : null
}
