/**
 * Days on a projection board. The MLB card has a day scroller (DateBar); the projection sports (NFL / NHL / NBA) now have one
 * too, built from the days that actually have games in the feed, in the site's time zone (Central), the same clock the MLB
 * bar uses. Pure, so the choice of day is testable without rendering.
 *
 * URL: `?date=YYYY-MM-DD` picks a day, `?date=all` shows every day, no param shows the first day with games.
 */
import { siteDateKey } from '@/lib/dates'

export interface BoardDay { key: string; count: number }

const DAY_KEY = /^\d{4}-\d{2}-\d{2}$/

/** The site-clock date key of an ISO game start, or null when there is no usable start. */
export function dayKeyOf(iso: string | null | undefined): string | null {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : siteDateKey(d)
}

/** Distinct game days with a row count each, soonest first. Rows with no start time belong to no day. */
export function boardDays(starts: (string | null | undefined)[]): BoardDay[] {
  const counts = new Map<string, number>()
  for (const s of starts) {
    const k = dayKeyOf(s)
    if (k) counts.set(k, (counts.get(k) ?? 0) + 1)
  }
  return [...counts.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([key, count]) => ({ key, count }))
}

/** The selected day for a `date` param: 'all', a day that has rows, or the first day with rows (also the answer for a stale date). */
export function chooseDay(param: string | undefined, days: BoardDay[]): string {
  if (days.length === 0) return 'all'
  if (param === 'all') return 'all'
  if (param && DAY_KEY.test(param) && days.some(d => d.key === param)) return param
  return days[0].key
}

/** Does a game start fall on the selected day? 'all' keeps everything, including rows with no start time. */
export function inDay(iso: string | null | undefined, selected: string): boolean {
  return selected === 'all' || dayKeyOf(iso) === selected
}

/**
 * One call for a page: the day strip's days (from ALL rows, so every game day stays selectable whatever the other filters
 * are), the selected day, and the rows that fall on it.
 */
export function sliceByDay<T>(rows: T[], startOf: (r: T) => string | null | undefined, param: string | undefined): { days: BoardDay[]; selected: string; rows: T[] } {
  const days = boardDays(rows.map(startOf))
  const selected = chooseDay(param, days)
  return { days, selected, rows: selected === 'all' ? rows : rows.filter(r => inDay(startOf(r), selected)) }
}
