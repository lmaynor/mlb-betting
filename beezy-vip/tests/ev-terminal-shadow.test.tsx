/**
 * +EV terminal: NFL Sink A, NHL and NBA rows are SHADOW (unvalidated). They must render without a Beezy score or a Kelly
 * stake, the NHL/NBA sport filters must be live, and the source files that carry typographic characters must stay ASCII
 * (the repo is edited from Windows PowerShell / cp1252 shells that turn raw multibyte characters into mojibake).
 */
import fs from 'fs'
import path from 'path'
import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { EvBookOutlierRow, EvFeed, EvMlbRow } from '../lib/types'

jest.mock('@/lib/betting-api', () => ({ apiGetEvFeed: jest.fn(() => new Promise(() => {})) }))
jest.mock('next/link', () => ({ __esModule: true, default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }))

import { EvClient } from '../app/ev/ev-client'

const mlb: EvMlbRow = {
  sport: 'mlb', id: 7, game_date: '2026-10-14', game_pk: 1, player: 'Pat Pitcher', away_team: 'NYY', home_team: 'BOS',
  bet_type: 'K_OVER_6.5', model_prob: 0.58, market_prob: 0.5, edge: 0.06, kelly_pct: 0.02, odds: 110, stake: 100,
  book: 'draftkings', result: null, profit: null, notes: null, n_books: 5, anchored: true, created_at: '2026-10-14T12:00:00Z',
}

const outlier = (over: Partial<EvBookOutlierRow>): EvBookOutlierRow => ({
  sport: 'nhl', game_key: 'nhl:2026-10-14:MTL@WSH', commence: '2026-10-14T23:00:00Z', mins_to_kick: 120, book: 'fanduel',
  player: 'Alex Ovechkin', player_norm: 'id8471214', player_id: 8471214, stat: 'sog', point: 2.5, side: 'OVER',
  american: 112, decimal: 2.12, p_fair: 0.52, ev: 0.092, n_books: 4, disp: 0.01, flag: true, reason: 'FLAG', ...over,
})

const block = (rows: EvBookOutlierRow[]) => ({
  rows, count: rows.length, status: 'shadow_live' as const, as_of: '2026-10-14T21:00:00Z', age_minutes: 5, stale: false,
  summary: {}, validated: false,
})

const feed: EvFeed = {
  sport: 'all', min_ev: 0.02, as_of: '2026-10-14T21:00:00Z',
  mlb: { rows: [mlb], count: 1 },
  nfl: {
    rows: [], count: 0, status: 'shadow_live', as_of: '2026-10-14T21:00:00Z', age_minutes: 4, stale: false, summary: {}, validated: false,
    book_outliers: [outlier({ sport: 'nfl', game_key: 'nfl:2026-10-18:IND@WAS', player: 'Test Receiver', player_id: null,
      stat: 'rec_yds', point: 63.5, book: 'novig', american: 115, ev: 0.071, n_books: 5 })],
  },
  nhl: block([outlier({})]),
  nba: block([outlier({ sport: 'nba', game_key: 'nba:2026-10-14:LAL@GSW', player: 'Test Guard', player_id: 3975, stat: 'points', point: 24.5, book: 'betmgm', ev: 0.05 })]),
}

describe('EvClient shadow rows', () => {
  it('lists NFL Sink A, NHL and NBA rows next to MLB and enables all five sport filters', () => {
    render(<EvClient initialFeed={feed} headline={null} />)
    for (const s of ['ALL', 'MLB', 'NFL', 'NHL', 'NBA']) {
      expect(screen.getByRole('button', { name: s })).not.toBeDisabled()
    }
    expect(screen.getByText(/Test Receiver Over 63.5 Rec Yds/)).toBeInTheDocument()
    expect(screen.getByText(/Alex Ovechkin Over 2.5 Shots on Goal/)).toBeInTheDocument()
    expect(screen.getByText(/Test Guard Over 24.5 Points/)).toBeInTheDocument()
    expect(screen.getByText(/Over 6\.5/)).toBeInTheDocument()
    expect(screen.getByText(/4 ROWS/)).toBeInTheDocument()
  })

  it('badges consensus rows MARKET with their book count and keeps the Beezy score on the MLB row only', () => {
    render(<EvClient initialFeed={feed} headline={null} />)
    expect(screen.getAllByText('MARKET')).toHaveLength(3)                       // NFL, NHL, NBA
    expect(screen.queryByText('SHADOW')).toBeNull()                             // no warning chip any more
    expect(screen.getByText('5 BK')).toBeInTheDocument()                        // NFL: 5 reference books
    expect(screen.getAllByText('4 BK')).toHaveLength(2)                         // NHL, NBA
    expect(screen.getAllByLabelText(/Beezy score/)).toHaveLength(1)              // MLB model-scored row only
    fireEvent.click(screen.getByRole('button', { name: 'NHL' }))
    expect(screen.queryByLabelText(/Beezy score/)).toBeNull()
  })

  it('sizes consensus rows with 3+ reference books at 1/4 Kelly and shows Thin (<3 bks) below that', () => {
    render(<EvClient initialFeed={feed} headline={null} />)
    expect(screen.getAllByText('1/4 KELLY STAKE')).toHaveLength(1)              // one column header, not a label repeated per row
    expect(screen.queryByText(/none/)).toBeNull()
    expect(screen.queryByText(/Thin/)).toBeNull()                               // all four rows are anchored by 3+ books
    fireEvent.click(screen.getByRole('button', { name: 'NHL' }))
    expect(screen.getByText('$23')).toBeInTheDocument()                         // +112 at 52% fair: 2.3% of the $1,000 default bankroll

    const thin: EvFeed = { ...feed, mlb: { rows: [], count: 0 }, nfl: null, nba: null, nhl: block([outlier({ n_books: 2 })]) }
    render(<EvClient initialFeed={thin} headline={null} />)
    expect(screen.getByText('Thin (<3 bks)')).toBeInTheDocument()
    expect(screen.getAllByText('2 BK')).toHaveLength(1)
  })

  it('puts the segmented filters in recessed groups and keeps the brand green for +EV values only', () => {
    render(<EvClient initialFeed={feed} headline={null} />)
    const all = screen.getByRole('button', { name: 'ALL' })
    expect(all).toHaveAttribute('aria-pressed', 'true')
    expect(all).toHaveStyle({ color: '#ffffff', background: '#1b1f2b' })
    expect(screen.getByRole('button', { name: 'NFL' })).toHaveStyle({ background: 'transparent' })
    expect(all.parentElement).toHaveStyle({ background: '#090a0f', padding: '3px' })
    const bankroll = screen.getByLabelText('BANKROLL')
    expect(bankroll).toHaveStyle({ background: 'transparent', width: '84px', textAlign: 'right' })
    expect(bankroll.parentElement).toHaveStyle({ background: '#090a0f', height: '34px' })
    expect(bankroll.previousElementSibling).toHaveTextContent('$')
  })

  it('renders a real table (one thead, one row per play) and tucks the paper-results caveat behind a Methodology drawer', () => {
    const headline = {
      graded: 100, wins: 55, losses: 40, pushes: 1, voids: 4, pending: 3, unique_props_graded: 60, flat_stake_unit: 100,
      flat_pnl_dollars: 1200, flat_roi_pct: 12, bankroll: 1000, kelly_ending_bankroll: 1300, kelly_roi_pct: 9, kelly_cap: 0.05,
    } as unknown as Parameters<typeof EvClient>[0]['headline']
    const { container } = render(<EvClient initialFeed={feed} headline={headline} />)
    expect(container.querySelectorAll('thead')).toHaveLength(1)
    expect(container.querySelectorAll('tbody tr[data-row]')).toHaveLength(4)
    expect(container.querySelector('article')).toBeNull()
    expect(container.querySelector('.kpi-grid')).toBeNull()
    const drawer = container.querySelector('details') as HTMLDetailsElement
    expect(drawer).not.toBeNull()
    expect(drawer.open).toBe(false)
    expect(drawer.querySelector('summary')).toHaveTextContent('Methodology & Audit Notes')
    expect(drawer).toHaveTextContent(/Paper results only/)
  })

  it('filters to one sport and renders the NHL headshot from the resolved player id', () => {
    const { container } = render(<EvClient initialFeed={feed} headline={null} />)
    fireEvent.click(screen.getByRole('button', { name: 'NHL' }))
    expect(screen.getByText(/Alex Ovechkin/)).toBeInTheDocument()
    expect(screen.queryByText(/Test Guard/)).toBeNull()
    expect(screen.getByText(/The sweep|Sweep is running/, { exact: false })).toBeInTheDocument()
    const img = container.querySelector('img') as HTMLImageElement
    expect(img.src).toBe('https://assets.nhle.com/mugs/nhl/latest/8471214.png')
    fireEvent.click(screen.getByRole('button', { name: 'NBA' }))
    expect(container.querySelector('img')!.src).toContain('headshots/nba/players/full/3975.png')
    fireEvent.click(screen.getByRole('button', { name: 'NFL' }))
    expect(container.querySelector('img')).toBeNull()                            // NFL has no headshots
  })

  it('states the sweep is idle instead of pretending, when the block is stale', () => {
    const idle = { ...feed, nhl: { ...block([outlier({})]), status: 'idle' as const, stale: true, age_minutes: 600 } }
    render(<EvClient initialFeed={idle} headline={null} />)
    fireEvent.click(screen.getByRole('button', { name: 'NHL' }))
    expect(screen.getByRole('note')).toHaveTextContent(/sweep is idle \(last run 10\.0h ago\)/)
  })
})

describe('Compare books drawer', () => {
  const quotes = [
    { book: 'draftkings', over: -130, under: 105, fair: 0.54 },
    { book: 'fanduel', over: 112, under: -135, fair: 0.5 },
    { book: 'betmgm', over: 110, under: null, fair: null },
    { book: 'caesars', over: null, under: -120, fair: 0.512 },
  ]
  const withBooks = (side: string): EvFeed => ({
    ...feed, mlb: { rows: [], count: 0 }, nfl: null, nba: null,
    nhl: block([outlier({ side, all_books: quotes })]),
  })

  it('toggles the book strip behind Compare N books, best OVER price first and flagged, shadow rule intact', () => {
    render(<EvClient initialFeed={withBooks('OVER')} headline={null} />)
    const btn = screen.getByRole('button', { name: /Compare 4 books/ })
    expect(btn).toHaveAttribute('aria-expanded', 'false')
    expect(btn).toHaveTextContent('Compare 4 books')
    expect(screen.queryByRole('group', { name: /All books/ })).toBeNull()      // collapsed hides the strip
    expect(screen.queryByText('DraftKings', { selector: 'span' })).toBeNull()   // the venue <option> also says DraftKings

    fireEvent.click(btn)
    expect(btn).toHaveAttribute('aria-expanded', 'true')
    const strip = screen.getByRole('group', { name: /All books/ })
    for (const name of ['DraftKings', 'FanDuel', 'BetMGM', 'Caesars']) expect(within(strip).getByText(name)).toBeInTheDocument()
    expect(within(strip).getByText('+112')).toBeInTheDocument()
    expect(within(strip).getByText('-130')).toBeInTheDocument()
    expect(within(strip).getByText('fair 54.0%')).toBeInTheDocument()
    const cards = Array.from(strip.querySelectorAll('[data-best]')) as HTMLElement[]
    expect(cards).toHaveLength(1)                                              // only the best OVER price is flagged
    expect(cards[0]).toHaveTextContent('FanDuel')
    expect(cards[0]).toHaveTextContent('BEST')
    const order = Array.from(strip.children[1].children).map(c => c.textContent ?? '')
    expect(order.map(t => ['FanDuel', 'BetMGM', 'DraftKings', 'Caesars'].findIndex(n => t.includes(n)))).toEqual([0, 1, 2, 3]) // +112, +110, -130, no over price
    expect(strip.textContent).toContain('\u2013')                               // null prices render an en dash
    expect(screen.queryByLabelText(/Beezy score/)).toBeNull()                  // consensus row: no 0-100 score
    expect(screen.getByText('MARKET')).toBeInTheDocument()
    expect(screen.getByText('4 BK')).toBeInTheDocument()

    fireEvent.click(btn)
    expect(btn).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('group', { name: /All books/ })).toBeNull()
  })

  it('ranks by the UNDER price when the row targets the under, and hides the button for a single book', () => {
    const { unmount } = render(<EvClient initialFeed={withBooks('UNDER')} headline={null} />)
    fireEvent.click(screen.getByRole('button', { name: /Compare 4 books/ }))
    const best = screen.getByRole('group', { name: /All books/ }).querySelector('[data-best]') as HTMLElement
    expect(best).toHaveTextContent('DraftKings')                               // under +105 beats -120 / -135
    unmount()
    const one: EvFeed = { ...withBooks('OVER'), nhl: block([outlier({ all_books: [quotes[0]] })]) }
    render(<EvClient initialFeed={one} headline={null} />)
    expect(screen.queryByRole('button', { name: /Compare/ })).toBeNull()
  })
})

describe('source files with typographic characters stay ASCII', () => {
  const root = path.resolve(__dirname, '..')
  const files = [
    'lib/sport-assets.ts', 'lib/sports.ts', 'lib/types.ts', 'components/ui/sport-assets.tsx',
    'app/ev/ev-client.tsx', 'app/sports/[sport]/sport-hub-client.tsx', 'app/sports/[sport]/page.tsx',
    'app/api/og/route.tsx', 'app/api/og/ev-recap-card/route.tsx', 'app/api/og/games-card/route.tsx', 'app/api/og/picks-card/route.tsx', 'app/api/og/props-card/route.tsx',
    'app/edge/page.tsx', 'app/edge/edge-client.tsx', 'app/edge/cockpit.tsx', 'app/api/edge-cockpit/route.ts',
    'components/layout/nav.tsx', 'components/landing/hero.tsx', 'lib/betting-api.ts', 'app/analytics/analytics-client.tsx', 'lib/beezy-score.ts',
    'tests/sport-assets.test.ts', 'tests/tokens.test.ts', 'tests/ev-terminal-shadow.test.tsx', 'tests/edge-cockpit.test.tsx',
  ]
  it.each(files)('%s', (f) => {
    const text = fs.readFileSync(path.join(root, f), 'utf8')
    const bad = [...text].filter(c => c.charCodeAt(0) > 127)
    expect(bad).toEqual([])
  })
  it('fold() strips combining marks via an escaped range', () => {
    const src = fs.readFileSync(path.join(root, 'lib/sport-assets.ts'), 'utf8')
    expect(src).toContain('.replace(/[\\u0300-\\u036f]/g, \'\')')
  })
})
