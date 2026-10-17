import '@testing-library/jest-dom'
import { render, screen, within } from '@testing-library/react'
import type { SystemStats } from '../lib/types'

jest.mock('next/link', () => ({ __esModule: true, default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }))

import { SystemsFooter } from '../components/picks/systems-footer'
import { getSystemsBySport } from '../lib/pick-systems'
import { SPORT_SLUGS } from '../lib/sports'

const stat = (system: string, total_bets: number, roi: number): SystemStats =>
  ({ system, total_bets, wins: 0, losses: 0, pushes: 0, win_rate: 50, roi, total_pnl: 0, avg_edge: 5 })

describe('systems footer (MLB, tracked)', () => {
  const stats = [stat('HR', 133, 3.2), stat('K', 250, -4.1), stat('NRFI', 90, 1.0), stat('F5', 40, 2.0)]

  it('with a kind, lists only the systems of that kind, each with its record', () => {
    render(<SystemsFooter sport="mlb" kind="player" stats={stats} />)
    expect(screen.getByRole('heading', { name: 'Player systems' })).toBeInTheDocument()
    const hr = within(screen.getByText('Home Run Props').closest('tr')!)
    expect(hr.getByText('133')).toBeInTheDocument()
    expect(hr.getByText('+3.2%')).toBeInTheDocument()
    expect(hr.getByText('133/200 bets')).toBeInTheDocument()
    expect(hr.getByText('Prop')).toBeInTheDocument()
    expect(within(screen.getByText('Pitcher Strikeouts').closest('tr')!).getByText('200+ bets')).toBeInTheDocument()
    expect(screen.queryByText('No Run First Inning')).toBeNull()
    expect(screen.queryByText('First 5 Innings')).toBeNull()
  })

  it('the game footer lists the game systems and not the player ones', () => {
    render(<SystemsFooter sport="mlb" kind="game" stats={stats} />)
    expect(screen.getByRole('heading', { name: 'Game systems' })).toBeInTheDocument()
    expect(within(screen.getByText('No Run First Inning').closest('tr')!).getByText('Game')).toBeInTheDocument()
    expect(screen.getByText('First 5 Innings')).toBeInTheDocument()
    expect(screen.queryByText('Home Run Props')).toBeNull()
  })

  it('with no kind it lists every MLB system once, under the sport heading', () => {
    render(<SystemsFooter sport="mlb" stats={stats} />)
    expect(screen.getByRole('heading', { name: 'MLB systems' })).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'View System ->' })).toHaveLength(getSystemsBySport('mlb').length)
  })

  it('links each system to its detail page, is anchored as #systems, and has one sticky thead', () => {
    const { container } = render(<SystemsFooter sport="mlb" kind="player" stats={stats} />)
    expect(container.querySelector('#systems')).not.toBeNull()
    expect(screen.getByText('Home Run Props').closest('a')).toHaveAttribute('href', '/picks/mlb/hr')
    expect(container.querySelectorAll('thead').length).toBe(1)
  })

  it('a shelved system with no live market and no bets is not listed; one that has bets still is', () => {
    const { unmount } = render(<SystemsFooter sport="mlb" stats={stats} />)
    expect(screen.queryByText('First 3 Innings')).toBeNull()
    expect(screen.queryByText('First 7 Innings')).toBeNull()
    expect(screen.queryByText('First Half')).toBeNull()
    unmount()
    render(<SystemsFooter sport="mlb" stats={[...stats, stat('F1H', 12, 0.5)]} />)
    expect(screen.getByText('First Half')).toBeInTheDocument()
  })
})

describe('systems footer (projection sports)', () => {
  it.each(['nfl', 'nhl', 'nba'] as const)('%s lists every registered system with a link, a distribution and study numbers or dashes', sport => {
    const { container } = render(<SystemsFooter sport={sport} />)
    const systems = getSystemsBySport(sport)
    expect(screen.getByRole('heading', { name: new RegExp(`${sport} systems`, 'i') })).toBeInTheDocument()
    expect(container.querySelectorAll('tbody tr')).toHaveLength(systems.length)
    for (const s of systems) {
      const href = `/picks/${sport}/${s.slug}`
      const tr = container.querySelector(`a[href="${href}"]`)!.closest('tr')!
      const row = within(tr)
      expect(row.getByText('Prop')).toBeInTheDocument()
      expect(row.getByText(s.family)).toBeInTheDocument()
      expect(row.getAllByRole('link').some(a => a.textContent === s.name && a.getAttribute('href') === href)).toBe(true)
      expect(row.getByRole('link', { name: 'View System ->' })).toHaveAttribute('href', href)
    }
    expect(screen.queryByText(/not tracked yet/i)).toBeNull()
    expect(screen.getByText(/empirical estimator/i)).toBeInTheDocument()
  })

  it('NBA points shows the study MAE, Brier and blend c; a stat no study covered shows dashes, never a number', () => {
    render(<SystemsFooter sport="nba" />)
    const pts = within(screen.getByText('Player Points').closest('tr')!)
    expect(pts.getByText('4.547 / 4.634')).toBeInTheDocument()
    expect(pts.getByText('0.2424')).toBeInTheDocument()
    expect(pts.getByText('+0.07 (+3.0)')).toBeInTheDocument()
    const steals = within(screen.getByText('Player Steals').closest('tr')!)
    expect(steals.getAllByText('--').length).toBeGreaterThanOrEqual(3)
  })

  it('the NHL power-play-points row is marked as a data gap, not given numbers', () => {
    render(<SystemsFooter sport="nhl" />)
    const pp = within(screen.getByText('Power Play Points').closest('tr')!)
    expect(pp.getByText('Data gap')).toBeInTheDocument()
    expect(pp.getByText('Not projected (data gap)')).toBeInTheDocument()
  })

  it('every sport renders the same footer structure', () => {
    const shape = (sport: (typeof SPORT_SLUGS)[number]) => {
      const { container, unmount } = render(<SystemsFooter sport={sport} />)
      const out = { id: !!container.querySelector('#systems'), thead: container.querySelectorAll('thead').length, links: container.querySelectorAll('tbody a[href*="/picks/"]').length > 0 }
      unmount()
      return out
    }
    for (const s of SPORT_SLUGS) expect(shape(s)).toEqual({ id: true, thead: 1, links: true })
  })
})
