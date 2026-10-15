/**
 * +EV terminal: NFL Sink A, NHL and NBA rows are SHADOW (unvalidated). They must render without a Beezy score or a Kelly
 * stake, the NHL/NBA sport filters must be live, and the source files that carry typographic characters must stay ASCII
 * (the repo is edited from Windows PowerShell / cp1252 shells that turn raw multibyte characters into mojibake).
 */
import fs from 'fs'
import path from 'path'
import '@testing-library/jest-dom'
import { fireEvent, render, screen } from '@testing-library/react'
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

  it('never shows a Beezy score or a Kelly stake on shadow rows (only the MLB tracked row has them)', () => {
    render(<EvClient initialFeed={feed} headline={null} />)
    expect(screen.getAllByText('SHADOW')).toHaveLength(3)                       // NFL, NHL, NBA
    expect(screen.getAllByLabelText(/Beezy score/)).toHaveLength(1)              // MLB only
    expect(screen.getAllByText(/none \u2014 unvalidated/)).toHaveLength(3)
    expect(screen.getAllByText('1/4 KELLY STAKE')).toHaveLength(1)
    expect(screen.getAllByText('STAKE')).toHaveLength(3)
    fireEvent.click(screen.getByRole('button', { name: 'NHL' }))
    expect(screen.queryByLabelText(/Beezy score/)).toBeNull()
    expect(screen.queryByText(/\$\d/)).toBeNull()                                // no dollar stake anywhere
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

describe('source files with typographic characters stay ASCII', () => {
  const root = path.resolve(__dirname, '..')
  const files = [
    'lib/sport-assets.ts', 'lib/sports.ts', 'lib/types.ts', 'components/ui/sport-assets.tsx',
    'app/ev/ev-client.tsx', 'app/sports/[sport]/sport-hub-client.tsx', 'app/sports/[sport]/page.tsx',
    'app/api/og/route.tsx', 'app/api/og/games-card/route.tsx', 'app/api/og/picks-card/route.tsx', 'app/api/og/props-card/route.tsx',
    'tests/sport-assets.test.ts', 'tests/tokens.test.ts', 'tests/ev-terminal-shadow.test.tsx',
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
