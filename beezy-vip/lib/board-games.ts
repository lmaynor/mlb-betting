/**
 * The games of a board, for the games strip (the matchup filter): every game with its two teams and start, plus how many
 * board rows each has. The feed lists every scheduled game (`games`) so a game with no rows yet is still there; when the
 * feed has none (an older payload, or MLB) the games come from the rows themselves. Pure, so it is testable without rendering.
 */
import { siteDateKey } from '@/lib/dates'
import type { BoardRow } from '@/lib/board-rows'
import type { SportSlug } from '@/lib/sports'
import type { SportProjections, TodaySlate } from '@/lib/types'

export interface BoardGame {
  key: string
  sport: SportSlug
  away: string
  home: string
  /** ISO start (NFL / NHL / NBA), or null */
  start: string | null
  /** a ready-made time label when the source has no ISO start (MLB's slate: '8:00 PM ET') */
  startText: string | null
  /** board rows in this game under the current day (0 = scheduled, nothing priced or projected yet) */
  count: number
}

const byStart = (a: BoardGame, b: BoardGame) => {
  if (a.start && b.start) return a.start < b.start ? -1 : a.start > b.start ? 1 : 0
  return a.start ? -1 : b.start ? 1 : 0
}

/** Games seen in the rows, one per game key (rows with no game key belong to no game). */
export function gamesFromRows(rows: BoardRow[]): BoardGame[] {
  const out = new Map<string, BoardGame>()
  for (const r of rows) {
    if (!r.gameKey || !r.away || !r.home) continue
    const g = out.get(r.gameKey)
    if (g) g.count += 1
    else out.set(r.gameKey, { key: r.gameKey, sport: r.sport, away: r.away, home: r.home, start: r.start, startText: null, count: 1 })
  }
  return [...out.values()].sort(byStart)
}

/** The feed's own game list merged with the rows' counts: scheduled games with no rows stay listed, with a count of 0. */
export function mergeGames(sport: SportSlug, feed: SportProjections['games'] | undefined, rows: BoardRow[]): BoardGame[] {
  const seen = gamesFromRows(rows)
  if (!feed || feed.length === 0) return seen
  const counts = new Map(seen.map(g => [g.key, g.count]))
  const out = feed.map(g => ({ key: g.game_key, sport, away: g.away, home: g.home, start: g.start, startText: null, count: counts.get(g.game_key) ?? 0 }))
  const known = new Set(out.map(g => g.key))
  return [...out, ...seen.filter(g => !known.has(g.key))].sort(byStart)
}

/** MLB's games for the day from the slate endpoint (every game, picks or not), keyed like the MLB board rows. */
export function gamesFromSlate(slate: TodaySlate | null | undefined, rows: BoardRow[]): BoardGame[] {
  const counts = new Map(gamesFromRows(rows).map(g => [g.key, g.count]))
  const out = (slate?.games ?? []).map(g => ({
    key: `mlb:${g.game_pk}`, sport: 'mlb' as const, away: g.away_team, home: g.home_team, start: null, startText: g.start_time,
    count: counts.get(`mlb:${g.game_pk}`) ?? 0,
  }))
  const known = new Set(out.map(g => g.key))
  return [...out, ...gamesFromRows(rows).filter(g => !known.has(g.key))]
}

/** '7:00 PM' for today's games, 'SUN 12:00 PM' for a later day: the label on a games-strip card. */
export function gameTimeLabel(g: Pick<BoardGame, 'start' | 'startText'>, today = siteDateKey()): string {
  if (!g.start) return g.startText ?? ''
  const d = new Date(g.start)
  if (Number.isNaN(d.getTime())) return g.startText ?? ''
  const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/Chicago' })
  const key = siteDateKey(d)
  if (key === today) return time
  const day = d.toLocaleDateString('en-US', { weekday: 'short', timeZone: 'America/Chicago' }).toUpperCase()
  return `${day} ${time}`
}

/** The same games with their row counts recomputed from `rows` (the rows the page actually shows, after its own view cut). */
export function withCounts(games: BoardGame[], rows: BoardRow[]): BoardGame[] {
  const counts = new Map(gamesFromRows(rows).map(g => [g.key, g.count]))
  return games.map(g => ({ ...g, count: counts.get(g.key) ?? 0 }))
}

/**
 * The instant a game starts: the feed's ISO start, or for MLB's slate ('7:05 PM ET', no date) that wall time on `today`'s
 * date in Eastern time. Null when neither is usable. Pure, so a client can turn it into the viewer's own clock.
 */
export function startInstant(g: Pick<BoardGame, 'start' | 'startText'>, today = siteDateKey()): Date | null {
  if (g.start) {
    const d = new Date(g.start)
    return Number.isNaN(d.getTime()) ? null : d
  }
  const m = /^(\d{1,2}):(\d{2})\s*(AM|PM)\s*ET$/i.exec(g.startText ?? '')
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(today)
  if (!m || !day) return null
  const hour = (Number(m[1]) % 12) + (m[3].toUpperCase() === 'PM' ? 12 : 0)
  const fmt = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', hourCycle: 'h23' })
  for (const offset of [4, 5]) {   // EDT, then EST: keep the offset under which Eastern wall time reads back as `hour`
    const cand = new Date(Date.UTC(Number(day[1]), Number(day[2]) - 1, Number(day[3]), hour + offset, Number(m[2])))
    if (Number(fmt.format(cand)) === hour) return cand
  }
  return null
}

/**
 * The start time on the viewer's own clock ('2:00 PM', or 'SUN 12:00 PM' when it falls on another local day). `timeZone`
 * defaults to the browser's; falls back to the server label when the start cannot be resolved.
 */
export function localTimeLabel(
  g: Pick<BoardGame, 'start' | 'startText'>, today = siteDateKey(), opts: { now?: Date; timeZone?: string } = {},
): string {
  const d = startInstant(g, today)
  if (!d) return g.startText ?? ''
  const tz = opts.timeZone ? { timeZone: opts.timeZone } : {}
  const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', ...tz })
  const day = (x: Date) => x.toLocaleDateString('en-CA', tz)
  if (day(d) === day(opts.now ?? new Date())) return time
  return `${d.toLocaleDateString('en-US', { weekday: 'short', ...tz }).toUpperCase()} ${time}`
}
