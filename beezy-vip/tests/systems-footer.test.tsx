import '@testing-library/jest-dom'
import { render, screen, within } from '@testing-library/react'
import type { SystemStats } from '../lib/types'

jest.mock('next/link', () => ({ __esModule: true, default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }))

import { SystemsFooter } from '../components/picks/systems-footer'

const stat = (system: string, total_bets: number, roi: number): SystemStats =>
  ({ system, total_bets, wins: 0, losses: 0, pushes: 0, win_rate: 50, roi, total_pnl: 0, avg_edge: 5 })

describe('systems footer', () => {
  const stats = [stat('HR', 133, 3.2), stat('K', 250, -4.1), stat('NRFI', 90, 1.0), stat('F5', 40, 2.0)]

  it('lists only the systems of its own kind, each with its record', () => {
    render(<SystemsFooter kind="player" stats={stats} />)
    expect(screen.getByRole('heading', { name: 'Player systems' })).toBeInTheDocument()
    const hr = within(screen.getByText('Home Run Props').closest('tr')!)
    expect(hr.getByText('133')).toBeInTheDocument()
    expect(hr.getByText('+3.2%')).toBeInTheDocument()
    expect(hr.getByText('133/200 bets')).toBeInTheDocument()
    expect(within(screen.getByText('Pitcher Strikeouts').closest('tr')!).getByText('200+ bets')).toBeInTheDocument()
    expect(screen.queryByText('No Run First Inning')).toBeNull()
    expect(screen.queryByText('First 5 Innings')).toBeNull()
  })

  it('the game footer lists the game systems and not the player ones', () => {
    render(<SystemsFooter kind="game" stats={stats} />)
    expect(screen.getByRole('heading', { name: 'Game systems' })).toBeInTheDocument()
    expect(screen.getByText('No Run First Inning')).toBeInTheDocument()
    expect(screen.getByText('First 5 Innings')).toBeInTheDocument()
    expect(screen.queryByText('Home Run Props')).toBeNull()
  })

  it('links each system to its detail page, is anchored as #systems, and has one sticky thead', () => {
    const { container } = render(<SystemsFooter kind="player" stats={stats} />)
    expect(container.querySelector('#systems')).not.toBeNull()
    expect(screen.getByText('Home Run Props').closest('a')).toHaveAttribute('href', '/picks/mlb/hr')
    expect(container.querySelectorAll('thead').length).toBe(1)
  })

  it('a shelved system with no live market and no bets is not listed', () => {
    render(<SystemsFooter kind="game" stats={stats} />)
    expect(screen.queryByText('First 3 Innings')).toBeNull()
    expect(screen.queryByText('First 7 Innings')).toBeNull()
  })
})
