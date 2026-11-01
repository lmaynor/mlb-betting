/**
 * The access policy (lib/access.ts) applied to the two multi-sport feeds: the +EV Terminal feed and the /edge cockpit rows.
 * Cut on the SERVER, in the pages and in the public proxies they refresh from, so withheld rows are never in a payload.
 *
 * Only LIVE rows are cut. Settled and past +EV rows are the public paper track record and always pass through.
 */
import { canViewFull, FREE_PREVIEW_ROWS, type Env, type Viewer } from '@/lib/access'
import type { CockpitRow, EvFeed } from '@/lib/types'
import type { OddsBoard } from '@/lib/odds-screen'
import type { GameProjectionsFeed } from '@/lib/game-lines'
import type { GameOddsFeed } from '@/lib/game-odds'
import { isLeague } from '@/lib/tiers'

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

/**
 * The Odds Terminal (/ev) is Pro only: everyone else gets the
 * FREE_PREVIEW_ROWS.ev_terminal best-covered lines (most books quoting) per sport. `locked` is how many were withheld.
 */
export function limitOddsBoard(board: OddsBoard, viewer: Viewer, env?: Env): { board: OddsBoard; locked: number } {
  const sport = isLeague(board.sport) ? board.sport : 'nfl'
  if (canViewFull(viewer, 'ev_terminal', sport, env)) return { board, locked: 0 }
  const keep = board.rows.slice().sort((a, b) => b.books.length - a.books.length).slice(0, FREE_PREVIEW_ROWS.ev_terminal)
  const kept = new Set(keep)
  const rows = board.rows.filter(r => kept.has(r))
  return { board: { ...board, rows, count: rows.length }, locked: board.rows.length - rows.length }
}

/**
 * Game Lines (model projections for NBA / NHL games) sit on the Odds Terminal, so they follow the Terminal's rule: Pro
 * sees everything, everyone else gets the same small preview as the props board (the FREE_PREVIEW_ROWS.ev_terminal
 * best-covered lines, most books quoting), and the priced games and matchup list are cut to those rows so no withheld
 * game rides along in `priced_games` or `games`. `locked` is how many feed rows were withheld.
 */
export function limitGameLines(feed: GameProjectionsFeed, viewer: Viewer, env?: Env): { feed: GameProjectionsFeed; locked: number } {
  const sport = isLeague(feed.sport) ? feed.sport : 'nfl'
  if (canViewFull(viewer, 'ev_terminal', sport, env)) return { feed, locked: 0 }

  const rows = feed.rows ?? []
  const keep = rows.slice().sort((a, b) => (b.n_books ?? 0) - (a.n_books ?? 0)).slice(0, FREE_PREVIEW_ROWS.ev_terminal)
  const kept = new Set(keep)
  const keptRows = rows.filter(r => kept.has(r))
  const keyOf = (game: string, system: string, side: string, line: number | null) => `${game}|${system}|${side}|${line ?? ''}`
  const keptKeys = new Set(keptRows.map(r => keyOf(r.game_key, r.system, r.side, r.line)))
  const keptGames = new Set(keptRows.map(r => r.game_key))

  const pricedGames = (feed.priced_games ?? [])
    .map(g => ({ ...g, markets: (g.markets ?? []).filter(m => keptKeys.has(keyOf(g.game_key, m.system, m.side, m.line))) }))
    .filter(g => g.markets.length > 0)
  const out: GameProjectionsFeed = {
    ...feed,
    rows: keptRows,
    count: keptRows.length,
    priced_games: pricedGames,
    games: (feed.games ?? []).filter(g => keptGames.has(g.game_key)),
  }
  return { feed: out, locked: rows.length - keptRows.length }
}

/**
 * Game-line PRICES (the /ev/games price screen) sit on the Odds Terminal too: Pro sees the whole feed, everyone else the
 * FREE_PREVIEW_ROWS.ev_terminal best-covered rows (most books quoting), and `games` is cut to the games those rows belong
 * to (with `n_rows` recounted) so no withheld game rides along. `locked` is how many feed rows were withheld.
 */
export function limitGameOdds(feed: GameOddsFeed, viewer: Viewer, env?: Env): { feed: GameOddsFeed; locked: number } {
  const sport = isLeague(feed.sport) ? feed.sport : 'nfl'
  if (canViewFull(viewer, 'ev_terminal', sport, env)) return { feed, locked: 0 }

  const rows = feed.rows ?? []
  const keep = rows.slice().sort((a, b) => (b.books?.length ?? 0) - (a.books?.length ?? 0)).slice(0, FREE_PREVIEW_ROWS.ev_terminal)
  const kept = new Set(keep)
  const keptRows = rows.filter(r => kept.has(r))
  const perGame = new Map<string, number>()
  for (const r of keptRows) perGame.set(r.game_key, (perGame.get(r.game_key) ?? 0) + 1)
  const out: GameOddsFeed = {
    ...feed,
    rows: keptRows,
    count: keptRows.length,
    games: (feed.games ?? []).filter(g => perGame.has(g.game_key)).map(g => ({ ...g, n_rows: perGame.get(g.game_key) ?? 0 })),
  }
  return { feed: out, locked: rows.length - keptRows.length }
}
