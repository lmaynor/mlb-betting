import {
  buildDailyCard,
  flatUnits,
  fromBet,
  fromProjection,
  isEvOutlier,
  isOfficialCardPlay,
  rankTopPicks,
  recordOf,
  topFromBets,
  type TopPick,
} from '../lib/top-picks'
import type { Bet, SportProjectionRow } from '../lib/types'

const bet = (o: Partial<Bet>): Bet => ({
  id: 1, system: 'K', game_date: '2026-10-16', game_pk: 100, bet_type: 'K_OVER_5.5', player: 'Gerrit Cole',
  away_team: 'NYY', home_team: 'BOS', odds: 110, stake: 25, model_prob: 0.58, market_prob: 0.5, edge: 0.08,
  kelly_pct: 0.02, kelly_triggered: true, result: null, profit: null, paper: true, book: 'draftkings', notes: null,
  created_at: '2026-10-16T16:00:00Z', ...o,
})

const proj = (o: Partial<SportProjectionRow>): SportProjectionRow => ({
  sport: 'nba', stat: 'pts', stat_label: 'Points', player: 'Jalen Brunson', player_norm: 'jalen brunson', team: 'NYK', opp: 'BOS',
  game_key: 'nba:2026-10-16:BOS@NYK', commence: null, mins_to_start: 120, has_line: true, line: 27.5, consensus_line: 27.5, side: 'over',
  proj_mean: 29.1, p_over_model: 0.6, p_model_side: 0.6, p_mkt_side: 0.52, l5_hit_rate: 0.6, l10_hit_rate: 0.6, szn_rate: 0.5, szn_avg: 27,
  avg_l10: 28, usage_label: 'Usage', n_games: 20, best_over_odds: -110, best_over_book: 'fanduel', best_under_odds: -110, best_under_book: 'betmgm',
  p_over_market: 0.52, n_books: 6, model_edge: 0.08, market_ev: 0.05, confluence: true, edge_capped: false, beezy_score: 60,
  score_basis: 'confluence', validated: false, engine: 'empirical_ewm_v1', ...o,
})

describe('top picks', () => {
  it('a projection row needs a line, a side, a price and a future start', () => {
    expect(fromProjection(proj({}))).not.toBeNull()
    expect(fromProjection(proj({ has_line: false }))).toBeNull()
    expect(fromProjection(proj({ side: null }))).toBeNull()
    expect(fromProjection(proj({ beezy_score: 0 }))).toBeNull()
    expect(fromProjection(proj({ mins_to_start: -5 }))).toBeNull()
    expect(fromProjection(proj({ best_over_odds: null }))).toBeNull()
  })

  it('a projection takes the price of the side the model likes and is never tracked', () => {
    const over = fromProjection(proj({}))!
    expect(over.odds).toBe(-110)
    expect(over.book).toBe('fanduel')
    expect(over.what).toBe('Over 27.5 Points')
    expect(over.basis).toBe('confluence')
    expect(over.tracked).toBe(false)
    const under = fromProjection(proj({ side: 'under', best_under_odds: 105, confluence: false }))!
    expect(under.odds).toBe(105)
    expect(under.book).toBe('betmgm')
    expect(under.basis).toBe('projection')
  })

  it('confluence edge is the smaller of model edge and market EV', () => {
    expect(fromProjection(proj({ model_edge: 0.08, market_ev: 0.05 }))!.edge).toBe(0.05)
    expect(fromProjection(proj({ score_basis: 'market', confluence: false, model_edge: 0.01, market_ev: 0.06 }))!.edge).toBe(0.06)
  })

  it('MLB bets are tracked, labelled model vs +EV, and edge stored as percent is normalised', () => {
    const m = fromBet(bet({ edge: 8 }))
    expect(m.tracked).toBe(true)
    expect(m.basis).toBe('model')
    expect(m.edge).toBeCloseTo(0.08)
    expect(m.who).toBe('Gerrit Cole')
  })

  it('ranks by score, then edge, then tracked first, and caps at 10', () => {
    const mk = (key: string, score: number, edge: number | null, tracked: boolean): TopPick =>
      ({ key, sport: tracked ? 'mlb' : 'nba', who: key, what: '', game: '', odds: 100, book: null, edge, score, basis: 'model', tracked, result: null })
    const ranked = rankTopPicks([mk('a', 50, 0.05, false), mk('b', 70, 0.02, false), mk('c', 50, 0.05, true), mk('d', 50, 0.09, false)])
    expect(ranked.map(p => p.key)).toEqual(['b', 'd', 'c', 'a'])
    const many = Array.from({ length: 25 }, (_, i) => mk(`k${i}`, i, 0.01, true))
    expect(rankTopPicks(many)).toHaveLength(10)
    expect(rankTopPicks(many)[0].score).toBe(24)
  })

  it('collapses one prop flagged at several books and drops unstaked predictions', () => {
    const a = bet({ id: 1, book: 'draftkings', edge: 0.05 })
    const b = bet({ id: 2, book: 'fanduel', edge: 0.09 })
    const c = bet({ id: 3, player: 'Other Guy', kelly_triggered: false })
    const top = topFromBets([a, b, c])
    expect(top).toHaveLength(1)
    expect(top[0].edge).toBeCloseTo(0.09)
  })

  it('flat units: win pays the price, loss is -1, push / void / pending are not graded', () => {
    expect(flatUnits(150, 'win')).toBeCloseTo(1.5)
    expect(flatUnits(-200, 'win')).toBeCloseTo(0.5)
    expect(flatUnits(-110, 'loss')).toBe(-1)
    expect(flatUnits(-110, 'push')).toBeNull()
    expect(flatUnits(-110, null)).toBeNull()
  })

  it('record counts graded picks and leaves voids and pending out of the units', () => {
    const r = recordOf([
      { odds: 100, result: 'win' }, { odds: -200, result: 'win' }, { odds: 120, result: 'loss' },
      { odds: 100, result: 'push' }, { odds: 100, result: 'void' }, { odds: 100, result: null },
    ])
    expect(r).toEqual({ wins: 2, losses: 1, pushes: 1, graded: 4, pending: 1, units: 1 + 0.5 - 1 })
  })

  it('isOfficialCardPlay enforces sweet-spot EV 3-10%, score >= 68, confluence, 4+ books', () => {
    const ok: TopPick = {
      key: 'nba|1', sport: 'nba', who: 'Tatum', what: 'Over 26.5 Pts', game: 'BOS @ NYK',
      odds: 112, book: 'fanduel', edge: 0.054, score: 74, basis: 'confluence', tracked: false,
      result: null, nBooks: 6, confluence: true, modelEdge: 0.032,
    }
    expect(isOfficialCardPlay(ok)).toBe(true)

    // Adverse selection cap: edge > 10% rejected
    expect(isOfficialCardPlay({ ...ok, edge: 0.12 })).toBe(false)
    // Edge < 3% rejected
    expect(isOfficialCardPlay({ ...ok, edge: 0.02 })).toBe(false)
    // Score < 68 rejected
    expect(isOfficialCardPlay({ ...ok, score: 65 })).toBe(false)
    // nBooks < 4 rejected
    expect(isOfficialCardPlay({ ...ok, nBooks: 3 })).toBe(false)
    // Confluence required for projections
    expect(isOfficialCardPlay({ ...ok, confluence: false, basis: 'projection' })).toBe(false)
  })

  it('isEvOutlier identifies soft book market outliers without confluence', () => {
    const outlier: TopPick = {
      key: 'nba|2', sport: 'nba', who: 'Brown', what: 'Over 22.5 Pts', game: 'BOS @ NYK',
      odds: 105, book: 'draftkings', edge: 0.05, score: 62, basis: 'projection', tracked: false,
      result: null, nBooks: 4, confluence: false,
    }
    expect(isEvOutlier(outlier)).toBe(true)

    // Confluence play in official gate is not an outlier
    const official: TopPick = { ...outlier, score: 72, confluence: true, basis: 'confluence', modelEdge: 0.02 }
    expect(isEvOutlier(official)).toBe(false)
  })

  it('buildDailyCard returns dynamic 1..5 plays without padding and deduplicates player/game', () => {
    const p1: TopPick = {
      key: 'p1', sport: 'nba', who: 'Tatum', what: 'Over 26.5 Pts', game: 'BOS @ NYK',
      odds: 110, book: 'fanduel', edge: 0.05, score: 75, basis: 'confluence', tracked: false,
      result: null, nBooks: 5, confluence: true, modelEdge: 0.03,
    }
    const p1_reb: TopPick = {
      ...p1, key: 'p1_reb', what: 'Over 8.5 Reb', score: 72,
    }
    const p2: TopPick = {
      key: 'p2', sport: 'nba', who: 'Brunson', what: 'Over 25.5 Pts', game: 'BOS @ NYK',
      odds: -105, book: 'draftkings', edge: 0.045, score: 70, basis: 'confluence', tracked: false,
      result: null, nBooks: 5, confluence: true, modelEdge: 0.02,
    }
    const p3: TopPick = {
      key: 'p3', sport: 'nba', who: 'Towns', what: 'Over 21.5 Pts', game: 'BOS @ NYK',
      odds: -110, book: 'betmgm', edge: 0.04, score: 69, basis: 'confluence', tracked: false,
      result: null, nBooks: 5, confluence: true, modelEdge: 0.02,
    }

    // p1 has 2 props -> only best (p1 pts, score 75) kept
    // BOS @ NYK has 3 players (Tatum, Brunson, Towns) -> only top 2 kept (Tatum, Brunson)
    const card = buildDailyCard([p1, p1_reb, p2, p3])
    expect(card).toHaveLength(2)
    expect(card.map(p => p.who)).toEqual(['Tatum', 'Brunson'])
  })
})
