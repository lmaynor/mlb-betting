/** The NBA games board: market beside our line, one real table, ladder sub-row, no edge/EV language. Pure render tests. */
import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'
import fs from 'fs'
import path from 'path'
import type { NbaGame, NbaGamesBoard } from '../lib/nba-games'
import { americanText, lineText, pctText, signedPts, tipText } from '../lib/nba-games'

jest.mock('next/link', () => ({ __esModule: true, default: ({ children, href, 'aria-current': current }: { children: React.ReactNode; href: string; 'aria-current'?: 'page' }) => <a href={href} aria-current={current}>{children}</a> }))

import { NbaGamesTable } from '../components/picks/nba-games-board'
import { SportTabs } from '../components/picks/sport-tabs'

const ladder = (center: number, kind: 'spread' | 'total') => [-1, 0, 1].map(s => ({ line: center + s, p: kind === 'spread' ? 0.5 + s * 0.03 : 0.5 - s * 0.03, fair_american: s === 0 ? -100 : s > 0 ? -115 : 105 }))

const game = (o: Partial<NbaGame> = {}): NbaGame => ({
  game_key: '2026-11-20_BOS_DET', date: '2026-11-20', start: '2026-11-21T01:00:00Z', home: 'DET', away: 'BOS',
  home_name: 'Detroit Pistons', away_name: 'Boston Celtics', n_books: 9, games_min: 16, reliable: true,
  market: { spread_home: 1.5, total: 222.5, home_win_p: 0.49, n_books_spread: 7, n_books_total: 7, pinnacle_home_win_p: 0.5 },
  model: { margin: 2.1, spread_home: -2.1, total: 224.1, home_win_p: 0.56, sigma_margin: 13.6, sigma_total: 18.7 },
  diff: { margin_pts: 3.6, total_pts: 1.6, home_win_pp: 7.0 },
  ladders: { spread_home: ladder(1.5, 'spread'), total: ladder(222.5, 'total') }, ...o,
})

const board = (games: NbaGame[], o: Partial<NbaGamesBoard> = {}): NbaGamesBoard => ({
  sport: 'nba', engine: 'nba_games_ridge_v1', validated: false, status: games.length ? 'ok' : 'no_games', stale: false,
  as_of: '2026-11-20T17:50:00Z', lines_as_of: '2026-11-20T17:35:00Z', age_minutes: 5, lines_age_minutes: 20, count: games.length,
  reliable_count: games.filter(g => g.reliable).length, label: 'Model lines vs market. Informational.', accuracy: null, games, ...o,
})

describe('formatting helpers', () => {
  it('signs, lines and percentages', () => {
    expect(signedPts(3.64)).toBe('+3.6'); expect(signedPts(-2)).toBe('-2.0'); expect(signedPts(null)).toBe('--')
    expect(lineText(-3.5)).toBe('-3.5'); expect(lineText(2.5)).toBe('+2.5'); expect(lineText(null)).toBe('--')
    expect(pctText(0.494)).toBe('49%'); expect(americanText(150)).toBe('+150'); expect(americanText(-120)).toBe('-120')
  })
  it('tip time is on the Central clock', () => {
    expect(tipText('2026-11-21T01:00:00Z')).toMatch(/Fri 7:00 PM/)
    expect(tipText(null)).toBe('--'); expect(tipText('garbage')).toBe('--')
  })
})

describe('NbaGamesTable', () => {
  it('is ONE real table with one head row; market beside ours; unreliable rows are marked', () => {
    const { container } = render(<NbaGamesTable board={board([game(), game({ game_key: 'b', home: 'SAS', away: 'OKC', reliable: false, games_min: 2 })])} />)
    expect(container.querySelectorAll('table')).toHaveLength(1)
    expect(container.querySelectorAll('thead tr')).toHaveLength(1)
    const rows = within(container.querySelector('tbody') as HTMLElement).getAllByRole('row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('BOS @ DET')).toBeInTheDocument()
    expect(within(rows[0]).getByText('+3.6')).toBeInTheDocument()
    expect(within(rows[0]).getByText('Ready')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Warming up')).toBeInTheDocument()
  })

  it('opens the alt-line ladder as a colSpan sub-row and says there is no price to compare', () => {
    const { container } = render(<NbaGamesTable board={board([game()])} />)
    fireEvent.click(screen.getByText('BOS @ DET'))
    const sub = container.querySelector('tbody tr:nth-child(2) td') as HTMLElement
    expect(sub).toHaveAttribute('colspan', '12')
    expect(within(sub).getByText(/FAIR DET SPREAD LADDER/)).toBeInTheDocument()
    expect(within(sub).getByText(/do not have alternate-line prices/i)).toBeInTheDocument()
    fireEvent.click(screen.getByText('BOS @ DET'))
    expect(container.querySelectorAll('tbody tr')).toHaveLength(1)
  })

  it('empty states differ for unavailable and no-games', () => {
    const { rerender } = render(<NbaGamesTable board={board([], { status: 'unavailable' })} />)
    expect(screen.getByText(/has not been produced yet/)).toBeInTheDocument()
    rerender(<NbaGamesTable board={board([])} />)
    expect(screen.getByText(/at least two books/)).toBeInTheDocument()
  })

  it('never uses edge / EV / pick language', () => {
    const { container } = render(<NbaGamesTable board={board([game()])} />)
    fireEvent.click(screen.getByText('BOS @ DET'))
    const text = (container.textContent ?? '').toLowerCase()
    for (const w of ['edge', '+ev', 'value bet', 'recommend', 'lock']) expect(text).not.toContain(w)
  })
})

describe('GAMES tab', () => {
  it('shows only on NBA', () => {
    const nba = render(<SportTabs sport="nba" active="games" />)
    expect(nba.getByText('GAMES')).toHaveAttribute('href', '/picks/nba/games')
    expect(nba.getByText('GAMES')).toHaveAttribute('aria-current', 'page')
    nba.unmount()
    render(<SportTabs sport="nfl" active="picks" />)
    expect(screen.queryByText('GAMES')).toBeNull()
  })
})

describe('source hygiene', () => {
  const files = ['lib/nba-games.ts', 'components/picks/nba-games-board.tsx', 'app/picks/[sport]/games/page.tsx', 'app/api/nba-games/route.ts']
  it('is ASCII, has no gradients/glows/transforms and the games route 404s every other sport', () => {
    for (const f of files) {
      const src = fs.readFileSync(path.join(__dirname, '..', f), 'utf8')
      expect(/[^\x00-\x7f]/.test(src)).toBe(false)
      expect(src).not.toMatch(/gradient|box-shadow|translate|scale\(/)
    }
    const page = fs.readFileSync(path.join(__dirname, '..', 'app/picks/[sport]/games/page.tsx'), 'utf8')
    expect(page).toMatch(/sport !== 'nba'\) notFound\(\)/)
  })
})
