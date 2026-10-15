/**
 * /edge multi-sport prop cockpit: sport switcher, master-detail list + inspector, L10 game-log chart against the prop
 * line, opportunity trend, multi-book odds matrix, mobile drawer, and the fallback to the MLB model board when the
 * cockpit API is not available.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { CockpitFeed, CockpitForm, CockpitRow } from '../lib/types'

jest.mock('@/lib/betting-api', () => ({ apiGetEdgeCockpit: jest.fn(() => new Promise(() => {})) }))

import { EdgeClient } from '../app/edge/edge-client'
import { FormSparkline, OddsMatrix, rankQuotes, UsageSparkline } from '../app/edge/cockpit'

const form = (vals: number[], line: number, over: Partial<CockpitForm> = {}): CockpitForm => ({
  line, n_games: vals.length,
  games: vals.map((v, i) => ({ date: `W${i + 1}`, opp: i % 2 ? 'WAS' : 'DAL', val: v, usage: 6 + (i >= vals.length - 3 ? 3 : 0), hit: v > line })),
  l5_rate: 0.8, l10_rate: 0.6, szn_rate: 0.55, szn_avg: 71.4, szn_games: 12,
  usage_label: 'Targets', avg_val: 70, avg_usage: 7, ...over,
})

const VALS = [40, 55, 62, 70, 64, 80, 91, 66, 75, 88]            // vs 63.5: miss miss miss hit hit hit hit hit hit hit => L5 100%, L10 70%

const row = (over: Partial<CockpitRow>): CockpitRow => ({
  id: 'nfl|1', sport: 'nfl', source: 'sink_a', shadow: true, player: 'Test Receiver', player_id: null, team: 'IND',
  matchup: 'IND @ WAS', commence: null, mins_to_kick: 120, stat: 'rec_yds', side: 'OVER', line: 63.5, book: 'fanduel',
  american: 115, decimal: 2.15, ev: 0.071, p_fair: 0.52, n_books: 5, flag: true, reason: null,
  all_books: [
    { book: 'draftkings', over: -108, under: -112, fair: 0.5 },
    { book: 'fanduel', over: 115, under: -135, fair: 0.49 },
    { book: 'novig', over: 110, under: null, fair: null },
  ],
  recentForm: form(VALS, 63.5), ...over,
})

const feed: CockpitFeed = {
  as_of: '2026-10-14T21:00:00Z', sport: 'all', min_ev: 0.02,
  sports: {
    mlb: { status: 'live', count: 1, as_of: null, age_minutes: null, stale: false, validated: true },
    nfl: { status: 'shadow_live', count: 1, as_of: null, age_minutes: 4, stale: false, validated: false },
    nhl: { status: 'shadow_live', count: 1, as_of: null, age_minutes: 4, stale: false, validated: false },
    nba: { status: 'unavailable', count: 0, as_of: null, age_minutes: null, stale: true, validated: false },
  },
  rows: [
    row({}),
    row({ id: 'nhl|1', sport: 'nhl', player: 'Alex Ovechkin', player_id: 8471214, team: 'WSH', matchup: 'MTL @ WSH', stat: 'sog', line: 2.5, book: 'betmgm', american: 112, ev: 0.092,
      recentForm: form([1, 3, 4, 2, 5, 3, 2, 4, 3, 3], 2.5, { usage_label: 'TOI (min)' }), all_books: null }),
    row({ id: 'mlb|model|1', sport: 'mlb', source: 'model', shadow: false, player: 'Slugger Jones', stat: 'home_runs', line: 0.5, book: 'draftkings', american: 380, ev: 0.04,
      p_fair: 0.24, all_books: null, system: 'HR', recentForm: form([0, 1, 0, 0, 1, 0, 0, 0, 1, 0], 0.5, { usage_label: null }),
      ev_la: [{ ev: 101, la: 24, hit: true }, { ev: 88, la: 5, hit: false }], spray: [{ x: 100, y: 120, hit: true }, { x: 150, y: 90, hit: false }] }),
  ],
}

const inList = () => within(screen.getByRole('list', { name: 'Prop candidates' }))

describe('sport switcher', () => {
  it('offers ALL SPORTS | MLB | NFL | NHL | NBA with live counts and filters the list', () => {
    render(<EdgeClient picks={[]} updated="now" cockpit={feed} />)
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map(t => t.textContent)).toEqual(['ALL SPORTS3', 'MLB1', 'NFL1', 'NHL1', 'NBA0'])
    expect(inList().getByText('Test Receiver')).toBeInTheDocument()
    expect(inList().getByText('Alex Ovechkin')).toBeInTheDocument()
    expect(inList().getByText('Slugger Jones')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: /^NFL/ }))
    expect(screen.getByRole('tab', { name: /^NFL/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByText('Alex Ovechkin')).toBeNull()
    expect(inList().getByText('Test Receiver')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: /^NBA/ }))
    expect(screen.getByText(/NBA: coming soon/)).toBeInTheDocument()
    expect(screen.getByText('No props match right now')).toBeInTheDocument()
  })

  it('filters by market chip and by player search', () => {
    render(<EdgeClient picks={[]} updated="now" cockpit={feed} />)
    const markets = screen.getByRole('group', { name: 'Market' })
    fireEvent.click(within(markets).getByRole('button', { name: /SHOTS ON GOAL/ }))
    expect(inList().getByText('Alex Ovechkin')).toBeInTheDocument()
    expect(screen.queryByText('Test Receiver')).toBeNull()
    fireEvent.click(within(markets).getByRole('button', { name: /ALL MARKETS/ }))
    fireEvent.change(screen.getByLabelText('Search player or team'), { target: { value: 'slugger' } })
    expect(screen.queryByText('Test Receiver')).toBeNull()
    expect(inList().getByText('Slugger Jones')).toBeInTheDocument()
  })
})

describe('prop list cards', () => {
  it('show line, target book + price, +EV badge and the L10 hit-rate pill', () => {
    render(<EdgeClient picks={[]} updated="now" cockpit={feed} />)
    fireEvent.click(screen.getByRole('tab', { name: /^NFL/ }))
    const card = inList().getByText('Over 63.5 Rec Yds').closest('button') as HTMLElement
    expect(card).toHaveTextContent('Test Receiver')
    expect(card).toHaveTextContent('FanDuel')
    expect(card).toHaveTextContent('+115')
    expect(card).toHaveTextContent('+7.1%')
    expect(card).toHaveTextContent('L10: 70%')
  })

  it('count the Under side when the flagged quote is the under', () => {
    const f: CockpitFeed = { ...feed, rows: [row({ side: 'UNDER', american: -105 })] }
    render(<EdgeClient picks={[]} updated="now" cockpit={f} />)
    const card = inList().getByText('Under 63.5 Rec Yds').closest('button') as HTMLElement
    expect(card).toHaveTextContent('L10: 30%')                                   // 3 of 10 games stayed under 63.5
  })

  it('show an em-dash style placeholder when no game log exists', () => {
    const f: CockpitFeed = { ...feed, rows: [row({ recentForm: null })] }
    render(<EdgeClient picks={[]} updated="now" cockpit={f} />)
    expect(screen.getByText('L10: --')).toBeInTheDocument()
  })
})

describe('inspector', () => {
  it('shows the price callout against consensus fair, the game log, the volume trend and the odds matrix', () => {
    render(<EdgeClient picks={[]} updated="now" cockpit={feed} />)
    fireEvent.click(screen.getByRole('tab', { name: /^NFL/ }))
    const aside = screen.getByRole('complementary', { name: 'Prop inspector' })
    expect(within(aside).getByRole('heading', { level: 2 })).toHaveTextContent('Test Receiver')
    expect(aside).toHaveTextContent('Best Price: +115 on FanDuel vs -108 Consensus Fair')
    expect(aside).toHaveTextContent('+7.1%')
    expect(aside).toHaveTextContent(/L10 GAME LOG VS LINE/)
    expect(aside).toHaveTextContent(/OPPORTUNITY AND VOLUME TREND/)
    expect(aside).toHaveTextContent(/MULTI-BOOK ODDS MATRIX/)
    expect(aside).toHaveTextContent('De-vigged consensus of 5 reference books.')
    expect(aside).not.toHaveTextContent(/SHADOW|unvalidated|Evidence/i)
  })

  it('selecting another card swaps the inspector', () => {
    render(<EdgeClient picks={[]} updated="now" cockpit={feed} />)
    fireEvent.click(inList().getByText('Over 2.5 Shots on Goal').closest('button') as HTMLElement)
    const aside = screen.getByRole('complementary', { name: 'Prop inspector' })
    expect(within(aside).getByRole('heading', { level: 2 })).toHaveTextContent('Alex Ovechkin')
    expect(aside.querySelector('img')!.getAttribute('src')).toBe('https://assets.nhle.com/mugs/nhl/latest/8471214.png')
  })

  it('renders the Statcast scatter and spray chart for MLB rows instead of the volume chart', () => {
    render(<EdgeClient picks={[]} updated="now" cockpit={feed} />)
    fireEvent.click(screen.getByRole('tab', { name: /^MLB/ }))
    const aside = screen.getByRole('complementary', { name: 'Prop inspector' })
    expect(within(aside).getByRole('img', { name: 'exit velocity versus launch angle' })).toBeInTheDocument()
    expect(within(aside).getByRole('img', { name: /batted-ball spray chart/ })).toBeInTheDocument()
    expect(aside).toHaveTextContent(/CONTACT QUALITY AND BATTED BALLS/)
    expect(aside).not.toHaveTextContent(/OPPORTUNITY AND VOLUME/)
  })

  it('degrades each panel when the game log is missing', () => {
    const f: CockpitFeed = { ...feed, rows: [row({ recentForm: null, all_books: null })] }
    render(<EdgeClient picks={[]} updated="now" cockpit={f} />)
    const aside = screen.getByRole('complementary', { name: 'Prop inspector' })
    expect(aside).toHaveTextContent('No game log is available for this player and stat yet.')
    expect(aside).toHaveTextContent('No volume history for this player yet.')
    expect(aside).toHaveTextContent('Only the flagged quote was captured for this line')
  })

  it('opens as a sheet on small screens and closes again', () => {
    render(<EdgeClient picks={[]} updated="now" cockpit={feed} />)
    const aside = screen.getByRole('complementary', { name: 'Prop inspector' })
    expect(aside.className).not.toContain('is-open')
    fireEvent.click(inList().getByText('Over 2.5 Shots on Goal').closest('button') as HTMLElement)
    expect(aside.className).toContain('is-open')
    fireEvent.click(within(aside).getByRole('button', { name: 'Close inspector' }))
    expect(aside.className).not.toContain('is-open')
  })
})

describe('FormSparkline', () => {
  it('draws ten bars against a dashed line, green when the over cleared and red when it missed, with four stat pills', () => {
    const { container } = render(<FormSparkline form={form(VALS, 63.5)} under={false} />)
    const svg = screen.getByRole('img', { name: /Last 10 games versus the 63.5 line: over cleared in 7 of 10/ })
    expect(svg).toBeInTheDocument()
    expect(container.querySelectorAll('rect[fill="#57F287"]')).toHaveLength(7)
    expect(container.querySelectorAll('rect[fill="#ef4444"]')).toHaveLength(3)
    expect(container.querySelector('line[stroke-dasharray]')).not.toBeNull()
    for (const [label, value] of [['L5 HIT%', '100%'], ['L10 HIT%', '70%'], ['SEASON AVG', '71.4'], ['LINE', '63.5']]) {
      expect(screen.getByText(label).previousElementSibling).toHaveTextContent(value)
    }
  })

  it('flips the colors for an under', () => {
    const { container } = render(<FormSparkline form={form(VALS, 63.5)} under />)
    expect(container.querySelectorAll('rect[fill="#57F287"]')).toHaveLength(3)
    expect(container.querySelectorAll('rect[fill="#ef4444"]')).toHaveLength(7)
    expect(screen.getByText('L10 HIT%').previousElementSibling).toHaveTextContent('30%')
  })

  it('does not color bars when the line is unknown', () => {
    const { container } = render(<FormSparkline form={form(VALS, 63.5, { line: null })} under={false} />)
    expect(container.querySelectorAll('rect[fill="#57F287"]')).toHaveLength(0)
    expect(container.querySelector('line[stroke-dasharray]')).toBeNull()
    expect(screen.getByText('L10 HIT%').previousElementSibling).toHaveTextContent('--')
  })
})

describe('UsageSparkline', () => {
  it('reports the opportunity trend in the metric of the sport', () => {
    render(<UsageSparkline form={form(VALS, 63.5)} />)
    expect(screen.getByRole('img', { name: /Targets over the last 10 games, trending up/ })).toBeInTheDocument()
    expect(screen.getByText('TREND').previousElementSibling).toHaveTextContent('+50%')   // 9 over the last 3 vs 6 before
    expect(screen.getByText(/Targets are/)).toHaveTextContent('trending up')
  })

  it('asks for history instead of drawing a line through one point', () => {
    render(<UsageSparkline form={form([50], 40)} />)
    expect(screen.getByText(/No targets history/)).toBeInTheDocument()
  })
})

describe('OddsMatrix', () => {
  it('ranks books by the flagged side price, highlights the best and tags the target book', () => {
    render(<OddsMatrix r={row({})} />)
    const rows = screen.getAllByRole('row').slice(1)
    expect(rows.map(r => ['FanDuel', 'Novig', 'DraftKings'].findIndex(n => r.textContent?.includes(n)))).toEqual([0, 1, 2])   // +115, +110, -108
    expect(rows[0]).toHaveAttribute('data-best', 'true')
    expect(rows[1]).not.toHaveAttribute('data-best')
    expect(rows[0]).toHaveTextContent('TARGET')
    expect(rows[1]).toHaveTextContent('+110')
    expect(rows[1].textContent).toContain('\u2013')                                          // novig has no under price
  })

  it('ranks by the under price for an under', () => {
    render(<OddsMatrix r={row({ side: 'UNDER', american: -105 })} />)
    const first = screen.getAllByRole('row')[1]
    expect(first).toHaveTextContent('DraftKings')                                            // -112 beats -135; novig has none
    expect(first).toHaveAttribute('data-best', 'true')
  })

  it('rankQuotes puts unpriced books last', () => {
    const r = rankQuotes([{ book: 'a', over: null, under: -110, fair: null }, { book: 'b', over: -150, under: null, fair: null }, { book: 'c', over: 100, under: null, fair: null }], false)
    expect(r.map(x => x.quote.book)).toEqual(['c', 'b', 'a'])
    expect(r.map(x => x.best)).toEqual([true, false, false])
  })
})

describe('without a cockpit feed', () => {
  it('opens on the MLB model board and hides the sport tabs that cannot work without a feed', () => {
    render(<EdgeClient picks={[]} updated="now" cockpit={null} />)
    expect(screen.queryAllByRole('tab', { name: /^(NFL|NHL|NBA|ALL SPORTS)/ })).toHaveLength(0)
    expect(screen.getByText('No model picks scored yet today.')).toBeInTheDocument()
  })

  it('keeps the model board reachable next to the cockpit for MLB', () => {
    render(<EdgeClient picks={[]} updated="now" cockpit={feed} />)
    fireEvent.click(screen.getByRole('tab', { name: /^MLB/ }))
    fireEvent.click(screen.getByRole('button', { name: 'MODEL BOARD' }))
    expect(screen.getByText('No model picks scored yet today.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'PROP COCKPIT' }))
    expect(inList().getByText('Slugger Jones')).toBeInTheDocument()
  })
})
