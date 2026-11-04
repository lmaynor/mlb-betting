/**
 * The Prop Research player list. The outlier sweep (props-alerts, twice a day) only emits a row when a book hangs a price well off
 * the market, so on most days the cockpit it feeds is empty and the player inspector (shot chart, trends, form) cannot be opened.
 * The daily projections board has a priced line for every player x stat (1,000+ rows a sport), so the list is built from that board:
 * every row with a line is a candidate, and a sweep row for the same player and stat replaces it and keeps its `flag`
 * (the card shows a Flagged badge and flagged rows sort first). Pure functions: the loader lives in lib/cockpit-loader.ts.
 */
import { americanToDecimal } from '@/lib/ev-math'
import type { ProjectionSport } from '@/lib/sports'
import type { CockpitFeed, CockpitRow, CockpitSportMeta, SportProjectionRow, SportProjections } from '@/lib/types'

/** rows kept per league: the list renders every card, and search still finds anyone inside this window */
export const PLAYERS_PER_SPORT = 200

export function foldPlayer(s: string | null | undefined): string {
  return (s ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim()
}

const keyOf = (sport: string, player: string | null | undefined, stat: string | null | undefined) => `${sport}|${foldPlayer(player)}|${(stat ?? '').toLowerCase()}`

/** 'nba:2026-11-03:BOS@MIA' -> 'BOS @ MIA' */
function matchupOf(gameKey: string | null | undefined, team: string | null, opp: string | null): string | null {
  const m = /:([A-Za-z.]+)@([A-Za-z.]+)$/.exec(gameKey ?? '')
  if (m) return `${m[1]} @ ${m[2]}`
  return team && opp ? `${team} vs ${opp}` : null
}

/** One projection-board row as a cockpit row: model source, unvalidated, not flagged (only the sweep flags). */
export function projectionToCockpitRow(r: SportProjectionRow): CockpitRow | null {
  const line = r.consensus_line ?? r.line
  if (!r.player || line == null) return null
  const side = r.side === 'under' ? 'UNDER' : 'OVER'
  const american = side === 'UNDER' ? r.best_under_odds : r.best_over_odds
  const book = side === 'UNDER' ? r.best_under_book : r.best_over_book
  return {
    id: `proj|${r.sport}|${r.game_key}|${r.player_norm}|${r.stat}`,
    sport: r.sport,
    source: 'model',
    shadow: true,
    player: r.player,
    player_id: null,
    headshot_id: r.headshot_id ?? null,
    team: r.team,
    matchup: matchupOf(r.game_key, r.team, r.opp),
    commence: r.commence,
    mins_to_kick: r.mins_to_start,
    stat: r.stat,
    side,
    line,
    book,
    american,
    decimal: american != null ? americanToDecimal(american) : null,
    // a price off two books, or one the backend capped, is not a credible EV: show none rather than a +60% longshot at the top
    ev: !r.edge_capped && (r.n_books ?? 0) >= 3 ? r.market_ev : null,
    p_fair: r.p_mkt_side ?? r.p_over_market,
    n_books: r.n_books,
    flag: false,
    reason: null,
    all_books: null,
    recentForm: null,
  }
}

/** Highest Beezy Score first, then the larger model-vs-market gap: the order the window of PLAYERS_PER_SPORT rows is cut in. */
function byStrength(a: SportProjectionRow, b: SportProjectionRow): number {
  return (b.beezy_score ?? 0) - (a.beezy_score ?? 0) || Math.abs(b.model_edge ?? 0) - Math.abs(a.model_edge ?? 0)
}

/**
 * `feed` is the sweep cockpit (null when the API is down), `boards` the projection boards by league. The result keeps the sweep's
 * rows as they are (flag, every book's quote, form), adds the best-scored board rows the sweep did not cover, and rewrites each
 * league's status line from the board, since the board (minutes old) is what the list now shows, not the sweep file (hours old).
 */
export function withProjectionPlayers(
  feed: CockpitFeed | null,
  boards: Partial<Record<ProjectionSport, SportProjections | null>>,
  now = new Date(),
): CockpitFeed | null {
  const loaded = (Object.entries(boards) as [ProjectionSport, SportProjections | null | undefined][]).filter(([, b]) => b && b.rows?.length)
  if (loaded.length === 0) return feed
  const sweep = feed?.rows ?? []
  const seen = new Set(sweep.map(r => keyOf(r.sport, r.player, r.stat)))
  const added: CockpitRow[] = []
  const sports: CockpitFeed['sports'] = { ...(feed?.sports ?? {}) }
  for (const [sport, board] of loaded) {
    const rows = board!.rows.slice().sort(byStrength)
    let n = 0
    for (const pr of rows) {
      if (n >= PLAYERS_PER_SPORT) break
      const k = keyOf(sport, pr.player, pr.stat)
      if (seen.has(k)) continue
      const row = projectionToCockpitRow(pr)
      if (!row) continue
      seen.add(k)
      added.push(row)
      n++
    }
    const meta: CockpitSportMeta = {
      status: board!.status === 'live' ? 'live' : board!.status,
      count: sweep.filter(r => r.sport === sport).length + n,
      as_of: board!.as_of,
      age_minutes: board!.age_minutes,
      stale: board!.stale,
      validated: false,
    }
    sports[sport] = meta
  }
  return {
    as_of: feed?.as_of ?? now.toISOString(),
    sport: feed?.sport ?? 'all',
    min_ev: feed?.min_ev ?? 0,
    rows: [...sweep, ...added],
    sports,
    ...(feed?.locked != null ? { locked: feed.locked } : {}),
  }
}
