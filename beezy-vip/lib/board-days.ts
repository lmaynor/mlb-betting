/**
 * Game days for a projection board. The live feed returns a rolling window of games (a week of NFL, three days of NHL, a week of NBA),
 * so the board must not pour all of it onto the TODAY tab: each row belongs to the day its game is played, and a day chip lets the
 * viewer step through the upcoming days. The day of a game is the one in its game key ('nba:2026-10-17:BOS@LAL', the feed's own game
 * day); when a row has no key date, its start time in the site's time zone. Pure, so it is testable without rendering.
 */
import { formatDateKey, siteDateKey } from '@/lib/dates'

export interface BoardDay {
  /** the game day, YYYY-MM-DD */
  date: string
  /** 'SUN OCT 18' */
  label: string
  /** board rows on that day */
  count: number
}

type Dated = { game_key?: string | null; commence?: string | null }

const KEY_DATE = /:(\d{4}-\d{2}-\d{2}):/
export const isDateKey = (s: string | undefined | null): s is string => !!s && /^\d{4}-\d{2}-\d{2}$/.test(s)

/** The day a row or game is played, or null when neither its key nor its start says. */
export function gameDayKey(x: Dated): string | null {
  const m = KEY_DATE.exec(x.game_key ?? '')
  if (m) return m[1]
  if (x.commence) {
    const d = new Date(x.commence)
    if (!Number.isNaN(d.getTime())) return siteDateKey(d)
  }
  return null
}

const dayLabel = (date: string) => formatDateKey(date, { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase().replace(',', '')

/** Days after `today` that have rows, soonest first (at most `max`). */
export function upcomingDays(rows: Dated[], today: string, max = 7): BoardDay[] {
  const counts = new Map<string, number>()
  for (const r of rows) {
    const d = gameDayKey(r)
    if (d && d > today) counts.set(d, (counts.get(d) ?? 0) + 1)
  }
  return [...counts.entries()].sort(([a], [b]) => (a < b ? -1 : 1)).slice(0, max).map(([date, count]) => ({ date, label: dayLabel(date), count }))
}

/**
 * The day the board shows: the requested upcoming day when it is valid (today or later), else today, and when nothing is
 * scheduled today but a later day has games (an NFL Tuesday), the next day with games, so the tab is never empty by default.
 */
export function viewDay(rows: Dated[], today: string, requested?: string): string {
  if (isDateKey(requested) && requested >= today) return requested
  if (rows.some(r => (gameDayKey(r) ?? today) === today)) return today
  return upcomingDays(rows, today, 1)[0]?.date ?? today
}

/** The rows of one day. A row with no day at all counts as today's, so nothing is silently dropped. */
export function onDay<T extends Dated>(rows: T[], date: string, today: string): T[] {
  return rows.filter(r => (gameDayKey(r) ?? today) === date)
}

/** The rows of the day the board opens on (today, or the next day with games): for counts that say "today". */
export function slateRows<T extends Dated>(rows: T[], today = siteDateKey()): T[] {
  return onDay(rows, viewDay(rows, today), today)
}
