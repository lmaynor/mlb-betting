import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { buildMlbProjections, parseSideLine, toProjectionRow } from '../lib/mlb-projections'
import type { Bet } from '../lib/types'

jest.mock('next/link', () => ({
  __esModule: true,
  default: ({ children, href, 'aria-current': current }: { children: React.ReactNode; href: string; 'aria-current'?: 'page' }) => <a href={href} aria-current={current}>{children}</a>,
}))

import { SportProjectionsBoard } from '../components/picks/sport-projections-board'
import { SportTabs } from '../components/picks/sport-tabs'
import { fromMlbProjections } from '../lib/board-rows'

let id = 0
const bet = (o: Partial<Bet>): Bet => ({
  id: ++id, system: 'K', game_date: '2026-10-16', game_pk: 1, bet_type: 'K_OVER_5.5', player: 'Gerrit Cole', away_team: 'NYY', home_team: 'BOS',
  odds: 110, stake: 25, model_prob: 0.6, market_prob: 0.5, edge: 0.06, kelly_pct: 0.01, kelly_triggered: true, result: null, profit: null,
  paper: true, book: 'fanduel', notes: null, created_at: '', proj_mean: 6.4, ...o,
})

describe('MLB projections', () => {
  it('parses the side and line, and treats the one-sided 2+/3+ thresholds as alt lines', () => {
    expect(parseSideLine('K_OVER_5.5')).toEqual({ side: 'OVER', line: 5.5 })
    expect(parseSideLine('BATTER_TB_UNDER_1.5')).toEqual({ side: 'UNDER', line: 1.5 })
    expect(parseSideLine('K_2PLUS_2.0')).toBeNull()
    expect(parseSideLine('HR')).toBeNull()
    expect(parseSideLine(null)).toBeNull()
  })

  it('only count markets with a stored projection are on the board; HR, game markets, +EV rows and projection-less rows are not', () => {
    const rows = buildMlbProjections([
      bet({}),
      bet({ system: 'HR', bet_type: 'HR', proj_mean: null, player: 'Judge' }),
      bet({ system: 'NRFI', bet_type: 'NRFI', player: null, proj_mean: null }),
      bet({ system: 'EV', bet_type: 'K_OVER_5.5_fanduel', player: 'Ev Guy', proj_mean: 5 }),
      bet({ system: 'BATTER_TB', bet_type: 'BATTER_TB_OVER_1.5', player: 'No Proj', proj_mean: null }),
      bet({ system: 'K', bet_type: 'K_2PLUS_2.0', player: 'Alt Line', proj_mean: 6 }),
    ])
    expect(rows.map(r => r.player)).toEqual(['Gerrit Cole'])
  })

  it('one row per player and market: a tracked pick beats an unstaked prediction, then the higher score', () => {
    const rows = buildMlbProjections([
      bet({ bet_type: 'K_UNDER_6.5', kelly_triggered: false, edge: 0.2, odds: -120 }),
      bet({ bet_type: 'K_OVER_5.5', kelly_triggered: true, edge: 0.05 }),
    ])
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ side: 'OVER', line: 5.5, isPick: true })
  })

  it('an unstaked row with no edge scores zero, and ranking is by score then edge', () => {
    const none = toProjectionRow(bet({ kelly_triggered: false, edge: -0.03 }))!
    expect(none.score).toBe(0)
    const rows = buildMlbProjections([
      bet({ player: 'Low', game_pk: 1, edge: 0.03 }),
      bet({ player: 'High', game_pk: 2, edge: 0.1 }),
    ])
    expect(rows.map(r => r.player)).toEqual(['High', 'Low'])
  })

  it('normalises percent-stored edge and probability', () => {
    const r = toProjectionRow(bet({ edge: 6, model_prob: 60 }))!
    expect(r.edge).toBeCloseTo(0.06)
    expect(r.pModel).toBeCloseTo(0.6)
  })
})

describe('MLB projections on the unified board', () => {
  const rows = buildMlbProjections([
    bet({}),
    bet({ system: 'BATTER_TB', bet_type: 'BATTER_TB_UNDER_1.5', player: 'Mookie Betts', game_pk: 2, proj_mean: 1.31, odds: -105, edge: 0.04, kelly_triggered: false }),
  ])
  const board = (r = rows) => <SportProjectionsBoard sport="mlb" rows={fromMlbProjections(r)} />

  it('shows the projection beside the line, the preferred side, the price, the edge and a PICK marker', () => {
    render(board())
    const cole = within(screen.getByText('Gerrit Cole').closest('tr')!)
    expect(cole.getByText('Over 5.5')).toBeInTheDocument()
    expect(cole.getByText('6.4')).toBeInTheDocument()
    expect(cole.getByText('60%')).toBeInTheDocument()
    expect(cole.getByText('+110')).toBeInTheDocument()
    expect(cole.getByText('+6.0%')).toBeInTheDocument()
    expect(cole.getByText('PICK')).toBeInTheDocument()
    // the market's fair probability is the model's minus the edge: 60% - 6% = 54%
    expect(cole.getByText(/54%/)).toBeInTheDocument()
    const betts = within(screen.getByText('Mookie Betts').closest('tr')!)
    expect(betts.getByText('Under 1.5')).toBeInTheDocument()
    expect(betts.getByText('1.31')).toBeInTheDocument()
    expect(betts.queryByText('PICK')).toBeNull()
    expect(document.querySelectorAll('thead').length).toBe(1)
  })

  it('has no UNVALIDATED badge (MLB is the tracked card) and no hit-rate columns to fill: dashes, not numbers', () => {
    render(board())
    expect(screen.queryByText('UNVALIDATED')).toBeNull()
    const cole = within(screen.getByText('Gerrit Cole').closest('tr')!)
    expect(cole.queryByText(/L5:/)).toBeNull()
    expect(cole.getAllByText('--').length).toBeGreaterThan(0)
  })

  it('market chips are the MLB systems and filter the board; an empty board is explained', () => {
    const { rerender } = render(board())
    fireEvent.click(screen.getByRole('button', { name: 'TB' }))
    expect(screen.queryByText('Gerrit Cole')).toBeNull()
    expect(screen.getByText('Mookie Betts')).toBeInTheDocument()
    rerender(<SportProjectionsBoard sport="mlb" rows={[]} emptyBody="Today's projections publish with the ~11:00 AM CT model run." />)
    expect(screen.getByText(/publish with the ~11:00 AM CT model run/)).toBeInTheDocument()
  })

  it('sport tabs mark the current view and link the three views of MLB', () => {
    render(<SportTabs sport="mlb" active="projections" />)
    expect(screen.getByRole('link', { name: 'PROJECTIONS' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'PICKS & +EV' })).not.toHaveAttribute('aria-current')
    expect(screen.getByRole('link', { name: 'PICKS & +EV' })).toHaveAttribute('href', '/picks/mlb')
  })
})
