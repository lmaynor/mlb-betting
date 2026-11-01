import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import { apiGetGameProjections } from '@/lib/betting-api'
import {
  buildGameLinesData, expectedMarginLabel, GAME_LINES_DEFAULT_LABEL, gameFeedEngines, gameFeedLabel, gameLinesCount, gameLinesViewLabel,
  highlightModelDiff, parseGameProjectionQuery, gameQueryString,
  type GamePricedGame, type GameProjectionRow, type GameProjectionsFeed,
} from '@/lib/game-lines'
import { GameCard, GameLinesNotice, GameLinesTable } from '@/app/ev/game-lines-view'

const mockGame = (over: Partial<GamePricedGame> = {}): GamePricedGame => ({
  game_key: 'nba:2026-10-31:BOS@MIA', home_team: 'MIA', away_team: 'BOS', game_date: '2026-10-31',
  mu_margin: -4.2, sigma_margin: 11.8, mu_total: 226.4, sigma_total: 17.5, exp_pace: 99.8,
  markets: [{
    system: 'NBA_SPREAD', market_label: 'Spread', side: 'home', line: -3.5, p_model: 0.554, push_prob: 0.021, p_fair: 0.519,
    best_odds: -108, best_book: 'fanduel', n_books: 6, model_edge: 0.035, market_ev: 0.028, confluence: true, beezy_score: 82,
  }],
  ...over,
})
const mockRow = (over: Partial<GameProjectionRow> = {}): GameProjectionRow => ({
  sport: 'nba', system: 'NBA_SPREAD', stat: 'spread', stat_label: 'Spread', player: 'BOS @ MIA', player_norm: 'bos_mia_nba_spread_home',
  team: 'MIA', opp: 'BOS', game_key: 'nba:2026-10-31:BOS@MIA', commence: '2026-10-31T23:30:00Z', side: 'home', line: -3.5, has_line: true,
  proj_mean: -4.2, p_model_side: 0.554, p_mkt_side: 0.519, n_books: 6, best_over_odds: -108, best_over_book: 'fanduel',
  model_edge: 0.035, market_ev: 0.028, confluence: true, beezy_score: 82, push_prob: 0.021, ...over,
})
const feed = (over: Partial<GameProjectionsFeed> = {}): GameProjectionsFeed => ({
  sport: 'nba', status: 'live', as_of: '2026-10-31T20:00:00Z', age_minutes: 5.2, stale: false, count: 1, rows: [mockRow()],
  priced_games: [mockGame()], games: [{ game_key: 'nba:2026-10-31:BOS@MIA', away: 'BOS', home: 'MIA', start: '2026-10-31T23:30:00Z' }],
  markets: ['spread', 'total', 'ml'], ...over,
})
const FILTERS = { sport: 'all', market: 'all', matchup: 'all', side: 'all', minEdge: 0, q: '' } as const

describe('feed status label (the optional validated / label / engine fields)', () => {
  it('defaults to UNVALIDATED when the feed carries no label or is not validated', () => {
    expect(gameFeedLabel(feed())).toBe(GAME_LINES_DEFAULT_LABEL)
    expect(gameFeedLabel(feed({ validated: false }))).toBe(GAME_LINES_DEFAULT_LABEL)
    expect(gameFeedLabel(feed({ validated: false, label: 'Experimental' }))).toBe(GAME_LINES_DEFAULT_LABEL) // never softer than UNVALIDATED
  })
  it('uses the backend wording when it already says UNVALIDATED, and only a validated feed may drop the warning', () => {
    expect(gameFeedLabel(feed({ validated: false, label: 'UNVALIDATED shadow model' }))).toBe('UNVALIDATED shadow model')
    expect(gameFeedLabel(feed({ validated: true, label: 'Validated: 2021-25 backtest' }))).toBe('Validated: 2021-25 backtest')
  })
  it('the view is only as validated as its weakest feed', () => {
    expect(gameLinesViewLabel([feed({ validated: true, label: 'ok' }), feed({ validated: false })])).toEqual({ label: GAME_LINES_DEFAULT_LABEL, validated: false })
    expect(gameLinesViewLabel([feed({ validated: true, label: 'ok' }), null]).validated).toBe(true)
    expect(gameLinesViewLabel([null, null]).validated).toBe(false)
  })
  it('names the engine from the feeds, never from a hard-coded list', () => {
    expect(gameFeedEngines([feed({ engine: 'kalman_joint_t_v1' }), feed({ engine: 'kalman_joint_t_v1' }), feed({ engine: 'matrix_v2' }), feed({ engine: null }), null]))
      .toEqual(['KALMAN_JOINT_T_V1', 'MATRIX_V2'])
    expect(gameFeedEngines([feed()])).toEqual([])
  })
  it('only a validated feed gets the green highlight', () => {
    expect(highlightModelDiff(0.03, false)).toBe(false)
    expect(highlightModelDiff(0.03, true)).toBe(true)
    expect(highlightModelDiff(0.01, true)).toBe(false)
    expect(highlightModelDiff(null, true)).toBe(false)
  })
})

describe('expectedMarginLabel', () => {
  it('names the favourite, and a zero margin is a pick, never AWAY -0.0', () => {
    expect(expectedMarginLabel({ mu_margin: 4.24, home_team: 'MIA', away_team: 'BOS' })).toBe('MIA -4.2')
    expect(expectedMarginLabel({ mu_margin: -4.2, home_team: 'MIA', away_team: 'BOS' })).toBe('BOS -4.2')
    expect(expectedMarginLabel({ mu_margin: 0, home_team: 'MIA', away_team: 'BOS' })).toBe('PICK')
    expect(expectedMarginLabel({ mu_margin: -0.04, home_team: 'MIA', away_team: 'BOS' })).toBe('PICK')
    expect(expectedMarginLabel({ home_team: 'MIA', away_team: 'BOS' })).toBeNull()
  })
})

describe('parseGameProjectionQuery (the proxy forwards nothing else)', () => {
  const q = (s: string) => parseGameProjectionQuery(new URLSearchParams(s))
  it('accepts a known sport, a real day and an in-range limit', () => {
    expect(q('sport=nba&day=2026-10-31&limit=300')).toEqual({ ok: true, query: { sport: 'nba', day: '2026-10-31', limit: 300 } })
    expect(q('sport=NHL')).toEqual({ ok: true, query: { sport: 'nhl' } })
    expect(q('sport=nfl&limit=1200')).toMatchObject({ ok: true })
    expect(q('sport=nba&market=spread')).toEqual({ ok: true, query: { sport: 'nba', market: 'spread' } })
  })
  it('rejects a missing or unknown sport', () => {
    expect(q('').ok).toBe(false)
    expect(q('sport=mlb').ok).toBe(false)
    expect(q('sport=nba%26x%3D1').ok).toBe(false)
  })
  it('rejects a malformed or impossible day', () => {
    for (const day of ['2026-13-01', '2026-02-30', '20261031', '2026-10-31T00:00', "2026-10-31'--", '../etc']) expect(q(`sport=nba&day=${encodeURIComponent(day)}`).ok).toBe(false)
  })
  it('rejects a limit outside 1..1200 or not a plain integer', () => {
    for (const limit of ['0', '1201', '-5', '1.5', '1e3', 'abc', '99999']) expect(q(`sport=nba&limit=${limit}`).ok).toBe(false)
  })
  it('rejects an unknown market', () => expect(q('sport=nba&market=player_points').ok).toBe(false))
  it('rebuilds the upstream query from validated values only (extra params are dropped)', () => {
    const r = q('sport=nba&day=2026-10-31&limit=50&api_key=steal&x=1')
    expect(r.ok && gameQueryString(r.query)).toBe('sport=nba&day=2026-10-31&limit=50')
  })
})

describe('buildGameLinesData and the visible count', () => {
  it('counts priced games on the Cards view and feed rows on the Data Matrix', () => {
    const two = feed({ rows: [mockRow(), mockRow({ side: 'away' }), mockRow({ system: 'NBA_TOTAL', stat: 'total', side: 'over', line: 226.5 })], priced_games: [mockGame()] })
    const data = buildGameLinesData({ nba: two, nhl: null }, FILTERS)
    expect(gameLinesCount(data, 'cards')).toBe(1)
    expect(gameLinesCount(data, 'table')).toBe(3)
  })
  it('carries each feed\'s validated flag onto its cards and rows', () => {
    const data = buildGameLinesData({ nba: feed({ validated: true }), nhl: feed({ sport: 'nhl', validated: false }) }, FILTERS)
    expect(data.pricedGames.map(g => [g.sport, g.validated])).toEqual([['nba', true], ['nhl', false]])
    expect(data.rows.map(r => r.validated)).toEqual([true, false])
  })
  it('filters by sport, market, side and minimum difference', () => {
    const f = feed({ rows: [mockRow(), mockRow({ system: 'NBA_TOTAL', stat: 'total', side: 'over', model_edge: 0.001 })] })
    expect(buildGameLinesData({ nba: f }, { ...FILTERS, market: 'total' }).rows).toHaveLength(1)
    expect(buildGameLinesData({ nba: f }, { ...FILTERS, minEdge: 0.02 }).rows).toHaveLength(1)
    expect(buildGameLinesData({ nba: f }, { ...FILTERS, sport: 'nhl' }).rows).toHaveLength(0)
    expect(buildGameLinesData({ nba: f }, { ...FILTERS, side: 'away_under' }).rows).toHaveLength(0)
  })
})

describe('Game Lines view: badge, header and highlight', () => {
  it('shows the default UNVALIDATED label and never says "edge" as a column or in the copy', () => {
    render(<GameLinesNotice label={gameLinesViewLabel([feed()]).label} validated={false} />)
    expect(screen.getByTestId('game-lines-label')).toHaveTextContent(GAME_LINES_DEFAULT_LABEL.toUpperCase())
    expect(screen.getByRole('note')).toHaveTextContent(/not an edge until a backtest supports it/i)
  })
  it('shows the backend label for a validated feed', () => {
    render(<GameLinesNotice label="Validated: 2021-25 backtest" validated />)
    expect(screen.getByTestId('game-lines-label')).toHaveTextContent('VALIDATED: 2021-25 BACKTEST')
  })
  it('renders MODEL DIFF headers (not EDGE) and no green on an unvalidated difference', () => {
    const data = buildGameLinesData({ nba: feed({ validated: false }) }, FILTERS)
    const { container, unmount } = render(<GameCard game={data.pricedGames[0]} />)
    expect(screen.getByText('MODEL DIFF')).toBeInTheDocument()
    expect(screen.queryByText('EDGE')).not.toBeInTheDocument()
    expect(container.innerHTML).not.toContain('87, 242, 135') // jsdom normalises #57F287 / rgba(87,242,135,..)
    unmount()
    const t = render(<GameLinesTable rows={data.rows} limit={10} />)
    expect(screen.getByText('MODEL DIFF')).toBeInTheDocument()
    expect(t.container.innerHTML).not.toContain('87, 242, 135')
  })
  it('highlights green only when the feed is validated', () => {
    const data = buildGameLinesData({ nba: feed({ validated: true }) }, FILTERS)
    const { container } = render(<GameCard game={data.pricedGames[0]} />)
    expect(container.innerHTML).toContain('87, 242, 135')
  })
  it('a zero model margin renders as PICK on the card', () => {
    const data = buildGameLinesData({ nba: feed({ priced_games: [mockGame({ mu_margin: 0 })] }) }, FILTERS)
    render(<GameCard game={data.pricedGames[0]} />)
    expect(screen.getByText('PICK')).toBeInTheDocument()
    expect(screen.queryByText(/-0\.0/)).not.toBeInTheDocument()
  })
})

describe('apiGetGameProjections', () => {
  it('fetches from the proxy endpoint in a browser environment', async () => {
    const mockFeed = feed({ sport: 'nhl', engine: 'distributional_matrix_v1', rows: [], priced_games: [], games: [], count: 0 })
    const originalFetch = global.fetch
    const mockFetch = jest.fn().mockResolvedValue({ ok: true, json: async () => mockFeed })
    global.fetch = mockFetch
    try {
      const res = await apiGetGameProjections('nhl', { market: 'spread', limit: 100 })
      expect(mockFetch).toHaveBeenCalledTimes(1)
      const calledUrl = mockFetch.mock.calls[0][0]
      expect(calledUrl).toContain('sport=nhl')
      expect(calledUrl).toContain('market=spread')
      expect(calledUrl).toContain('limit=100')
      expect(res.engine).toBe('distributional_matrix_v1')
    } finally {
      global.fetch = originalFetch
    }
  })
})
