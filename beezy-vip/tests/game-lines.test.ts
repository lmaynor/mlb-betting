import type { OddsScreenMode } from '@/lib/odds-screen'
import type { GamePricedGame, GameProjectionRow, GameProjectionsFeed } from '@/lib/game-lines'
import { apiGetGameProjections } from '@/lib/betting-api'

describe('Game Lines quantitative model types and API client', () => {
  it('includes game_lines in OddsScreenMode', () => {
    const mode: OddsScreenMode = 'game_lines'
    expect(mode).toBe('game_lines')
  })

  it('verifies game projections feed and row structure', () => {
    const mockGame: GamePricedGame = {
      game_key: 'nba:2026-10-31:BOS@MIA',
      home_team: 'MIA',
      away_team: 'BOS',
      game_date: '2026-10-31',
      mu_margin: -4.2,
      sigma_margin: 11.8,
      mu_total: 226.4,
      sigma_total: 17.5,
      exp_pace: 99.8,
      markets: [
        {
          system: 'NBA_SPREAD',
          market_label: 'Spread',
          side: 'home',
          line: -3.5,
          p_model: 0.554,
          push_prob: 0.021,
          p_fair: 0.519,
          best_odds: -108,
          best_book: 'fanduel',
          n_books: 6,
          model_edge: 0.035,
          market_ev: 0.028,
          confluence: true,
          beezy_score: 82,
        },
      ],
    }

    const mockRow: GameProjectionRow = {
      sport: 'nba',
      system: 'NBA_SPREAD',
      stat: 'spread',
      stat_label: 'Spread',
      player: 'BOS @ MIA',
      player_norm: 'bos_mia_nba_spread_home',
      team: 'MIA',
      opp: 'BOS',
      game_key: 'nba:2026-10-31:BOS@MIA',
      commence: '2026-10-31T23:30:00Z',
      side: 'home',
      line: -3.5,
      has_line: true,
      proj_mean: -4.2,
      p_model_side: 0.554,
      p_mkt_side: 0.519,
      n_books: 6,
      best_over_odds: -108,
      best_over_book: 'fanduel',
      model_edge: 0.035,
      market_ev: 0.028,
      confluence: true,
      beezy_score: 82,
      push_prob: 0.021,
    }

    const feed: GameProjectionsFeed = {
      sport: 'nba',
      status: 'live',
      engine: 'kalman_joint_t_v1',
      as_of: '2026-10-31T20:00:00Z',
      age_minutes: 5.2,
      stale: false,
      count: 1,
      rows: [mockRow],
      priced_games: [mockGame],
      games: [{ game_key: 'nba:2026-10-31:BOS@MIA', away: 'BOS', home: 'MIA', start: '2026-10-31T23:30:00Z' }],
      markets: ['spread', 'total', 'ml'],
    }

    expect(feed.sport).toBe('nba')
    expect(feed.priced_games[0].mu_margin).toBe(-4.2)
    expect(feed.rows[0].model_edge).toBe(0.035)
  })

  it('apiGetGameProjections fetches from proxy endpoint in browser environment', async () => {
    const mockFeed: GameProjectionsFeed = {
      sport: 'nhl',
      status: 'live',
      engine: 'distributional_matrix_v1',
      as_of: '2026-10-31T20:00:00Z',
      age_minutes: 3.1,
      stale: false,
      count: 0,
      rows: [],
      priced_games: [],
      games: [],
      markets: ['spread', 'total', 'ml'],
    }

    const originalFetch = global.fetch
    const mockFetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => mockFeed,
    })
    global.fetch = mockFetch

    try {
      const res = await apiGetGameProjections('nhl', { market: 'spread', limit: 100 })
      expect(mockFetch).toHaveBeenCalledTimes(1)
      const calledUrl = mockFetch.mock.calls[0][0]
      expect(calledUrl).toContain('sport=nhl')
      expect(calledUrl).toContain('market=spread')
      expect(calledUrl).toContain('limit=100')
      expect(res.sport).toBe('nhl')
      expect(res.engine).toBe('distributional_matrix_v1')
    } finally {
      global.fetch = originalFetch
    }
  })
})
