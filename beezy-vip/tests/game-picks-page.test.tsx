import '@testing-library/jest-dom'
import { fireEvent, render, screen } from '@testing-library/react'
import { GamePicksClient } from '@/app/picks/games/game-picks-client'
import { isMlbOffseason } from '@/lib/dates'
import type { GamePricedGame, GameProjectionRow, GameProjectionsFeed } from '@/lib/game-lines'

const game = (key: string, away: string, home: string, system: string): GamePricedGame => ({
  game_key: key, home_team: home, away_team: away, game_date: '2026-11-02', mu_margin: -4.2, sigma_margin: 11.8, mu_total: 226.4, sigma_total: 17.5, exp_pace: 99.8,
  markets: [{ system, market_label: 'Spread', side: 'home', line: -3.5, p_model: 0.554, push_prob: 0.02, p_fair: 0.519, best_odds: -108, best_book: 'fanduel', n_books: 6, model_edge: 0.035, market_ev: 0.028, confluence: false, beezy_score: 50 }],
})
const row = (key: string, system: string, stat: string): GameProjectionRow => ({
  sport: 'nba', system, stat, stat_label: stat, player: 'BOS @ MIA', player_norm: `${key}_${stat}`, team: 'MIA', opp: 'BOS', game_key: key, commence: '2026-11-02T23:30:00Z',
  side: 'home', line: -3.5, has_line: true, proj_mean: -4.2, p_model_side: 0.554, p_mkt_side: 0.519, n_books: 6, best_over_odds: -108, best_over_book: 'fanduel',
  model_edge: 0.035, market_ev: 0.028, confluence: false, beezy_score: 50, push_prob: 0.02,
})
const feed = (sport: 'nba' | 'nhl', key: string, away: string, home: string, system: string): GameProjectionsFeed => ({
  sport, status: 'live', as_of: '2026-11-02T20:00:00Z', age_minutes: 4, stale: false, count: 1, rows: [row(key, system, 'spread')],
  priced_games: [game(key, away, home, system)], games: [{ game_key: key, away, home, start: '2026-11-02T23:30:00Z' }], markets: ['spread', 'total', 'ml'],
})

describe('/picks/games NBA + NHL game lines (reachable while MLB is in the offseason)', () => {
  const feeds = { nba: feed('nba', 'nba:2026-11-02:BOS@MIA', 'BOS', 'MIA', 'NBA_SPREAD'), nhl: feed('nhl', 'nhl:2026-11-02:TOR@MTL', 'TOR', 'MTL', 'NHL_SPREAD') }

  it('shows both sports with the UNVALIDATED label', () => {
    render(<GamePicksClient feeds={feeds} sport="all" />)
    expect(screen.getByTestId('game-lines-label').textContent).toMatch(/UNVALIDATED/i)
    expect(screen.getAllByText(/BOS/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/TOR/).length).toBeGreaterThan(0)
  })
  it('narrows to one sport and says so when the feed is not published', () => {
    const { rerender } = render(<GamePicksClient feeds={feeds} sport="nhl" />)
    expect(screen.queryAllByText(/BOS/)).toHaveLength(0)
    expect(screen.getAllByText(/TOR/).length).toBeGreaterThan(0)
    rerender(<GamePicksClient feeds={{ nba: null, nhl: null }} sport="all" />)
    expect(screen.getByRole('status').textContent).toMatch(/not published yet/i)
  })
  it('the market filter hides markets that do not match', () => {
    render(<GamePicksClient feeds={feeds} sport="all" />)
    fireEvent.click(screen.getByRole('button', { name: 'Total' }))
    expect(screen.getByRole('status').textContent).toMatch(/No priced games/i)
  })
})

describe('isMlbOffseason (Central, Nov 5 - Mar 19)', () => {
  const at = (iso: string) => isMlbOffseason(new Date(iso))
  it('is true through the winter and false in season', () => {
    expect(at('2026-11-05T18:00:00Z')).toBe(true)
    expect(at('2026-12-25T18:00:00Z')).toBe(true)
    expect(at('2027-03-19T18:00:00Z')).toBe(true)
  })
  it('is false for the World Series window and from opening day on', () => {
    expect(at('2026-11-04T18:00:00Z')).toBe(false)
    expect(at('2026-10-20T18:00:00Z')).toBe(false)
    expect(at('2027-03-20T18:00:00Z')).toBe(false)
    expect(at('2027-07-01T18:00:00Z')).toBe(false)
  })
})
