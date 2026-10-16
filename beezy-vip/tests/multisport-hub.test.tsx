/**
 * NFL / NHL / NBA are first-class hubs: a daily projection board, confluence badges, spec-and-validation tables
 * instead of "not tracked yet", and the pricing page's three plans. Pure render tests; no network.
 */
import fs from 'fs'
import path from 'path'
import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { EvBookOutlierRow, SportProjectionRow, SportProjections } from '../lib/types'

jest.mock('next/link', () => ({ __esModule: true, default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }))
jest.mock('next/dynamic', () => ({ __esModule: true, default: () => () => <button>checkout</button> }))

import { SportHubClient } from '../app/sports/[sport]/sport-hub-client'
import { ProjectionsBoard, boardStatusText, sidePrice } from '../app/sports/[sport]/projections-board'
import { PricingSection, PLANS, ANNUAL_SAVINGS_PCT, ANNUAL_PER_MONTH } from '../components/landing/pricing'
import { SPORT_MODEL_SPECS } from '../lib/prop-model-specs'
import { SPORT_PILLARS } from '../lib/sports'

const row = (o: Partial<SportProjectionRow> = {}): SportProjectionRow => ({
  sport: 'nba', stat: 'points', stat_label: 'Points', player: 'Jo Doe', player_norm: 'jo doe', team: 'LAL', opp: 'BOS',
  game_key: 'nba:2026-10-15:BOS@LAL', commence: '2026-10-15T23:00:00Z', mins_to_start: 300, has_line: true, line: 24.5,
  consensus_line: 24.5, side: 'over', proj_mean: 26.3, p_over_model: 0.6, p_model_side: 0.6, p_mkt_side: 0.52,
  l5_hit_rate: 0.8, l10_hit_rate: 0.7, szn_rate: 0.6, szn_avg: 25.1, avg_l10: 26, usage_label: 'Minutes', n_games: 20,
  best_over_odds: 120, best_over_book: 'fanduel', best_under_odds: -110, best_under_book: 'betmgm', p_over_market: 0.52,
  n_books: 6, model_edge: 0.08, market_ev: 0.05, confluence: true, edge_capped: false, beezy_score: 62, score_basis: 'confluence',
  validated: false, engine: 'empirical_ewm_v1', headshot_id: null, ...o,
})

const data = (rows: SportProjectionRow[], o: Partial<SportProjections> = {}): SportProjections => ({
  sport: 'nba', engine: 'empirical_ewm_v1', validated: false, status: 'live', stale: false, as_of: '2026-10-15T16:00:00Z',
  age_minutes: 4, lines_age_minutes: 12, gamelog_latest: '2026-10-14', stale_gamelog: false, stats: ['points', 'rebounds'],
  count: rows.length, rows, summary: {}, ...o,
})

const rotation = row({ player: 'Sam Rotation', player_norm: 'sam rotation', has_line: false, line: null, consensus_line: null,
  side: null, p_over_model: null, p_model_side: null, p_mkt_side: null, best_over_odds: null, best_over_book: null,
  best_under_odds: null, best_under_book: null, model_edge: null, market_ev: null, confluence: false, beezy_score: 0,
  score_basis: 'none', l5_hit_rate: null, l10_hit_rate: null, stat: 'rebounds', stat_label: 'Rebounds', proj_mean: 7.2 })

describe('projection board', () => {
  it('renders player, matchup, line, projection, hit rates, best price, edges, score and the confluence badge', () => {
    render(<ProjectionsBoard sport="nba" data={data([row()])} />)
    const tr = screen.getByText('Jo Doe').closest('tr')!
    const t = within(tr)
    expect(t.getByText('MODEL + MARKET')).toBeInTheDocument()
    expect(t.getByText('LAL v BOS')).toBeInTheDocument()
    expect(t.getByText('O 24.5')).toBeInTheDocument()
    expect(t.getByText('26.3')).toBeInTheDocument()
    expect(t.getByText('60%')).toBeInTheDocument()
    expect(t.getByText('80%')).toBeInTheDocument()
    expect(t.getByText('70%')).toBeInTheDocument()
    expect(t.getByText('+120')).toBeInTheDocument()
    expect(t.getByText('+8.0%')).toBeInTheDocument()
    expect(t.getByText('+5.0%')).toBeInTheDocument()
    expect(t.getByText('62')).toBeInTheDocument()
    expect(screen.getByText('UNVALIDATED')).toBeInTheDocument()
    expect(document.querySelectorAll('thead').length).toBe(1)
  })
  it('filters by market and shows rotation rows (no line) with dashes, never fake numbers', () => {
    render(<ProjectionsBoard sport="nba" data={data([row(), rotation])} />)
    expect(screen.getByText('Sam Rotation')).toBeInTheDocument()
    const rot = within(screen.getByText('Sam Rotation').closest('tr')!)
    expect(rot.getByText('7.2')).toBeInTheDocument()
    expect(rot.queryByText('MODEL + MARKET')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Points' }))
    expect(screen.queryByText('Sam Rotation')).toBeNull()
    expect(screen.getByText('Jo Doe')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'All' }))
    expect(screen.getByText('Sam Rotation')).toBeInTheDocument()
  })
  it('explains an unavailable or empty board instead of leaving a blank page', () => {
    const { rerender } = render(<ProjectionsBoard sport="nfl" data={null} />)
    expect(screen.getByText(/could not be loaded/i)).toBeInTheDocument()
    rerender(<ProjectionsBoard sport="nfl" data={data([], { status: 'idle' })} />)
    expect(screen.getByText(/No NFL games are scheduled/i)).toBeInTheDocument()
  })
  it('side price follows the model side; status text is plain language', () => {
    expect(sidePrice(row({ side: 'under' }))).toEqual({ odds: -110, book: 'betmgm', side: 'under' })
    expect(sidePrice(row({ side: null }))).toMatchObject({ odds: 120, side: 'over' })
    expect(boardStatusText(data([row()]))).toBe('Lines updated 12 min ago')
    expect(boardStatusText(data([], { status: 'awaiting_lines', stale_gamelog: true, gamelog_latest: '2026-06-13' }))).toContain('off-season')
    expect(boardStatusText(data([], { status: 'unavailable' }))).toMatch(/not available/)
  })
})

const hubProps = (sport: 'nfl' | 'nhl' | 'nba', projections: SportProjections | null, outliers: EvBookOutlierRow[] = []) => ({
  sport, projections, outliers, block: null,
})

const outlier = (player: string) => ({
  game_key: 'x:2026-10-15:AAA@BBB', player, player_norm: player.toLowerCase(), stat: 'sog', point: 2.5, side: 'over', book: 'fanduel',
  american: 110, p_fair: 0.5, n_books: 5, ev: 0.06, flag: true, reason: null, player_id: null,
}) as unknown as EvBookOutlierRow

describe('NFL / NHL / NBA hubs', () => {
  it.each(['nfl', 'nhl', 'nba'] as const)('%s hub opens on its own projection board with the same three tabs, never a coming-soon empty state', (sport) => {
    render(<SportHubClient {...hubProps(sport, data([row({ sport })]))} />)
    expect(screen.getAllByRole('tab').map(t => t.textContent)).toEqual(['PROJECTIONS', 'MARKET PRICES'])
    expect(screen.getByRole('tab', { name: 'PROJECTIONS' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Jo Doe')).toBeInTheDocument()
    expect(screen.queryByText(/coming soon/i)).toBeNull()
    expect(screen.getByText('LIVE')).toBeInTheDocument()
  })
  it.each(['nfl', 'nhl', 'nba'] as const)('%s market prices tab has one layout: a preview note and either the outlier table or the same empty state', (sport) => {
    const { unmount } = render(<SportHubClient {...hubProps(sport, null)} />)
    fireEvent.click(screen.getByRole('tab', { name: 'MARKET PRICES' }))
    expect(screen.getByText('PREVIEW.')).toBeInTheDocument()
    expect(screen.getByText('No prices beating the market')).toBeInTheDocument()
    unmount()
    render(<SportHubClient {...hubProps(sport, null, [outlier('Pat Mahomes')])} />)
    fireEvent.click(screen.getByRole('tab', { name: 'MARKET PRICES' }))
    expect(screen.getByText('Pat Mahomes', { exact: false })).toBeInTheDocument()
    expect(screen.queryByText(/Kalshi vs sportsbooks/i)).toBeNull()
  })
  it('a cut board says how many rows are withheld and links to plans', () => {
    render(<SportHubClient {...hubProps('nba', data([row({ sport: 'nba' })]))} lockedProjections={12} />)
    expect(screen.getByRole('note')).toHaveTextContent(/12 more NBA projections today/)
    expect(screen.getByRole('link', { name: /plans/i })).toHaveAttribute('href', '/pricing')
  })
  it.each(['nfl', 'nhl', 'nba'] as const)('%s systems footer is on every tab: real specification and validation, not "not tracked yet"', (sport) => {
    render(<SportHubClient {...hubProps(sport, null)} />)
    expect(screen.queryByRole('tab', { name: 'MODELS' })).toBeNull()
    expect(screen.getByRole('heading', { name: new RegExp(`${sport} systems`, 'i') })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'MARKET PRICES' }))
    expect(screen.getByRole('heading', { name: new RegExp(`${sport} systems`, 'i') })).toBeInTheDocument()
    expect(screen.queryByText(/not tracked yet/i)).toBeNull()
    for (const m of SPORT_MODEL_SPECS[sport].models) expect(screen.getByText(m.label)).toBeInTheDocument()
    expect(screen.getByText(/What the daily board runs/i)).toBeInTheDocument()
  })
  it('NBA validation numbers are the study\'s, and a metric no study produced is a dash', () => {
    const pts = SPORT_MODEL_SPECS.nba.models.find(m => m.key === 'points')!.validation!
    expect([pts.mae, pts.baselineMae, pts.crps, pts.brier, pts.ece]).toEqual([4.547, 4.634, 3.179, 0.2424, 0.0093])
    expect(SPORT_MODEL_SPECS.nfl.models.every(m => m.validation!.mae === null && m.validation!.brier === null)).toBe(true)
    expect(SPORT_MODEL_SPECS.nhl.models.filter(m => m.trained).map(m => m.key)).toEqual(['sog'])
  })
  it('no hub, market or badge still reads as a placeholder', () => {
    for (const p of Object.values(SPORT_PILLARS)) {
      expect(p.status).toBe('live')
      expect(p.seasonBadge).toMatch(/\+EV SCANNER$/)
      for (const m of p.markets) expect(m.status).toBe(p.slug === 'mlb' ? m.status : 'Active')
    }
  })
})

describe('pricing', () => {
  it('shows the three plans at the specified prices with the real annual savings', () => {
    render(<PricingSection />)
    expect(screen.getByText('$19')).toBeInTheDocument()
    expect(screen.getByText('$39')).toBeInTheDocument()
    expect(screen.getByText('$299')).toBeInTheDocument()
    expect(PLANS.single.price).toBe(19)
    expect(ANNUAL_SAVINGS_PCT).toBe(36)
    expect(ANNUAL_PER_MONTH.toFixed(2)).toBe('24.92')
    expect(screen.getByText(/save 36%/)).toBeInTheDocument()
    expect(screen.getByText(/HOW THE DUAL-ENGINE TERMINAL WORKS/)).toBeInTheDocument()
    expect(screen.getByText(/STRONG \(70\+\)/)).toBeInTheDocument()      // the published v2 cut, not the old 65
  })
  it('the single-sport league picker changes the plan copy and exposes four leagues', () => {
    render(<PricingSection />)
    const group = screen.getByRole('radiogroup', { name: 'League' })
    expect(within(group).getAllByRole('radio').map(b => b.textContent)).toEqual(['NFL', 'NBA', 'NHL', 'MLB'])
    expect(screen.getByText(/#nfl-card/)).toBeInTheDocument()
    fireEvent.click(within(group).getByRole('radio', { name: 'NHL' }))
    expect(screen.getByText(/#nhl-card/)).toBeInTheDocument()
    expect(screen.queryByText(/#nfl-card/)).toBeNull()
  })
  it('stays pre-launch (waitlist CTAs) and the card styling has no glow or gradient', () => {
    render(<PricingSection />)
    expect(screen.getAllByText('Join waitlist')).toHaveLength(3)
    const src = fs.readFileSync(path.resolve(__dirname, '../components/landing/pricing.tsx'), 'utf8')
    expect(src).not.toMatch(/linear-gradient|glow|boxShadow: [^']*rgba\((?!0, 0, 0)/)
  })
})
