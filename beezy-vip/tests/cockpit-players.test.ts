import { PLAYERS_PER_SPORT, projectionToCockpitRow, withProjectionPlayers } from '@/lib/cockpit-players'
import type { CockpitFeed, CockpitRow, SportProjectionRow, SportProjections } from '@/lib/types'

function prow(over: Partial<SportProjectionRow> = {}): SportProjectionRow {
  return {
    sport: 'nba', stat: 'points', stat_label: 'Points', player: 'Stephen Curry', player_norm: 'stephen curry', team: 'GSW', opp: 'LAL',
    game_key: 'nba:2026-11-03:GSW@LAL', commence: '2026-11-04T03:00:00Z', mins_to_start: 95, has_line: true, line: 27.5, consensus_line: 27.5,
    side: 'over', proj_mean: 29.1, p_over_model: 0.58, p_model_side: 0.58, p_mkt_side: 0.5, l5_hit_rate: 0.6, l10_hit_rate: 0.7, szn_rate: 0.6,
    szn_avg: 28, avg_l10: 28.4, usage_label: 'Minutes', n_games: 9, best_over_odds: -105, best_over_book: 'draftkings', best_under_odds: -115,
    best_under_book: 'fanduel', p_over_market: 0.5, n_books: 6, model_edge: 0.08, market_ev: 0.02, confluence: false, edge_capped: false,
    beezy_score: 60, score_basis: 'market', validated: false, engine: 'empirical_ewm_v2',
    ...over,
  }
}

function board(rows: SportProjectionRow[], sport: 'nba' | 'nhl' | 'nfl' = 'nba'): SportProjections {
  return { sport, engine: 'x', validated: false, status: 'live', stale: false, as_of: '2026-11-04T01:00:00Z', age_minutes: 10, lines_age_minutes: 9,
    gamelog_latest: null, stale_gamelog: null, stats: [], count: rows.length, rows, summary: {} }
}

const sweepRow = (over: Partial<CockpitRow> = {}): CockpitRow => ({
  id: 'sweep-1', sport: 'nba', source: 'sink_a', shadow: true, player: 'Stephen Curry', player_id: 201939, team: 'GSW', matchup: 'GSW @ LAL', commence: null,
  mins_to_kick: 95, stat: 'points', side: 'OVER', line: 27.5, book: 'betmgm', american: 120, decimal: 2.2, ev: 0.08, p_fair: 0.52, n_books: 5, flag: true,
  reason: null, all_books: [{ book: 'betmgm', over: 120, under: -150, fair: null }], recentForm: null, ...over,
})

const sweepFeed = (rows: CockpitRow[]): CockpitFeed => ({ as_of: '2026-11-04T01:00:00Z', sport: 'all', min_ev: 0.02, rows, sports: {
  nba: { status: 'idle', count: 0, as_of: '2026-11-03T22:35:00Z', age_minutes: 171, stale: true, validated: false },
  mlb: { status: 'idle', count: 0, as_of: null, age_minutes: null, stale: false, validated: true } } })

describe('projectionToCockpitRow', () => {
  it('maps the preferred side, its best price and the matchup', () => {
    const r = projectionToCockpitRow(prow({ side: 'under' }))!
    expect(r).toMatchObject({ side: 'UNDER', book: 'fanduel', american: -115, line: 27.5, matchup: 'GSW @ LAL', stat: 'points', flag: false, shadow: true, source: 'model' })
    expect(r.decimal).toBeCloseTo(1.8696, 3)
  })
  it('shows an EV only when 3+ books stand behind it and the backend did not cap it', () => {
    expect(projectionToCockpitRow(prow())!.ev).toBe(0.02)
    expect(projectionToCockpitRow(prow({ n_books: 2 }))!.ev).toBeNull()
    expect(projectionToCockpitRow(prow({ edge_capped: true }))!.ev).toBeNull()
  })
  it('defaults to the over when the model has no side, and skips rows with no line', () => {
    expect(projectionToCockpitRow(prow({ side: null }))!.side).toBe('OVER')
    expect(projectionToCockpitRow(prow({ line: null, consensus_line: null }))).toBeNull()
  })
})

describe('withProjectionPlayers', () => {
  it('lists the board players when the sweep found nothing, and says the data is fresh', () => {
    const out = withProjectionPlayers(sweepFeed([]), { nba: board([prow(), prow({ player: 'LeBron James', player_norm: 'lebron james' })]) })!
    expect(out.rows.map(r => r.player)).toEqual(['Stephen Curry', 'LeBron James'])
    expect(out.sports.nba).toMatchObject({ status: 'live', stale: false, age_minutes: 10, count: 2 })
    expect(out.sports.mlb?.status).toBe('idle')
  })
  it('keeps the sweep row (flag, all books) and drops the board row for the same player and stat', () => {
    const out = withProjectionPlayers(sweepFeed([sweepRow()]), { nba: board([prow(), prow({ stat: 'rebounds' })]) })!
    const curry = out.rows.filter(r => r.player === 'Stephen Curry')
    expect(curry.map(r => [r.stat, r.flag])).toEqual([['points', true], ['rebounds', false]])
    expect(curry[0].all_books).toHaveLength(1)
    expect(out.sports.nba?.count).toBe(2)
  })
  it('matches names ignoring accents and punctuation', () => {
    const out = withProjectionPlayers(sweepFeed([sweepRow({ player: 'Nikola Jokić' })]), { nba: board([prow({ player: 'Nikola Jokic', player_norm: 'nikola jokic' })]) })!
    expect(out.rows).toHaveLength(1)
  })
  it('cuts each league to its best-scored window', () => {
    const many = Array.from({ length: PLAYERS_PER_SPORT + 30 }, (_, i) => prow({ player: `P${i}`, player_norm: `p${i}`, beezy_score: i }))
    const out = withProjectionPlayers(null, { nba: board(many) })!
    expect(out.rows).toHaveLength(PLAYERS_PER_SPORT)
    expect(out.rows[0].player).toBe(`P${PLAYERS_PER_SPORT + 29}`)
  })
  it('builds a feed from the boards alone when the sweep is down, and returns the sweep untouched when no board loaded', () => {
    expect(withProjectionPlayers(null, { nba: board([prow()]) })!.rows).toHaveLength(1)
    const feed = sweepFeed([sweepRow()])
    expect(withProjectionPlayers(feed, { nba: null, nhl: board([], 'nhl') })).toBe(feed)
    expect(withProjectionPlayers(null, {})).toBeNull()
  })
})
