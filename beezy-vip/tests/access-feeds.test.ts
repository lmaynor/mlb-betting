import { limitCockpit, limitEvFeed, limitGameLines } from '../lib/access-feeds'
import type { GameProjectionRow, GameProjectionsFeed } from '../lib/game-lines'
import { canViewFull, FREE_PREVIEW_ROWS, type Viewer } from '../lib/access'
import type { CockpitRow, EvFeed } from '../lib/types'

const ON = { ENFORCE_TIERS: '1' }
const TODAY = '2026-10-16'
const free: Viewer = { tier: 'public', sport: null }
const nbaPass: Viewer = { tier: 'single_sport', sport: 'nba' }
const pro: Viewer = { tier: 'pro_monthly', sport: null }

const mlb = (id: number, edge: number, over: Partial<{ result: string | null; game_date: string }> = {}) =>
  ({ id, edge, result: null, game_date: TODAY, ...over })
const out = (player: string, ev: number) => ({ player, ev, game_key: 'x:d:A@B' })

const feed = (): EvFeed => ({
  sport: 'all', min_ev: 0.02, as_of: '',
  mlb: { rows: [mlb(1, 0.09), mlb(2, 0.02), mlb(3, 0.5, { result: 'win', game_date: '2026-10-10' }), mlb(4, 0.03, { game_date: '2026-10-12' })], count: 4 },
  nfl: { rows: [{ ticker: 't1', net_ev: 0.08 }], book_outliers: [out('Nfl A', 0.07), out('Nfl B', 0.01)], count: 3, status: 'shadow_live', as_of: null, age_minutes: 1, stale: false, summary: {}, validated: false },
  nhl: { rows: [out('Nhl A', 0.06)], count: 1, status: 'shadow_live', as_of: null, age_minutes: 1, stale: false, summary: {}, validated: false },
  nba: { rows: [out('Nba A', 0.05), out('Nba B', 0.04)], count: 2, status: 'shadow_live', as_of: null, age_minutes: 1, stale: false, summary: {}, validated: false },
} as unknown as EvFeed)

describe('limitEvFeed', () => {
  it('the +EV terminal is Pro only: a single-sport pass is cut like a free viewer', () => {
    expect(canViewFull(pro, 'ev_terminal', 'mlb', ON)).toBe(true)
    expect(canViewFull(nbaPass, 'ev_terminal', 'nba', ON)).toBe(false)
    expect(canViewFull(free, 'ev_terminal', 'mlb', ON)).toBe(false)
  })

  it('keeps only the best live rows across every sport, and never cuts the settled or past record', () => {
    const f = feed()
    const { feed: cut, locked } = limitEvFeed(f, free, TODAY, ON)
    // live rows: mlb 1 (0.09), nfl kalshi (0.08), nfl A (0.07), nhl A (0.06), nba A (0.05) are the best five
    const kept = [
      ...cut.mlb.rows.filter(r => r.result == null && r.game_date >= TODAY).map(r => `mlb${r.id}`),
      ...(cut.nfl?.rows ?? []).map(() => 'nfl-kalshi'),
      ...(cut.nfl?.book_outliers ?? []).map(r => r.player),
      ...(cut.nhl?.rows ?? []).map(r => r.player),
      ...(cut.nba?.rows ?? []).map(r => r.player),
    ]
    expect(kept.sort()).toEqual(['Nba A', 'Nfl A', 'Nhl A', 'mlb1', 'nfl-kalshi'].sort())
    expect(kept).toHaveLength(FREE_PREVIEW_ROWS.ev_terminal)
    // live: mlb1, mlb2 (mlb4 is a past-dated pending row, not live); nfl 1 + 2; nhl 1; nba 2 -> 8 live, 5 kept
    expect(locked).toBe(8 - FREE_PREVIEW_ROWS.ev_terminal)
    // the settled row and the past-date row are public record and survive untouched
    expect(cut.mlb.rows.map(r => r.id)).toEqual(expect.arrayContaining([3, 4]))
    expect(cut.mlb.count).toBe(cut.mlb.rows.length)
  })

  it('leaves Pro and a pre-launch site untouched', () => {
    const f = feed()
    expect(limitEvFeed(f, pro, TODAY, ON).feed).toBe(f)
    expect(limitEvFeed(f, free, TODAY, {}).locked).toBe(0)
  })

  it('does not mutate the feed it was given', () => {
    const f = feed()
    limitEvFeed(f, free, TODAY, ON)
    expect(f.mlb.rows).toHaveLength(4)
    expect(f.nba!.rows).toHaveLength(2)
  })
})

const row = (sport: CockpitRow['sport'], id: string, ev: number): CockpitRow => ({ sport, id, ev } as unknown as CockpitRow)

describe('limitCockpit', () => {
  const rows = [
    ...Array.from({ length: 4 }, (_, i) => row('nba', `nba${i}`, 0.05 + i * 0.01)),
    ...Array.from({ length: 4 }, (_, i) => row('nfl', `nfl${i}`, 0.04 + i * 0.01)),
    ...Array.from({ length: 4 }, (_, i) => row('mlb', `mlb${i}`, 0.03 + i * 0.01)),
  ]

  it('a free viewer gets one shared preview of the best rows overall', () => {
    const { rows: kept, locked } = limitCockpit(rows, free, ON)
    expect(kept).toHaveLength(FREE_PREVIEW_ROWS.cockpit)
    expect(locked).toBe(rows.length - FREE_PREVIEW_ROWS.cockpit)
    expect(Math.min(...kept.map(r => r.ev!))).toBeGreaterThanOrEqual(Math.max(...rows.filter(r => !kept.includes(r)).map(r => r.ev!)))
  })

  it('a single-sport pass sees its own league whole plus the same small preview of the rest', () => {
    const { rows: kept } = limitCockpit(rows, nbaPass, ON)
    expect(kept.filter(r => r.sport === 'nba')).toHaveLength(4)
    expect(kept.filter(r => r.sport !== 'nba')).toHaveLength(FREE_PREVIEW_ROWS.cockpit)
  })

  it('all-access and pre-launch see everything, in order', () => {
    expect(limitCockpit(rows, pro, ON).rows).toBe(rows)
    expect(limitCockpit(rows, free, {}).rows).toBe(rows)
  })
})

describe('limitGameLines (Game Lines follow the Odds Terminal rule)', () => {
  const mkRow = (i: number, nBooks: number, side = 'home'): GameProjectionRow => ({
    sport: 'nba', system: 'NBA_SPREAD', stat: 'spread', stat_label: 'Spread', player: `A${i} @ B${i}`, player_norm: `a${i}`, game_key: `nba:d:A${i}@B${i}`,
    commence: '2026-10-31T23:00:00Z', side, line: -3.5, has_line: true, proj_mean: -4, p_model_side: 0.55, p_mkt_side: 0.5, n_books: nBooks,
    model_edge: 0.05, market_ev: 0.04, confluence: false, beezy_score: 50, push_prob: 0.02,
  })
  const gameFeed = (): GameProjectionsFeed => {
    const rows = Array.from({ length: 9 }, (_, i) => mkRow(i, i + 1))
    return {
      sport: 'nba', status: 'live', engine: 'e', as_of: null, age_minutes: 1, stale: false, count: 9, rows, markets: [],
      priced_games: rows.map(r => ({
        game_key: r.game_key, home_team: 'B', away_team: 'A', game_date: 'd',
        markets: [{ system: 'NBA_SPREAD', market_label: 'Spread', side: 'home', line: -3.5, p_model: 0.55, push_prob: 0, p_fair: 0.5, best_odds: -110, best_book: 'x', n_books: r.n_books, model_edge: 0.05, market_ev: 0.04, confluence: false, beezy_score: 50 }],
      })),
      games: rows.map(r => ({ game_key: r.game_key, away: 'A', home: 'B', start: 's' })),
    }
  }

  it('Pro sees the whole feed; so does everyone before launch / with tiers not enforced', () => {
    const f = gameFeed()
    expect(limitGameLines(f, pro, ON)).toEqual({ feed: f, locked: 0 })
    expect(limitGameLines(f, free, {})).toEqual({ feed: f, locked: 0 })
  })

  it('a free viewer and a single-sport pass get the same small preview as the props board: the best-covered rows', () => {
    for (const viewer of [free, nbaPass]) {
      const { feed: cut, locked } = limitGameLines(gameFeed(), viewer, ON)
      expect(cut.rows).toHaveLength(FREE_PREVIEW_ROWS.ev_terminal)
      expect(cut.rows.map(r => r.n_books)).toEqual(expect.arrayContaining([9, 8, 7, 6, 5]))
      expect(Math.min(...cut.rows.map(r => r.n_books ?? 0))).toBe(5)
      expect(cut.count).toBe(FREE_PREVIEW_ROWS.ev_terminal)
      expect(locked).toBe(9 - FREE_PREVIEW_ROWS.ev_terminal)
    }
  })

  it('no withheld game rides along in priced_games or games', () => {
    const { feed: cut } = limitGameLines(gameFeed(), free, ON)
    const keptKeys = new Set(cut.rows.map(r => r.game_key))
    expect(cut.priced_games).toHaveLength(keptKeys.size)
    expect(cut.priced_games.every(g => keptKeys.has(g.game_key))).toBe(true)
    expect(cut.games.every(g => keptKeys.has(g.game_key))).toBe(true)
    expect(JSON.stringify(cut)).not.toContain('nba:d:A0@B0')
  })

  it('drops a priced market whose row was withheld even inside a kept game', () => {
    const f = gameFeed()
    const kept = f.rows[8]
    f.rows.push({ ...mkRow(8, 1, 'away'), side: 'away' })
    f.priced_games[8].markets.push({ ...f.priced_games[8].markets[0], side: 'away', model_edge: 0.99 })
    const { feed: cut } = limitGameLines(f, free, ON)
    const game = cut.priced_games.find(g => g.game_key === kept.game_key)!
    expect(game.markets.map(m => m.side)).toEqual(['home'])
  })

  it('does not mutate the feed it was given', () => {
    const f = gameFeed()
    limitGameLines(f, free, ON)
    expect(f.rows).toHaveLength(9)
    expect(f.priced_games).toHaveLength(9)
  })
})
