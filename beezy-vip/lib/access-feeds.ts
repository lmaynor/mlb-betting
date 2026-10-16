/**
 * The access policy (lib/access.ts) applied to the two multi-sport feeds: the +EV Terminal feed and the /edge cockpit rows.
 * Cut on the SERVER, in the pages and in the public proxies they refresh from, so withheld rows are never in a payload.
 *
 * Only LIVE rows are cut. Settled and past +EV rows are the public paper track record and always pass through.
 */
import { canViewFull, FREE_PREVIEW_ROWS, type Env, type Viewer } from '@/lib/access'
import type { CockpitRow, EvFeed } from '@/lib/types'

/** Total live +EV rows a non-Pro viewer sees across every sport: the best by EV. */
export function limitEvFeed(feed: EvFeed, viewer: Viewer, today: string, env?: Env): { feed: EvFeed; locked: number } {
  if (canViewFull(viewer, 'ev_terminal', 'mlb', env)) return { feed, locked: 0 }

  const live: { row: object; ev: number }[] = []
  for (const r of feed.mlb?.rows ?? []) if (r.result == null && r.game_date >= today) live.push({ row: r, ev: r.edge ?? -1 })
  for (const r of feed.nfl?.book_outliers ?? []) live.push({ row: r, ev: r.ev ?? -1 })
  for (const r of feed.nfl?.rows ?? []) live.push({ row: r, ev: r.net_ev ?? -1 })
  for (const r of feed.nhl?.rows ?? []) live.push({ row: r, ev: r.ev ?? -1 })
  for (const r of feed.nba?.rows ?? []) live.push({ row: r, ev: r.ev ?? -1 })

  const keep = new Set(live.slice().sort((a, b) => b.ev - a.ev).slice(0, FREE_PREVIEW_ROWS.ev_terminal).map(l => l.row))
  const liveSet = new Set(live.map(l => l.row))
  const cut = <T extends object>(rows: T[]): T[] => rows.filter(r => !liveSet.has(r) || keep.has(r))

  const mlbRows = cut(feed.mlb.rows)
  const out: EvFeed = {
    ...feed,
    mlb: { ...feed.mlb, rows: mlbRows, count: mlbRows.length },
    nfl: feed.nfl ? { ...feed.nfl, rows: cut(feed.nfl.rows), book_outliers: feed.nfl.book_outliers ? cut(feed.nfl.book_outliers) : feed.nfl.book_outliers } : feed.nfl,
    nhl: feed.nhl ? { ...feed.nhl, rows: cut(feed.nhl.rows) } : feed.nhl,
    nba: feed.nba ? { ...feed.nba, rows: cut(feed.nba.rows) } : feed.nba,
  }
  return { feed: out, locked: live.length - keep.size }
}

/**
 * Cockpit rows: a viewer's entitled sports are whole; every other sport shares one preview budget (the best by EV), so a
 * free viewer gets the top few rows overall and a single-sport pass gets its league plus the same small preview.
 */
export function limitCockpit(rows: CockpitRow[], viewer: Viewer, env?: Env): { rows: CockpitRow[]; locked: number } {
  const closed = rows.filter(r => !canViewFull(viewer, 'cockpit', r.sport, env))
  if (closed.length === 0) return { rows, locked: 0 }
  const keep = new Set(closed.slice().sort((a, b) => (b.ev ?? -1) - (a.ev ?? -1)).slice(0, FREE_PREVIEW_ROWS.cockpit))
  const closedSet = new Set(closed)
  return { rows: rows.filter(r => !closedSet.has(r) || keep.has(r)), locked: closed.length - keep.size }
}
