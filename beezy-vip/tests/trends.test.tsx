/**
 * /edge NBA trends: the windowed maths (hit rates with pushes, histogram, minutes fit, driver tone), the charts and tables, the panel
 * states, and the NBA inspector tabs (Trends / Shots / Prices) wired into the cockpit.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { CockpitFeed, CockpitRow, TrendGame, TrendsOk } from '../lib/types'

const apiGetTrends = jest.fn()
const apiGetShotChart = jest.fn()
jest.mock('@/lib/betting-api', () => ({
  apiGetEdgeCockpit: jest.fn(() => new Promise(() => {})),
  apiGetTrends: (...a: unknown[]) => apiGetTrends(...a),
  apiGetShotChart: (...a: unknown[]) => apiGetShotChart(...a),
}))

import { driverTone, hitSide, histogram, minutesFit, niceWidth, sampleStats, sideRate, windowGames } from '../lib/trends'
import { Distribution, GameLog, MinutesScatter, TrendsBody, TrendsPanel } from '../app/edge/trends'
import { Cockpit } from '../app/edge/cockpit'

const game = (i: number, val: number, over: Partial<TrendGame> = {}): TrendGame => ({
  date: `2026-0${1 + Math.floor(i / 28)}-${String(1 + (i % 28)).padStart(2, '0')}`, opp: ['BOS', 'DET', 'CLE'][i % 3], home: i % 2 === 0, rest: 1, stype: 'RS',
  season: i < 12 ? 2025 : 2026, starter: true, min: 30 + (i % 7), val, pts: val, reb: 4, ast: 5, fg3m: 2, fg3a: 7, fga: 16, fta: 5, tov: 2, pf: 2,
  r5: i >= 4 ? val : null, r10: i >= 9 ? val : null, ...over,
})
const VALS = [22, 31, 27, 35, 19, 30, 28, 33, 26, 29, 24, 27, 30, 21, 33, 25, 28, 36, 27, 31]
const GAMES = VALS.map((v, i) => game(i, v))

const win = (n: number) => ({ n, mean: 27.5, median: 27.5, sd: 4, cv: 0.15, over: 8, under: 10, push: 2, over_rate: 0.44, p10: 21, p25: 24, p75: 31, p90: 34 })
const trends = (over: Partial<TrendsOk> = {}): TrendsOk => ({
  status: 'ok', player: 'Jalen Brunson', team: 'NYK', stat: 'points', label: 'Points', line: 27.5, season: 2026, seasons: [2025, 2026], as_of: '2026-11-02',
  n_loaded: GAMES.length, scope: 'two_seasons', postseason_games_excluded: 19, games: GAMES,
  summary: { season: win(8), l10: win(10), l5: win(5), prev: win(12) }, hist: { lo: 18, hi: 40, width: 2, counts: [1, 2, 3] },
  rolling: { l5: GAMES.map(g => g.r5), l10: GAMES.map(g => g.r10) },
  splits: [
    { key: 'home', label: 'Home', n: 41, mean: 26.6, over_rate: 0.44, min_mean: 35.2 }, { key: 'away', label: 'Away', n: 41, mean: 25.2, over_rate: 0.46, min_mean: 34.9 },
    { key: 'rest0', label: 'Back-to-back', n: 3, mean: 24.5, over_rate: 0.33, min_mean: 35.7 }, { key: 'bench', label: 'Bench', n: 0, mean: null, over_rate: null, min_mean: null },
    { key: 'vs_opp', label: 'vs BOS', n: 5, mean: 24.2, over_rate: 0.4, min_mean: 36.3 },
  ],
  minutes: { n: 20, mean: 33, sd: 2, l5_mean: 34.6, corr_val_min: 0.2, slope_per_min: 0.8, per36: 26.6 },
  teammate_out: [{ player: 'Miles McBride', min_avg: 18.3, n_with: 43, n_without: 39, mean_with: 25.4, mean_without: 27.9, over_with: 0.44, over_without: 0.46, min_with: 34.1, min_without: 36.1, fga_with: 19.8, fga_without: 20.4 }],
  drivers: [
    { key: 'fga', label: 'Own field-goal attempts', corr: 0.72, n: 82, recent_mean: 21.8, season_mean: 20.1, z: 0.8 },
    { key: 'fta', label: 'Own free-throw attempts', corr: 0.45, n: 82, recent_mean: 8.4, season_mean: 5.9, z: 1.75 },
    { key: 'tm_fga', label: "Teammates' field-goal attempts", corr: -0.39, n: 82, recent_mean: 67.6, season_mean: 69, z: -1.4 },
  ],
  variance: { n: 82, sd: 8, cv: 0.31, min_cv: 0.13, share_from_minutes: 0.23, note: 'Minutes explain 23% of the game-to-game swing in points.' },
  proxies: { available: ['minutes'], missing: ['potential assists', 'touches', 'drives', 'passes'], note: 'tracking data is not in our sources' },
  ...over,
})

describe('trends maths', () => {
  it('windows the chronological log', () => {
    expect(windowGames(GAMES, 'l10')).toHaveLength(10)
    expect(windowGames(GAMES, 'l20')).toHaveLength(20)
    expect(windowGames(GAMES, 'season').every(g => g.season === 2026)).toBe(true)
    expect(windowGames(GAMES, 'season')).toHaveLength(8)
    expect(windowGames(GAMES, 'all')).toHaveLength(20)
    expect(windowGames([], 'season')).toEqual([])
  })

  it('counts over, under and pushes on an integer line and never calls a push a hit', () => {
    const gs = [20, 25, 25, 30].map((v, i) => game(i, v))
    const s = sampleStats(gs, 25)
    expect(s).toMatchObject({ n: 4, over: 1, under: 1, push: 2, mean: 25, median: 25 })
    expect(sideRate(s, false)).toBe(0.5)                                          // 1 of the 2 decided games
    expect(hitSide(25, 25, false)).toBeNull()
    expect(hitSide(26, 25.5, false)).toBe(true)
    expect(hitSide(26, 25.5, true)).toBe(false)
    expect(hitSide(null, 25, false)).toBeNull()
    expect(sampleStats([], 25).n).toBe(0)
    expect(sideRate(sampleStats([game(0, 25)], 25), false)).toBeNull()
  })

  it('builds a histogram that accounts for every game', () => {
    const h = histogram(VALS)!
    expect(h.counts.reduce((a, b) => a + b, 0)).toBe(VALS.length)
    expect(h.lo % h.width).toBe(0)
    expect(niceWidth(0, 10)).toBe(1)
    expect(niceWidth(10, 60)).toBe(5)
    expect(histogram([])).toBeNull()
  })

  it('fits stat on minutes and refuses tiny or degenerate samples', () => {
    const gs = Array.from({ length: 12 }, (_, i) => game(i, 10 + 2 * (20 + i), { min: 20 + i }))     // val = 50 + 2 * minutes... exact line
    const fit = minutesFit(gs)!
    expect(fit.slope).toBeCloseTo(2, 6)
    expect(fit.r).toBeCloseTo(1, 6)
    expect(minutesFit(gs.slice(0, 5))).toBeNull()
    expect(minutesFit(Array.from({ length: 12 }, (_, i) => game(i, 20 + i, { min: 30 })))).toBeNull()
  })

  it('reads a driver as hot or cold only when it matters and moves the stat', () => {
    expect(driverTone(0.7, 1.8)).toBe('up')
    expect(driverTone(0.7, -1.8)).toBe('down')
    expect(driverTone(-0.5, 1.4)).toBe('down')                                      // an opposing driver running high drags the stat down
    expect(driverTone(0.7, 0.4)).toBe('flat')
    expect(driverTone(0.1, 3)).toBe('flat')
    expect(driverTone(null, 3)).toBe('flat')
  })
})

describe('trend charts', () => {
  it('draws the game log with the line, rolling means and a season break', () => {
    const { container } = render(<GameLog games={GAMES} line={27.5} under={false} label="Points" rolling />)
    const svg = container.querySelector('svg')!
    expect(svg).toHaveAttribute('aria-label', expect.stringContaining('over cleared in 11 of 20'))
    expect(container.querySelectorAll('rect').length).toBe(20)
    expect(container.querySelectorAll('path').length).toBe(2)                         // L5 and L10
    expect(container).toHaveTextContent('Line 27.5')
    expect(container.querySelector('line[stroke-dasharray="2 3"]')).not.toBeNull()    // the 2025 -> 2026 boundary
  })

  it('flips the hit colouring for an under', () => {
    const { container } = render(<GameLog games={GAMES} line={27.5} under label="Points" rolling={false} />)
    expect(container.querySelector('svg')).toHaveAttribute('aria-label', expect.stringContaining('under cleared in 9 of 20'))
  })

  it('needs two games for a log and eight for a distribution or scatter', () => {
    expect(render(<GameLog games={GAMES.slice(0, 1)} line={27.5} under={false} label="Points" rolling={false} />).container).toHaveTextContent('Not enough games')
    expect(render(<Distribution games={GAMES.slice(0, 5)} line={27.5} under={false} label="Points" />).container).toHaveTextContent('at least 8 games')
    expect(render(<MinutesScatter games={GAMES.slice(0, 5)} line={27.5} label="Points" l5Min={34} />).container).toHaveTextContent('at least 8 games')
  })

  it('states the empirical split of the distribution', () => {
    render(<Distribution games={GAMES} line={27.5} under={false} label="Points" />)
    expect(screen.getByText(/20 games: median/)).toBeInTheDocument()
    expect(screen.getByText(/not a model price/)).toBeInTheDocument()
  })
})

describe('TrendsBody', () => {
  it('renders every section with sample sizes and hides empty splits', () => {
    render(<TrendsBody data={trends()} under={false} />)
    expect(screen.getByLabelText('Sample summary')).toHaveTextContent('Over 27.5')
    expect(screen.getByRole('table', { name: /Points against the line over several windows/ })).toBeInTheDocument()
    const splits = screen.getByRole('table', { name: 'Splits for the market' })
    expect(within(splits).getByText('vs BOS')).toBeInTheDocument()
    expect(within(splits).queryByText('Bench')).not.toBeInTheDocument()              // n = 0
    expect(screen.getByRole('table', { name: 'His numbers when a teammate sat out' })).toHaveTextContent('Miles McBride')
    const drivers = screen.getByRole('table', { name: 'What the stat tracks game to game' })
    expect(drivers).toHaveTextContent('Own free-throw attempts')
    expect(drivers).toHaveTextContent('+1.8')
    expect(screen.getByText(/Not in our data: potential assists, touches, drives, passes/)).toBeInTheDocument()
    expect(screen.getByText(/19 postseason games are left out/)).toBeInTheDocument()
  })

  it('switches the sample window and keeps the strip in step', () => {
    render(<TrendsBody data={trends()} under={false} />)
    expect(screen.getByLabelText('Sample summary')).toHaveTextContent('20 g')
    fireEvent.click(screen.getByRole('button', { name: 'Last 10' }))
    expect(screen.getByLabelText('Sample summary')).toHaveTextContent('10 g')
    fireEvent.click(screen.getByRole('button', { name: 'This season' }))
    expect(screen.getByLabelText('Sample summary')).toHaveTextContent('8 g')
  })

  it('reports the under side everywhere for an under pick', () => {
    render(<TrendsBody data={trends()} under />)
    expect(screen.getByLabelText('Sample summary')).toHaveTextContent('Under 27.5')
    expect(screen.getByRole('table', { name: 'Splits for the market' })).toHaveTextContent('56%')    // 1 - 0.44
  })

  it('tells a teammate-less player so', () => {
    render(<TrendsBody data={trends({ teammate_out: [] })} under={false} />)
    expect(screen.getByText(/No rotation teammate has missed enough/)).toBeInTheDocument()
  })
})

const nbaRow = (over: Partial<CockpitRow> = {}): CockpitRow => ({
  id: 'nba|1', sport: 'nba', source: 'sink_a', shadow: true, player: 'Jalen Brunson', player_id: 3934672, team: 'NYK', matchup: 'NYK @ BOS', commence: null,
  mins_to_kick: 60, stat: 'points', side: 'OVER', line: 27.5, book: 'fanduel', american: -110, decimal: 1.91, ev: 0.03, p_fair: 0.5, n_books: 5,
  flag: true, reason: null, all_books: [{ book: 'fanduel', over: -110, under: -110, fair: 0.5 }],
  recentForm: { line: 27.5, n_games: 3, games: [{ date: '2026-11-01', opp: 'BOS', val: 30, usage: 34, hit: true }, { date: '2026-11-02', opp: 'DET', val: 22, usage: 33, hit: false }, { date: '2026-11-03', opp: 'CLE', val: 28, usage: 35, hit: true }], l5_rate: 0.6, l10_rate: 0.6, szn_rate: 0.5, szn_avg: 26, szn_games: 50, usage_label: 'Minutes', avg_val: 26, avg_usage: 34 },
  ...over,
})

describe('TrendsPanel', () => {
  beforeEach(() => { apiGetTrends.mockReset(); apiGetShotChart.mockReset().mockResolvedValue({ status: 'no_player' }) })

  it('asks for the player, market, line and the opponent read from the matchup', async () => {
    apiGetTrends.mockResolvedValue(trends())
    render(<TrendsPanel row={nbaRow()} />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading trends')
    await waitFor(() => expect(screen.getByLabelText('Sample summary')).toBeInTheDocument())
    expect(apiGetTrends).toHaveBeenCalledWith({ player: 'Jalen Brunson', stat: 'points', line: 27.5, opp: 'BOS' })
  })

  it('says why there is nothing to show and offers the fallback', async () => {
    apiGetTrends.mockResolvedValue({ status: 'no_player' })
    const a = render(<TrendsPanel row={nbaRow()} fallback={<div>fallback chart</div>} />)
    await waitFor(() => expect(a.container).toHaveTextContent('No box-score history for Jalen Brunson'))
    expect(a.container).toHaveTextContent('fallback chart')
    a.unmount()
    apiGetTrends.mockResolvedValue({ status: 'locked' })
    const b = render(<TrendsPanel row={nbaRow()} />)
    await waitFor(() => expect(b.container).toHaveTextContent('part of full NBA prop research'))
    b.unmount()
    apiGetTrends.mockRejectedValue(new Error('boom'))
    const c = render(<TrendsPanel row={nbaRow()} />)
    await waitFor(() => expect(c.container).toHaveTextContent('could not be loaded'))
  })
})

describe('NBA inspector tabs', () => {
  const feed: CockpitFeed = {
    as_of: '2026-11-03T21:00:00Z', sport: 'all', min_ev: 0.02, rows: [nbaRow()],
    sports: { nba: { status: 'shadow_live', count: 1, as_of: null, age_minutes: 3, stale: false, validated: false } },
  }
  beforeEach(() => { apiGetTrends.mockReset().mockResolvedValue(trends()); apiGetShotChart.mockReset().mockResolvedValue({ status: 'no_player' }) })

  it('opens on Trends, then Shots and Prices on demand, and leaves other sports alone', async () => {
    render(<Cockpit feed={feed} sport="nba" updated={Date.now()} loading={false} onRefresh={() => {}} />)
    const tabs = screen.getByRole('tablist', { name: 'NBA research views' })
    expect(within(tabs).getByRole('tab', { name: 'Trends' })).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => expect(screen.getByLabelText('Sample summary')).toBeInTheDocument())
    fireEvent.click(within(tabs).getByRole('tab', { name: 'Shots' }))
    await waitFor(() => expect(apiGetShotChart).toHaveBeenCalled())
    expect(screen.queryByLabelText('Sample summary')).not.toBeInTheDocument()
    fireEvent.click(within(tabs).getByRole('tab', { name: 'Prices' }))
    expect(screen.getByRole('table', { name: /Prices from every book/ })).toBeInTheDocument()
  })

  it('falls back to the L10 log when the trends endpoint has nothing', async () => {
    apiGetTrends.mockResolvedValue({ status: 'no_player' })
    render(<Cockpit feed={feed} sport="nba" updated={Date.now()} loading={false} onRefresh={() => {}} />)
    await waitFor(() => expect(screen.getByText(/No box-score history for Jalen Brunson/)).toBeInTheDocument())
    expect(screen.getByText(/game log vs line/i)).toBeInTheDocument()
  })
})
