/**
 * NFL / NHL / NBA are first-class: ONE projection board component shared with MLB, the market-prices table, the systems
 * footer, the methodology card, and the pricing page's three plans. Pure render tests; no network.
 */
import fs from 'fs'
import path from 'path'
import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { EvBookOutlierRow, SportProjectionRow, SportProjections } from '../lib/types'

jest.mock('next/link', () => ({ __esModule: true, default: ({ children, href, 'aria-current': current }: { children: React.ReactNode; href: string; 'aria-current'?: 'page' }) => <a href={href} aria-current={current}>{children}</a> }))
jest.mock('next/dynamic', () => ({ __esModule: true, default: () => () => <button>checkout</button> }))

import { MarketPrices } from '../components/picks/market-prices'
import { SportProjectionsBoard } from '../components/picks/sport-projections-board'
import { SportTabs } from '../components/picks/sport-tabs'
import { SystemMethodology } from '../components/picks/system-methodology'
import { PricingSection, PLANS, ANNUAL_SAVINGS_PCT, ANNUAL_PER_MONTH } from '../components/landing/pricing'
import { boardStatusText, fromSportProjections, sidePrice } from '../lib/board-rows'
import { getSportSystemBySlug, getSystemsBySport } from '../lib/pick-systems'
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

const board = (rows: SportProjectionRow[], sport: 'nba' | 'nfl' | 'nhl' = 'nba') => <SportProjectionsBoard sport={sport} rows={fromSportProjections(rows)} />

describe('unified projections board', () => {
  it('renders score and tier, player and team, game, market and line, model, market fair, form, best price and edge', () => {
    render(board([row()]))
    const tr = screen.getByText('Jo Doe').closest('tr')!
    const t = within(tr)
    expect(t.getByText('62')).toBeInTheDocument()
    expect(t.getByText('LEAN')).toBeInTheDocument()
    const cells = [...tr.querySelectorAll('td')].map(td => td.textContent ?? '')
    expect(cells[1]).not.toContain('LAL')                   // the player cell is just the name
    expect(cells[2]).toBe('LAL')                            // the team column: his own team only, no game or opponent
    expect(t.getByText('Over 24.5')).toBeInTheDocument()
    expect(t.getByText('26.3')).toBeInTheDocument()
    expect(t.getByText('60%')).toBeInTheDocument()
    expect(t.getByText(/52% -108/)).toBeInTheDocument()
    expect(t.getByText(/6 bk/)).toBeInTheDocument()
    expect(t.getByText('L5 80%')).toBeInTheDocument()
    expect(t.getByText('L10 70%')).toBeInTheDocument()
    expect(t.getByText('+120')).toBeInTheDocument()
    expect(t.getByText('FanDuel')).toBeInTheDocument()
    expect(t.getByText('+8.0%')).toBeInTheDocument()
    expect(t.getByText('+5.0%')).toBeInTheDocument()
    expect(t.getByText('MODEL + MARKET')).toBeInTheDocument()
    expect(document.querySelectorAll('thead').length).toBe(1)
    expect(screen.getAllByRole('columnheader').map(h => h.textContent)).toEqual(
      ['SCORE', 'PLAYER', 'TEAM', 'MARKET & LINE', 'MODEL', 'MARKET FAIR', 'FORM', 'BEST PRICE', 'MODEL EDGE', 'PRICE EV'])
  })

  it('the team column falls back to the matchup only when the feed does not name the player\'s team', () => {
    render(board([row({ team: null })]))
    const cell = [...screen.getByText('Jo Doe').closest('tr')!.querySelectorAll('td')][2].textContent ?? ''
    expect(cell).toContain('BOS@LAL')
  })

  it('has no controls of its own: the filter bar above the table is the only set', () => {
    render(board([row(), rotation]))
    expect(screen.queryByRole('searchbox')).toBeNull()
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.queryByRole('group')).toBeNull()
  })

  it('hit rates and price follow the listed side: an under pick shows the share that stayed under and the under price', () => {
    render(board([row({ side: 'under', l5_hit_rate: 0.8, l10_hit_rate: 0.7 })]))
    const t = within(screen.getByText('Jo Doe').closest('tr')!)
    expect(t.getByText('L5 20%')).toBeInTheDocument()
    expect(t.getByText('L10 30%')).toBeInTheDocument()
    expect(t.getByText('Under 24.5')).toBeInTheDocument()
    expect(t.getByText('-110')).toBeInTheDocument()
    expect(t.getByText('BetMGM')).toBeInTheDocument()
  })

  it('a row with no line shows its projection and the player\'s own averages, never fake odds or hit rates', () => {
    render(board([row(), rotation]))
    const rot = within(screen.getByText('Sam Rotation').closest('tr')!)
    expect(rot.getByText('7.2')).toBeInTheDocument()
    expect(rot.getByText('(no line yet)')).toBeInTheDocument()
    expect(rot.getByText(/L10 avg 26\.0/)).toBeInTheDocument()
    expect(rot.getByText(/szn 25\.1/)).toBeInTheDocument()
    expect(rot.queryByText('MODEL + MARKET')).toBeNull()
    expect(rot.queryByText(/%/)).toBeNull()
  })

  it('leaves out a column nothing fills instead of printing a column of dashes', () => {
    render(board([rotation]))
    expect(screen.getAllByRole('columnheader').map(h => h.textContent)).not.toContain('MARKET FAIR')
    expect(screen.getAllByRole('columnheader').map(h => h.textContent)).not.toContain('RESULT')
    expect(screen.getAllByRole('columnheader').map(h => h.textContent)).toContain('FORM')
  })

  it('shows a RESULT column when a settled pick is on the board', () => {
    const rows = fromSportProjections([row()]).map(r => ({ ...r, result: 'win' as const }))
    render(<SportProjectionsBoard sport="nba" rows={rows} />)
    expect(screen.getByRole('columnheader', { name: 'RESULT' })).toBeInTheDocument()
    expect(screen.getByText('WIN')).toBeInTheDocument()
  })

  it('explains an empty board instead of leaving a blank page', () => {
    const { rerender } = render(<SportProjectionsBoard sport="nfl" rows={[]} emptyBody="The projection board could not be loaded." />)
    expect(screen.getByText(/could not be loaded/i)).toBeInTheDocument()
    rerender(<SportProjectionsBoard sport="nfl" rows={[]} />)
    expect(screen.getByText(/No NFL rows match/i)).toBeInTheDocument()
  })

  it('shows the freshness line, as a warning when the lines are stale', () => {
    render(<SportProjectionsBoard sport="nba" rows={fromSportProjections([row()])} status="Lines are 10.0 h old" statusWarn />)
    expect(screen.getByRole('status')).toHaveTextContent('Lines are 10.0 h old')
  })

  it('side price follows the model side; status text is plain language', () => {
    expect(sidePrice(row({ side: 'under' }))).toEqual({ odds: -110, book: 'betmgm', side: 'under' })
    expect(sidePrice(row({ side: null }))).toMatchObject({ odds: 120, side: 'over' })
    expect(boardStatusText(data([row()]))).toBe('Lines updated 12 min ago')
    expect(boardStatusText(data([], { status: 'awaiting_lines', stale_gamelog: true, gamelog_latest: '2026-06-13' }))).toContain('off-season')
    expect(boardStatusText(data([], { status: 'unavailable' }))).toMatch(/not available/)
  })
})

describe('sport tabs', () => {
  it.each(['mlb', 'nfl', 'nhl', 'nba'] as const)('%s shows its name, its system count and the same three tabs', sport => {
    render(<SportTabs sport={sport} active="projections" />)
    expect(screen.getByText(SPORT_PILLARS[sport].shortName)).toBeInTheDocument()
    expect(screen.getByText(new RegExp(`${getSystemsBySport(sport).length} Systems`))).toBeInTheDocument()
    expect(screen.getAllByRole('link').map(a => a.textContent)).toEqual(['PICKS & +EV', 'PROJECTIONS', 'SYSTEMS'])
    expect(screen.getByRole('link', { name: 'PROJECTIONS' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'PICKS & +EV' })).toHaveAttribute('href', `/picks/${sport}`)
    expect(screen.getByRole('link', { name: 'PROJECTIONS' })).toHaveAttribute('href', `/picks/${sport}/projections`)
    expect(screen.getByRole('link', { name: 'SYSTEMS' })).toHaveAttribute('href', `/picks/${sport}/systems`)
  })
  it('the header counts match the specified systems per sport', () => {
    expect(['mlb', 'nfl', 'nhl', 'nba'].map(s => getSystemsBySport(s as 'mlb').length)).toEqual([10, 10, 7, 10])
  })
})

const outlier = (player: string, stat = 'sog') => ({
  game_key: 'x:2026-10-15:AAA@BBB', player, player_norm: player.toLowerCase(), stat, point: 2.5, side: 'over', book: 'fanduel',
  american: 110, p_fair: 0.5, n_books: 5, ev: 0.06, flag: true, reason: null, player_id: null,
}) as unknown as EvBookOutlierRow

describe('market prices', () => {
  it.each(['nfl', 'nhl', 'nba'] as const)('%s has one layout: a preview note and either the outlier table or the same empty state', sport => {
    const { unmount } = render(<MarketPrices sport={sport} rows={[]} block={null} />)
    expect(screen.getByText('PREVIEW.')).toBeInTheDocument()
    expect(screen.getByText('No prices beating the market')).toBeInTheDocument()
    unmount()
    render(<MarketPrices sport={sport} rows={[outlier('Pat Mahomes')]} block={null} />)
    expect(screen.getByText('Pat Mahomes', { exact: false })).toBeInTheDocument()
    expect(screen.queryByText(/Kalshi vs sportsbooks/i)).toBeNull()
  })
  it('a cut list says how many rows are withheld and links to plans', () => {
    render(<MarketPrices sport="nba" rows={[outlier('Pat Mahomes')]} block={null} locked={12} />)
    expect(screen.getAllByRole('note').some(n => /12 more NBA market prices today/.test(n.textContent ?? ''))).toBe(true)
    expect(screen.getByRole('link', { name: /plans/i })).toHaveAttribute('href', '/pricing')
  })
})

describe('system methodology and the validation data', () => {
  it('shows the research model numbers, the live-engine caveat and that projections have no graded record', () => {
    render(<SystemMethodology system={getSportSystemBySlug('nba', 'pts')!} />)
    expect(screen.getByText('4.547 vs 4.634')).toBeInTheDocument()
    expect(screen.getByText('+0.07 (+3.0)')).toBeInTheDocument()
    expect(screen.getByText(/does not model injuries/i)).toBeInTheDocument()
    expect(screen.getByText(/No graded history is published for NBA projections/)).toBeInTheDocument()
  })
  it('a stat no study covered prints dashes, and the PP-points gap is stated instead of a made-up projection', () => {
    const { unmount } = render(<SystemMethodology system={getSportSystemBySlug('nba', 'steals')!} />)
    expect(screen.getByText('No trained model; unvalidated')).toBeInTheDocument()
    expect(screen.getByText('No study has compared this stat with prices.')).toBeInTheDocument()
    unmount()
    render(<SystemMethodology system={getSportSystemBySlug('nhl', 'pp-points')!} />)
    expect(screen.getByText('DATA GAP.')).toBeInTheDocument()
    expect(screen.getByText(/no power-play assists column/i)).toBeInTheDocument()
  })
  it('NBA validation numbers are the study\'s, and a metric no study produced is null', () => {
    const pts = SPORT_MODEL_SPECS.nba.models.find(m => m.key === 'points')!.validation!
    expect([pts.mae, pts.baselineMae, pts.crps, pts.brier, pts.ece]).toEqual([4.547, 4.634, 3.179, 0.2424, 0.0093])
    expect(SPORT_MODEL_SPECS.nfl.models.filter(m => m.validation).every(m => m.validation!.mae === null && m.validation!.brier === null)).toBe(true)
    expect(SPORT_MODEL_SPECS.nhl.models.filter(m => m.trained).map(m => m.key)).toEqual(['sog'])
    expect(SPORT_MODEL_SPECS.nba.models.find(m => m.key === 'rebounds')!.blend).toEqual({ c: 0.19, t: 7.71 })
    expect(SPORT_MODEL_SPECS.nba.models.find(m => m.key === 'steals')!.blend).toBeNull()
  })
  it('every registered projection system has a spec entry, so nothing in the footer is blank by accident', () => {
    for (const sport of ['nfl', 'nhl', 'nba'] as const)
      for (const s of getSystemsBySport(sport)) expect(SPORT_MODEL_SPECS[sport].models.map(m => m.key)).toContain(s.statKey)
  })
  it('no pillar, market or badge still reads as a placeholder', () => {
    for (const p of Object.values(SPORT_PILLARS)) {
      expect(p.status).toBe('live')
      expect(p.seasonBadge).toMatch(/\+EV SCANNER$/)
      for (const m of p.markets) expect(m.status).toBe(p.slug === 'mlb' ? m.status : m.key === 'pp_points' ? 'Data gap' : 'Active')
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
