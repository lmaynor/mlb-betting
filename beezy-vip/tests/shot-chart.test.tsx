/**
 * /edge NBA shot chart: the pure maths (shrinkage, the six-step diverging scale, hex geometry, court paths) and the panel
 * (loading / locked / empty / error states, the three views, the opponent table, the stale-season note).
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { CockpitRow, ShotChartOk } from '../lib/types'

const apiGetShotChart = jest.fn()
jest.mock('@/lib/betting-api', () => ({ apiGetShotChart: (...a: unknown[]) => apiGetShotChart(...a), apiGetEdgeCockpit: jest.fn(() => new Promise(() => {})) }))

import {
  DIFF_BREAKS, DIFF_COLORS, courtPaths, diffBin, diffColor, diffPoints, hexCenter, hexMarks, hexPoints, shotPaths, shrunkRate, sizeBins, sx, sy,
} from '../lib/shot-chart'
import { opponentOf, ShotChartBody, ShotChartPanel, VolumeBars } from '../app/edge/shot-chart'

describe('shot chart maths', () => {
  it('shrinks small samples toward the league rate', () => {
    expect(shrunkRate(1, 1, 0.5)).toBeCloseTo(0.5833, 3)                       // 1-for-1 is not 100%
    expect(shrunkRate(50, 100, 0.5)).toBeCloseTo(0.5, 6)
    expect(diffPoints(1, 1, 500, 1000)).toBeCloseTo(8.33, 1)                    // +8 pts, not +50
    expect(diffPoints(60, 100, 500, 1000)).toBeCloseTo(9.52, 1)
    expect(diffPoints(3, 4, 0, 0)).toBe(0)                                      // no league baseline: neutral
  })

  it('bins differences on the notebook breakpoints -9, -3, 0, +3, +9', () => {
    expect(DIFF_BREAKS).toEqual([-9, -3, 0, 3, 9])
    expect(DIFF_COLORS).toHaveLength(6)
    const cases: [number, number][] = [[-20, 0], [-9.01, 0], [-9, 1], [-3.01, 1], [-3, 2], [-0.01, 2], [0, 3], [2.99, 3], [3, 4], [8.99, 4], [9, 5], [30, 5]]
    for (const [d, bin] of cases) expect(diffBin(d)).toBe(bin)
    expect(diffColor(-12)).toBe('#b91c1c')
    expect(diffColor(12)).toBe('#57F287')
  })

  it('splits cell sizes into three percentile bins', () => {
    const bin = sizeBins([1, 1, 2, 3, 5, 8, 13, 21, 34])
    expect(bin(1)).toBe(0)
    expect(bin(34)).toBe(2)
    expect([1, 3, 8, 34].map(bin).every(b => b === 0 || b === 1 || b === 2)).toBe(true)
    expect(sizeBins([])(5)).toBe(0)
  })

  it('maps feet to svg units with the baseline at the bottom', () => {
    expect(sx(25)).toBe(0)                                                      // mirrored: the shooter's right (negative x) is the chart's right
    expect(sx(-25)).toBe(500)
    expect(sy(0)).toBe(380)
    expect(sy(5.25)).toBeCloseTo(327.5, 6)
    const [cx, cy] = hexCenter(0, 0, 1.5)
    expect([cx, cy]).toEqual([0, 0])
    expect(hexPoints(0, 0, 1.5, 1.5).split(' ')).toHaveLength(6)
  })

  it('draws one path for makes and one for misses', () => {
    const p = shotPaths([[0, 50, 1], [100, 120, 0], [-200, 250, 3], [30, 60, 2]])
    expect((p.made.match(/M/g) ?? []).length).toBe(2)
    expect((p.missed.match(/M/g) ?? []).length).toBe(2)
  })

  it('builds hex marks coloured by the shrunk difference with larger cells for busier spots', () => {
    const marks = hexMarks([[0, 0, 40, 32, 1000, 650], [1, 0, 2, 0, 1000, 450], [0, 1, 12, 6, 1000, 450]], 1.5)
    expect(marks).toHaveLength(3)
    expect(marks[0].fill).toBe('#57F287')                                        // 32/40 vs 65%, shrunk: +13
    expect(marks[1].diff).toBeLessThan(0)                                        // 0 for 2 vs 45%, shrunk
    expect(marks[1].diff).toBeGreaterThan(-20)
    expect(marks[0].bin).toBeGreaterThanOrEqual(marks[2].bin)
  })

  it('has every court line as a path', () => {
    const p = courtPaths()
    for (const k of Object.keys(p) as (keyof typeof p)[]) expect(p[k]).toMatch(/^M/)
    expect(p.threePoint).toContain('A237.5 237.5 0 0 1')
  })
})

describe('opponentOf', () => {
  it('reads the opponent from either side of the matchup', () => {
    expect(opponentOf('NYK @ BOS', 'NYK')).toBe('BOS')
    expect(opponentOf('NYK @ BOS', 'BOS')).toBe('NYK')
    expect(opponentOf('NYK @ BOS', 'LAL')).toBeNull()
    expect(opponentOf(null, 'NYK')).toBeNull()
    expect(opponentOf('garbage', 'NYK')).toBeNull()
  })
})

const hexCells: ShotChartOk['hexes'] = [[0, 0, 40, 30, 1000, 650], [1, 0, 6, 2, 900, 400], [0, 1, 14, 6, 800, 360]]
const zones: ShotChartOk['zones'] = [
  { key: 'Restricted Area|C', label: 'Restricted area', cx: 0, cy: 6.4, fga: 226, fgm: 145, lfga: 59000, lfgm: 39000 },
  { key: 'Left Corner 3|L', label: 'Left corner 3', cx: -22, cy: 6, fga: 14, fgm: 5, lfga: 9000, lfgm: 3400 },
  { key: 'Mid-Range|C', label: 'Mid-range centre', cx: 0, cy: 17, fga: 0, fgm: 0, lfga: 12000, lfgm: 5200 },
]
const ok = (over: Partial<ShotChartOk> = {}): ShotChartOk => ({
  status: 'ok', player: 'Jalen Brunson', team: 'NYK', season: 2024, label: '2024-25', seasons: [2024, 2023], source: 'x', last_game: '2025-04-13',
  hoop: [0, 5.25], hex_r: 1.5, fga: 240, fgm: 150, fg3a: 60, fg3m: 24, n_games: 65,
  league: { fga: 200000, fgm: 94000, fg3a: 75000, fg3m: 27000 },
  shots: [[0, 60, 1], [100, 150, 0], [-220, 70, 3], [180, 250, 2]], hexes: hexCells, grid: [[0, 0, 0], [1, 0, 1], [0, 1, 2], [2, 0, -1]],
  zones,
  groups: [{ group: 'Layup', fga: 100, fgm: 70, lfga: 40000, lfgm: 24000 }, { group: 'Jump shot', fga: 120, fgm: 50, lfga: 68000, lfgm: 25600 }],
  games: Array.from({ length: 6 }, (_, i) => ({ date: `2025-04-0${i + 1}`, opp: 'BOS', home: i % 2 === 0, fga: 18 + i, fgm: 9, fg3a: 6 + (i % 3), fg3m: 2 })),
  venue: { home: { games: 34, fga: 606, fgm: 289, fg3a: 202, fg3m: 75 }, away: { games: 31, fga: 594, fgm: 296, fg3a: 195, fg3m: 77 } },
  opponent: { team: 'BOS', fga: 6000, fg3a: 2600, zones: [{ key: 'Restricted Area|C', fga: 1800, fgm: 1200 }, { key: 'Left Corner 3|L', fga: 220, fgm: 80 }, { key: 'Mid-Range|C', fga: 400, fgm: 160 }], lfga: 200000, lfg3a: 75000, n_teams: 30 },
  ...over,
})

const row: CockpitRow = {
  id: 'nba|1', sport: 'nba', source: 'sink_a', shadow: true, player: 'Jalen Brunson', player_id: 3934672, team: 'NYK', matchup: 'NYK @ BOS', commence: null,
  mins_to_kick: 60, stat: 'points', side: 'OVER', line: 27.5, book: 'fanduel', american: -110, decimal: 1.91, ev: 0.03, p_fair: 0.5, n_books: 5,
  flag: true, reason: null, all_books: null, recentForm: null,
}

describe('ShotChartBody', () => {
  it('shows the season strip, the zone table with attempt counts and the opponent defence', () => {
    render(<ShotChartBody data={ok()} onSeason={() => {}} />)
    const strip = screen.getByLabelText('Season shooting summary')
    expect(strip).toHaveTextContent('62.5% (150/240)')
    expect(strip).toHaveTextContent('47.0%')                                    // league FG
    const zonesTable = screen.getByRole('table', { name: 'Field goals by zone against the league' })
    expect(within(zonesTable).getByText('Restricted area')).toBeInTheDocument()
    expect(within(zonesTable).queryByText('Mid-range centre')).not.toBeInTheDocument()   // 0 attempts: not listed
    expect(screen.getByRole('table', { name: /BOS defence/ })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Shooting at home and away' })).toBeInTheDocument()
    expect(screen.getByText(/Shot data ends Apr 13, 2025/)).toBeInTheDocument()  // far in the past: stated, not hidden
  })

  it('switches views and lets a tapped cell report its numbers', () => {
    const { container } = render(<ShotChartBody data={ok()} onSeason={() => {}} />)
    expect(container.querySelectorAll('svg[role=img] polygon').length).toBe(3)  // hex view is the default
    fireEvent.click(container.querySelector('svg[role=img] polygon')!)
    expect(screen.getByRole('status')).toHaveTextContent('30 of 40 (75.0%) here; league 65.0%')
    fireEvent.click(screen.getByRole('button', { name: 'Shots' }))
    expect(container.querySelectorAll('svg[role=img] path').length).toBeGreaterThan(8)
    expect(screen.getByRole('button', { name: 'Shots' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Zones' }))
    expect(container.querySelectorAll('svg[role=img] polygon').length).toBe(3)  // grid cells without a zone are skipped
    expect(screen.getByLabelText(/shooting by zone/)).toBeInTheDocument()
  })

  it('offers the other seasons and reports the pick', () => {
    const onSeason = jest.fn()
    render(<ShotChartBody data={ok()} onSeason={onSeason} />)
    fireEvent.change(screen.getByLabelText('Season'), { target: { value: '2023' } })
    expect(onSeason).toHaveBeenCalledWith(2023)
  })

  it('omits the opponent section when there is no opponent', () => {
    render(<ShotChartBody data={ok({ opponent: null })} />)
    expect(screen.queryByText('Opponent defence')).not.toBeInTheDocument()
  })
})

describe('VolumeBars', () => {
  it('needs at least three games', () => {
    const { container } = render(<VolumeBars games={ok().games.slice(0, 2)} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('ShotChartPanel', () => {
  beforeEach(() => apiGetShotChart.mockReset())

  it('asks for the player by name with the opponent from the matchup', async () => {
    apiGetShotChart.mockResolvedValue(ok())
    render(<ShotChartPanel row={row} />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading shot chart')
    await waitFor(() => expect(screen.getByLabelText('Season shooting summary')).toBeInTheDocument())
    expect(apiGetShotChart).toHaveBeenCalledWith({ player: 'Jalen Brunson', season: undefined, opp: 'BOS' })
  })

  it('says so when a player has no shots, the viewer is locked out, or the call fails', async () => {
    apiGetShotChart.mockResolvedValue({ status: 'no_player', seasons: [2024] })
    const a = render(<ShotChartPanel row={row} />)
    await waitFor(() => expect(a.container).toHaveTextContent('No shot data for Jalen Brunson'))
    a.unmount()
    apiGetShotChart.mockResolvedValue({ status: 'locked' })
    const b = render(<ShotChartPanel row={row} />)
    await waitFor(() => expect(b.container).toHaveTextContent('part of full NBA prop research'))
    b.unmount()
    apiGetShotChart.mockRejectedValue(new Error('boom'))
    const c = render(<ShotChartPanel row={row} />)
    await waitFor(() => expect(c.container).toHaveTextContent('could not be loaded'))
  })
})
